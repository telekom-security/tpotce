"""Frozen history inventories and private, portable snapshot repositories."""

import hashlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import platform
import shlex
import shutil
import stat
import tarfile
import tempfile
import time
from typing import Callable, Optional, Tuple
import urllib.parse
import uuid

from tpotctl import kibana_objects, migration, say, snapshots
from tpotctl import migration_runtime as runtime
from tpotctl.migration import MigrationError
from tpotctl.migration_runtime import DockerRuntime

MAX_HISTORY_METADATA = 128 * 1024 * 1024
# Kibana exports through a search, which sees a write only after its index refreshes (1 s by default)
KIBANA_INVENTORY_READS = 10
KIBANA_INVENTORY_DELAY = 1.5


def _history_time(value) -> Optional[datetime]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MigrationError("history time bounds must be timestamps or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        return parsed
    except ValueError as err:
        raise MigrationError("history inventory has an invalid timestamp") from err


def _validate_history(value: dict, source: dict) -> dict:
    required = {"format", "schema", "run", "elasticsearch_version", "kibana_version",
                "snapshot", "indices", "inventory", "kibana", "ignored_indices"}
    if not isinstance(value, dict) or set(value) != required \
            or value["format"] != "tpot-migration-history" or type(value["schema"]) is not int \
            or value["schema"] != 2 or value["snapshot"] != "original" \
            or not isinstance(value["run"], str) or not re.fullmatch("[0-9a-f]{32}", value["run"]):
        raise MigrationError("invalid history metadata format")
    if value["elasticsearch_version"] != source["elasticsearch_version"] \
            or value["kibana_version"] != source["kibana_version"]:
        raise MigrationError("history versions do not match the source package")
    snapshots._version(value["elasticsearch_version"])
    indices = snapshots._indices(value["indices"])
    if any(not (name.startswith(("logstash-", "tpot-sensor-heartbeat-")) or name == "tpot-sensor-status")
           for name in indices):
        raise MigrationError("history inventory contains an unselected index")
    inventory = value["inventory"]
    if not isinstance(inventory, dict) or set(inventory) != set(indices):
        raise MigrationError("history index selection does not match its inventory")
    for entry in inventory.values():
        if not isinstance(entry, dict) or set(entry) != {"created", "count", "first", "last", "aliases", "samples"} \
                or not isinstance(entry["created"], str) or not re.fullmatch("[0-9]{7}", entry["created"]) \
                or type(entry["count"]) is not int or entry["count"] < 0 or not isinstance(entry["aliases"], dict):
            raise MigrationError("invalid history index inventory")
        first, last = _history_time(entry["first"]), _history_time(entry["last"])
        if (first is None) != (last is None) or first is not None and last is not None and first > last:
            raise MigrationError("history time bounds are inconsistent")
        samples = entry["samples"]
        if not isinstance(samples, list) or len(samples) > min(3, entry["count"]) \
                or bool(samples) != bool(entry["count"]):
            raise MigrationError("history inventory has missing or invalid document samples")
        identifiers = set()
        for sample in samples:
            if not isinstance(sample, dict) or set(sample) != {"id", "source"} \
                    or not isinstance(sample["id"], str) or not sample["id"] \
                    or sample["id"] in identifiers or not isinstance(sample["source"], dict):
                raise MigrationError("history inventory has invalid or duplicate document samples")
            identifiers.add(sample["id"])
    kibana = value["kibana"]
    if not isinstance(kibana, dict) or bool(kibana) != (value["kibana_version"] is not None):
        raise MigrationError("history Kibana inventory does not match its source")
    if kibana:
        if value["kibana_version"] != value["elasticsearch_version"]:
            raise MigrationError("history requires matching source Elasticsearch and Kibana versions")
        for identifier, space in kibana.items():
            if not identifier or not isinstance(space, dict) or set(space) != {"space", "objects"} \
                    or not isinstance(space["space"], dict) or space["space"].get("id") != identifier \
                    or not isinstance(space["objects"], dict):
                raise MigrationError("invalid history Kibana space inventory")
            if "config:" + value["kibana_version"] not in space["objects"]:
                raise MigrationError("history inventory is missing active Kibana settings")
            for key, obj in space["objects"].items():
                if not isinstance(obj, dict) or not isinstance(obj.get("id"), str) or not obj["id"] \
                        or not isinstance(obj.get("type"), str) or not obj["type"] \
                        or kibana_objects.key(obj) != key or not isinstance(obj.get("attributes"), dict) \
                        or not isinstance(obj.get("references"), list):
                    raise MigrationError("invalid history Kibana object inventory")
                for ref in obj["references"]:
                    if not isinstance(ref, dict) or any(not isinstance(ref.get(field), str)
                                                       for field in ("id", "type", "name")):
                        raise MigrationError("invalid history Kibana object reference")
    ignored = value["ignored_indices"]
    if not isinstance(ignored, list) or any(not isinstance(name, str) or not name for name in ignored) \
            or len(set(ignored)) != len(ignored) or set(ignored).intersection(indices):
        raise MigrationError("invalid history excluded-index inventory")
    return value


def _read_history_metadata(stream, source: dict) -> dict:
    raw = stream.read(MAX_HISTORY_METADATA + 1)
    if len(raw) > MAX_HISTORY_METADATA:
        raise MigrationError("history inventory exceeds the supported metadata size")
    try:
        value = json.loads(raw, object_pairs_hook=migration._unique_pairs)
        json.dumps(value, allow_nan=False)
        return _validate_history(value, source)
    except (ValueError, snapshots.SnapshotError, RecursionError) as err:
        raise MigrationError(f"invalid history metadata: {err}") from err


def read_history(manifest: dict, package_path: str) -> Tuple[dict, int]:
    """Validate both history layers without extracting repository or document data."""
    if migration.validate_package(package_path) != manifest:
        raise MigrationError("history package does not match its manifest")
    files = {item["path"] for item in manifest["files"] if item["path"].startswith("history/")}
    if "history" not in manifest["scope"] or files != {"history/inventory.json", "history/repository.tar"}:
        raise MigrationError("history package must contain its inventory and snapshot repository")
    try:
        with tarfile.open(package_path, "r:") as outer:
            member = outer.getmember("payload/history/inventory.json")
            if member.size > MAX_HISTORY_METADATA:
                raise MigrationError("history inventory exceeds the supported metadata size")
            with outer.extractfile(member) as stream:
                metadata = _read_history_metadata(stream, manifest["source"])
            size = 0
            with outer.extractfile("payload/history/repository.tar") as stream, \
                    tarfile.open(fileobj=stream, mode="r|") as repository:
                for entry in _repository_members(repository):
                    size += entry.size
                    with repository.extractfile(entry) as data:
                        while data.read(1024 * 1024):
                            pass
        return metadata, size
    except (OSError, ValueError, KeyError, tarfile.TarError, snapshots.SnapshotError, RecursionError) as err:
        raise MigrationError(f"invalid history package: {err}") from err


def _target_versions(target_dir: str, kibana: bool) -> Tuple[str, str]:
    versions = {}
    for service, key in (("elasticsearch", "ES_VER"), ("kibana", "KB_VER")):
        if service == "kibana" and not kibana:
            continue
        text = migration._read_source_file(os.path.join(target_dir, "docker", "elk", service, "Dockerfile"),
                                          "target Elastic version")
        values = re.findall(rf"(?m)^ARG {key}=([0-9]+\.[0-9]+\.[0-9]+)[ \t]*$", text)
        if len(values) != 1:
            raise MigrationError("target image does not declare one exact Elastic version")
        versions[service] = values[0]
    if kibana and versions["kibana"] != versions["elasticsearch"]:
        raise MigrationError("target Elasticsearch and Kibana versions do not match")
    text = migration._read_source_file(os.path.join(target_dir, "update.sh"), "target intermediate version")
    values = re.findall(r'(?m)^myES_STEP_VERSION="([0-9]+\.[0-9]+\.[0-9]+)"[ \t]*$', text)
    if len(values) != 1:
        raise MigrationError("target updater does not declare one pinned intermediate version")
    return versions["elasticsearch"], values[0]


def _existing_directory(path: str) -> str:
    while not os.path.exists(path):
        path = os.path.dirname(path)
    if not os.path.isdir(path):
        raise MigrationError("history storage path is not a directory")
    return path


_RESERVE = 512 * 1024 * 1024


def _gib(value: int) -> str:
    return f"{value / 1024 ** 3:.1f} GiB"


def _require_space(state_folder: str, data_folder: str, state_bytes: int, data_bytes: int, what: str) -> None:
    """One reserve where staging and data share a file system, one each where they do not."""
    if os.stat(state_folder).st_dev == os.stat(data_folder).st_dev:
        needs = ((state_folder, state_bytes + data_bytes + _RESERVE),)
    else:
        needs = ((state_folder, state_bytes + _RESERVE), (data_folder, data_bytes + _RESERVE))
    for folder, needed in needs:
        free = shutil.disk_usage(folder).free
        if free < needed:
            raise MigrationError(f"not enough free space {what}: {folder} needs {_gib(needed)}, "
                                 f"{_gib(free)} are free")


def prepare_history(target_dir: str, state_dir: str, manifest: dict, package_path: str,
                    *, env_path: Optional[str] = None) -> dict:
    from tpotctl.envfile import EnvFile
    from tpotctl.migration_import import _target_data_path

    metadata, size = read_history(manifest, package_path)
    target, intermediate = _target_versions(target_dir, bool(metadata["kibana"]))
    try:
        route = snapshots.restore_route(metadata["elasticsearch_version"], target,
            [entry["created"] for entry in metadata["inventory"].values()],
            kibana=bool(metadata["kibana"]), intermediate=intermediate)
    except snapshots.SnapshotError as err:
        raise MigrationError(str(err)) from err
    data = _target_data_path(target_dir, env_path)
    state_dir = os.path.realpath(state_dir)
    if os.path.commonpath((data, state_dir)) in (data, state_dir):
        raise MigrationError("private migration staging must not overlap the target data path")
    state_folder = _existing_directory(state_dir)
    data_folder = _existing_directory(data)
    copied = 0 if os.path.abspath(package_path) == os.path.join(os.path.abspath(state_dir), "package.tar") \
        else os.stat(package_path).st_size
    state_bytes = size * (4 if len(route) > 1 else 2) + copied
    same = os.stat(state_folder).st_dev == os.stat(data_folder).st_dev
    storage = {"state_bytes": state_bytes + _RESERVE, "data_bytes": size + _RESERVE,
               "same_filesystem": same, "required_bytes": state_bytes + size + _RESERVE * (1 if same else 2)}
    _require_space(state_folder, data_folder, state_bytes, size,
                   "for the history repositories, intermediate and target data")
    first = [entry["first"] for entry in metadata["inventory"].values() if entry["first"] is not None]
    last = [entry["last"] for entry in metadata["inventory"].values() if entry["last"] is not None]
    env = EnvFile(env_path or os.path.join(target_dir, "env.example"))
    from tpotctl import envschema

    schema = envschema.load_schema()
    return {
        "route": list(route), "indices": len(metadata["indices"]),
        "documents": sum(entry["count"] for entry in metadata["inventory"].values()),
        "first": min(first, key=_history_time) if first else None,
        "last": max(last, key=_history_time) if last else None,
        "spaces": len(metadata["kibana"]),
        "objects": sum(len(space["objects"]) for space in metadata["kibana"].values()),
        "ignored_indices": len(metadata["ignored_indices"]), "storage": storage,
        "retention": {
            "events": env.get("TPOT_ES_RETENTION") or schema["TPOT_ES_RETENTION"].default,
            "sensor_history": env.get("TPOT_SENSOR_STATUS_RETENTION") or schema["TPOT_SENSOR_STATUS_RETENTION"].default,
        },
    }


def _live_container(inventory: migration.SourceInventory, service: str, docker: DockerRuntime) -> dict:
    definition = inventory.compose.get("services", {}).get(service, {})
    name = definition.get("container_name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name):
        raise MigrationError(f"history migration requires an explicit {service} container name")
    info = docker.inspect("container", name)
    if info.get("Image") != inventory.source["image_digests"].get(service):
        raise MigrationError(f"running {service} image differs from the source configuration")
    if info.get("State", {}).get("Running") is not True:
        raise MigrationError(f"source {service} must be running for the initial history checks")
    project = inventory.compose.get("name")
    labels = info.get("Config", {}).get("Labels")
    if not project or not isinstance(labels, dict) or labels.get("com.docker.compose.project") != project:
        raise MigrationError(f"source {service} belongs to another Compose project")
    return info


def _source_api(info: dict, port: int) -> snapshots.SnapshotClient:
    ports = info.get("NetworkSettings", {}).get("Ports", {}).get(str(port) + "/tcp")
    if not isinstance(ports, list) or len(ports) != 1 or not isinstance(ports[0], dict) \
            or ports[0].get("HostIp") not in ("127.0.0.1", "0.0.0.0"):
        raise MigrationError("source maintenance API must be accessible through its local IPv4 port")
    value = ports[0].get("HostPort")
    if not isinstance(value, str) or not value.isdigit():
        raise MigrationError("source maintenance API has no valid published port")
    return snapshots.SnapshotClient("http://127.0.0.1:" + value)


def source_plan(inventory: migration.SourceInventory, docker: DockerRuntime) -> dict:
    source = inventory.source
    if source["elasticsearch_version"] is None:
        raise MigrationError("this source has no local Elasticsearch history; export history on its HIVE")
    info = _live_container(inventory, "elasticsearch", docker)
    client = _source_api(info, 9200)
    if client.version() != source["elasticsearch_version"]:
        raise MigrationError("source Elasticsearch runtime version differs from its image metadata")
    cluster = client.request("GET", "/_cluster/health?local=true")
    if not isinstance(cluster, dict) or type(cluster.get("number_of_nodes")) is not int \
            or cluster["number_of_nodes"] != 1:
        raise MigrationError("history migration requires a single-node source cluster")
    nodes = client.request("GET", "/_nodes/_local/settings?flat_settings=true")
    try:
        nodes = nodes["nodes"]
        if not isinstance(nodes, dict) or len(nodes) != 1:
            raise MigrationError("history migration requires a single-node source cluster")
        data = next(iter(nodes.values()))["settings"]["path.data"]
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if not isinstance(data, str) or not os.path.isabs(data) or os.path.normpath(data) != data:
            raise MigrationError("source Elasticsearch did not report one concrete data path")
        mounts = [mount for mount in info["Mounts"]
                  if mount.get("Type") == "bind" and mount.get("RW") is True
                  and isinstance(mount.get("Destination"), str)
                  and os.path.commonpath((mount["Destination"], data)) == mount["Destination"]]
        if len(mounts) != 1:
            raise MigrationError("source Elasticsearch data must use one unambiguous writable bind mount")
        mount = mounts[0]
        if any(other != mount and (other.get("Type") != "tmpfs" or
                                   other.get("Destination") not in ("/tmp", "/var/tmp"))
               for other in info["Mounts"]):
            raise MigrationError("custom Elasticsearch mounts need qualification before history migration")
        host_data = os.path.realpath(os.path.join(mount["Source"], os.path.relpath(data, mount["Destination"])))
        if os.path.commonpath((source["data_path"], host_data)) != source["data_path"] \
                or not os.path.isdir(host_data):
            raise MigrationError("source Elasticsearch data does not belong to the checked T-Pot data path")
    except (KeyError, TypeError, ValueError) as err:
        raise MigrationError("cannot resolve the actual Elasticsearch data mount") from err
    plan = {"es_image": info["Image"], "es_data": host_data, "es_url": client.url,
            "project": inventory.compose["name"], "kibana_image": None}
    if source["kibana_version"] is not None:
        kibana = _live_container(inventory, "kibana", docker)
        api = _source_api(kibana, 5601)
        try:
            try:
                raw = kibana_objects._request("GET", "/api/status", api.url, api.opener.open)
            except kibana_objects.KibanaError as err:
                if err.status != 404:
                    raise
                raw = kibana_objects._request("GET", "/kibana/api/status", api.url, api.opener.open)
            observed = json.loads(raw)
            if observed == {"status": {"overall": {"level": "available"}}}:
                actual = docker.docker("exec", inventory.compose["services"]["kibana"]["container_name"],
                                       "/usr/share/kibana/bin/kibana", "--version").strip()
            else:
                actual = observed["version"]["number"]
        except (kibana_objects.KibanaError, ValueError, KeyError, TypeError) as err:
            raise MigrationError("cannot verify the source Kibana runtime version") from err
        if actual != source["kibana_version"]:
            raise MigrationError("source Kibana runtime version differs from its image metadata")
        plan["kibana_image"] = kibana["Image"]
    return plan


def assert_data_idle(data: str, docker: DockerRuntime) -> None:
    data = os.path.realpath(data)
    for identifier in docker.docker("ps", "--quiet").split():
        try:
            mounts = json.loads(docker.docker("inspect", "--format", "{{json .Mounts}}", identifier))
        except ValueError as err:
            raise MigrationError("cannot inspect running container data mounts") from err
        if not isinstance(mounts, list):
            raise MigrationError("cannot determine whether another container uses the source data")
        for mount in mounts:
            if isinstance(mount, dict) and mount.get("Type") == "tmpfs":
                continue
            if not isinstance(mount, dict) or mount.get("Type") not in ("bind", "volume") \
                    or not isinstance(mount.get("Source"), str) or not os.path.isabs(mount["Source"]):
                raise MigrationError("a running container returned an invalid data mount")
            path = os.path.realpath(mount["Source"])
            common = os.path.commonpath((path, data))
            if mount.get("RW", True) and (common == path or common == data):
                raise MigrationError("another running container can write the source Elasticsearch data")


def select_indices(client: snapshots.SnapshotClient) -> Tuple[list, list]:
    rows = client.request("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index,status")
    if not isinstance(rows, list):
        raise MigrationError("Elasticsearch returned no concrete index inventory")
    selected, ignored, seen = [], [], set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("index"), str) or not row["index"] \
                or row["index"] in seen:
            raise MigrationError("Elasticsearch returned an invalid or duplicate index")
        name = row["index"]
        seen.add(name)
        if name.startswith(("logstash-", "tpot-sensor-heartbeat-")) or name == "tpot-sensor-status":
            snapshots._indices([name])
            if row.get("status") != "open":
                raise MigrationError(f"selected index is not open: {name}")
            selected.append(name)
        else:
            ignored.append(name)
    return sorted(selected), sorted(ignored)


def _space_objects(endpoint: str, opener: Optional[Callable], version: str) -> Optional[dict]:
    """The objects of one space, or None while the export is older than the real-time settings."""
    _text, objects = kibana_objects._export(endpoint, opener, allow_empty=True)
    found = {}
    for obj in objects:
        key = kibana_objects.key(obj)
        if key in found:
            raise MigrationError("Kibana exported a duplicate object within a space")
        found[key] = kibana_objects.normalise(obj)
    settings = json.loads(kibana_objects._request(
        "POST", "/api/saved_objects/_bulk_get", endpoint, opener,
        json.dumps([{"type": kind, "id": version} for kind in ("config", "config-global")]).encode(),
        "application/json"))
    settings = settings.get("saved_objects") if isinstance(settings, dict) else None
    if not isinstance(settings, list) or len(settings) != 2:
        raise MigrationError("Kibana returned no complete active settings inventory")
    seen, stale = set(), False
    for setting in settings:
        if not isinstance(setting, dict) or setting.get("type") not in ("config", "config-global") \
                or setting.get("type") in seen or setting.get("id") != version:
            raise MigrationError("Kibana returned an invalid active settings inventory")
        seen.add(setting["type"])
        if "error" in setting:
            if setting["type"] == "config-global" and isinstance(setting["error"], dict) \
                    and setting["error"].get("statusCode") == 404:
                continue
            raise MigrationError("Kibana could not read an active settings object")
        if not isinstance(setting.get("attributes"), dict):
            raise MigrationError("Kibana returned invalid active settings")
        key = kibana_objects.key(setting)
        normal = kibana_objects.normalise(setting)
        stale = stale or (key in found and found[key] != normal)
        found[key] = normal
    return None if stale else found


def kibana_inventory(url: str, opener: Optional[Callable] = None, *, version: str) -> dict:
    snapshots._version(version)
    try:
        spaces = json.loads(kibana_objects._request("GET", "/api/spaces/space", url, opener))
        if not isinstance(spaces, list) or not spaces:
            raise MigrationError("Kibana returned no spaces")
        result = {}
        for space in spaces:
            if not isinstance(space, dict) or not isinstance(space.get("id"), str) \
                    or not space["id"] or space["id"] in result:
                raise MigrationError("Kibana returned invalid or duplicate spaces")
            identifier = space["id"]
            endpoint = url if identifier == "default" else \
                url.rstrip("/") + "/s/" + urllib.parse.quote(identifier, safe="")
            # Kibana upgrades the active per-space/global settings lazily when rendering its UI.
            kibana_objects._request("GET", "/app/home", endpoint, opener)
            for attempt in range(KIBANA_INVENTORY_READS):
                if attempt:
                    time.sleep(KIBANA_INVENTORY_DELAY)
                found = _space_objects(endpoint, opener, version)
                if found is not None:
                    break
            else:
                raise MigrationError("Kibana returned conflicting objects within a space")
            result[identifier] = {"space": space, "objects": found}
        return result
    except (ValueError, kibana_objects.KibanaError) as err:
        raise MigrationError(f"cannot inventory all Kibana spaces: {err}") from err


def verify_kibana(expected: dict, actual: dict, *, source_version: str, target_version: str) -> None:
    snapshots._version(source_version)
    snapshots._version(target_version)
    if not isinstance(expected, dict) or not isinstance(actual, dict) or set(expected) != set(actual):
        raise MigrationError("restored Kibana spaces differ from the source")
    for identifier, before in expected.items():
        after = actual[identifier]
        for key, value in before["space"].items():
            if after["space"].get(key) != value:
                raise MigrationError(f"restored Kibana space settings differ: {json.dumps(identifier)}")
        for key, value in before["objects"].items():
            active_config = key in (f"config:{source_version}", f"config-global:{source_version}")
            wanted = f"{value['type']}:{target_version}" if active_config else key
            observed = after["objects"].get(wanted)
            matches = observed == value
            if active_config and isinstance(observed, dict):
                fields = dict(value, id=target_version)
                attributes = {name: setting for name, setting in fields.pop("attributes").items()
                              if name != "buildNum"}
                matches = all(name in observed and observed[name] == setting for name, setting in fields.items())
                settings = observed.get("attributes")
                matches = matches and isinstance(settings, dict) and all(
                    name in settings and settings[name] == setting for name, setting in attributes.items())
            if not matches:
                location = json.dumps(identifier + "/" + wanted, ensure_ascii=True)
                raise MigrationError(f"restored Kibana object differs or is missing: {location}")


def verify_restored(client: snapshots.SnapshotClient, metadata: dict, *, target_version: str,
                    kibana_url: Optional[str] = None, opener: Optional[Callable] = None) -> None:
    """Read the actual restored state again, including indices not named by the package."""
    if client.version() != target_version:
        raise MigrationError("restored Elasticsearch runtime version differs from the target")
    selected, _ignored = select_indices(client)
    if set(selected) != set(metadata["indices"]):
        raise MigrationError("restored index set differs from the source")
    snapshots.verify_inventory(metadata["inventory"], client.inventory(selected))
    client.verify_samples(metadata["inventory"])
    if metadata["kibana"]:
        if not kibana_url:
            raise MigrationError("restored Kibana must be available for acceptance")
        verify_kibana(metadata["kibana"], kibana_inventory(kibana_url, opener, version=target_version),
                      source_version=metadata["kibana_version"], target_version=target_version)


def _repository_files(root: Path, relative: str = ""):
    path = root / relative
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise MigrationError("snapshot repository contains a linked or invalid directory")
    with os.scandir(path) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        child = relative + "/" + name if relative else name
        migration._logical_path("repository/" + child, ("repository",))
        info = (root / child).lstat()
        if stat.S_ISDIR(info.st_mode):
            yield from _repository_files(root, child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            yield child
        else:
            raise MigrationError("snapshot repository must contain only real directories and single-link files")


def pack_repository(directory: str, destination: str) -> None:
    root = Path(directory)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                count = 0
                for name in _repository_files(root):
                    handle, before = migration._open_regular(str(root / name), "snapshot repository file")
                    with handle:
                        if before.st_nlink != 1:
                            raise MigrationError("snapshot repository file gained another link")
                        item = tarfile.TarInfo(name)
                        item.mode, item.size = 0o600, before.st_size
                        archive.addfile(item, handle)
                        after = os.fstat(handle.fileno())
                        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                            raise MigrationError("snapshot repository changed while being packaged")
                    count += 1
                if not count:
                    raise MigrationError("snapshot repository is empty")
            stream.flush()
            os.fsync(stream.fileno())
        validate_repository(destination)
        migration._fsync_directory(str(Path(destination).parent))
    except (OSError, tarfile.TarError, MigrationError) as err:
        os.unlink(destination)
        raise MigrationError(f"cannot package snapshot repository: {err}") from err


def _repository_members(archive):
    files, directories = set(), set()
    for member in archive:
        migration._logical_path("repository/" + member.name, ("repository",))
        if not member.isfile() or member.issparse() or member.size < 0:
            raise MigrationError("snapshot archive must contain only regular files")
        prefixes = [str(parent) for parent in Path(member.name).parents if str(parent) != "."]
        if member.name in files or member.name in directories or any(prefix in files for prefix in prefixes):
            raise MigrationError("snapshot archive contains duplicate or conflicting paths")
        files.add(member.name)
        directories.update(prefixes)
        yield member
    if not files:
        raise MigrationError("snapshot archive is empty")


def validate_repository(filename: str) -> None:
    handle, _info = migration._open_regular(filename, "snapshot archive")
    try:
        with handle, tarfile.open(fileobj=handle, mode="r|") as archive:
            for member in _repository_members(archive):
                with archive.extractfile(member) as data:
                    while data.read(1024 * 1024):
                        pass
    except (OSError, tarfile.TarError) as err:
        raise MigrationError(f"cannot validate snapshot archive: {err}") from err


def unpack_repository(filename: str, directory: str) -> None:
    target = Path(directory)
    if os.path.lexists(target) and (not stat.S_ISDIR(target.lstat().st_mode) or any(target.iterdir())):
        raise MigrationError("snapshot repository target must be empty and cannot be a link")
    validate_repository(filename)
    migration._private_directory(str(target), create=True)
    handle, _info = migration._open_regular(filename, "snapshot archive")
    try:
        with handle, tarfile.open(fileobj=handle, mode="r|") as archive:
            for member in _repository_members(archive):
                path = target / member.name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as output, archive.extractfile(member) as data:
                    while True:
                        chunk = data.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                migration._fsync_directory(str(path.parent))
        migration._fsync_directory(str(target))
    except (OSError, tarfile.TarError) as err:
        raise MigrationError(f"cannot unpack snapshot repository; private partial files are retained: {err}") from err


def _host_id() -> str:
    value = migration._read_source_file("/etc/machine-id", "source host identity").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise MigrationError("the source has no valid machine identity")
    return value


def _state_directory(source: str) -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    key = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return os.path.join(base, "tpotce", "migration", "exports", key)


# the phases of an export journal in which its source may still be held: resume_source releases it
RESUMABLE = ("new", "held", "snapshot", "packaged", "resuming")


def export_state(source_dir: str) -> str:
    """The journal (state.json) of the history export of a checkout. resume_source and the Update & backup
    page of the T-Pot Manager (ops.migration_hold) read the same file."""
    return os.path.join(_state_directory(os.path.realpath(os.path.expanduser(source_dir))), "state.json")


def holds_source(state: dict) -> bool:
    """Has the export of this journal stopped its source without having released it again?"""
    phase, facts = state.get("phase"), state.get("facts")
    return phase in RESUMABLE and (phase != "new" or (isinstance(facts, dict) and "source" in facts))


def _system_docker(journal: runtime.Journal, host: runtime.SystemdHold) -> DockerRuntime:
    def execute(*args, timeout=180):
        if not args or args[0] != "docker":
            raise runtime.RuntimeError("only Docker operations belong to this migration executor")
        return host.command("env", "-u", "DOCKER_HOST", "-u", "DOCKER_CONTEXT",
                            "docker", "--host", "unix:///var/run/docker.sock", *args[1:], timeout=timeout)

    return DockerRuntime(journal, execute=execute)


def _host_docker(journal: runtime.Journal, host: runtime.SystemdHold) -> DockerRuntime:
    docker = _system_docker(journal, host)
    current = migration._run_output(["docker", "info", "--format", "{{.ID}}"],
                                    what="checking the source Docker daemon").strip()
    privileged = docker.docker("info", "--format", "{{.ID}}").strip()
    if not current or current != privileged:
        raise MigrationError("the selected Docker daemon is not the host's system Docker")
    return docker


def _check_unit(source: str, host: runtime.SystemdHold) -> None:
    value = host.command("systemctl", "show", "--property=ExecStart", "--value", host.unit)
    match = re.search(r"argv\[\]=([^;]+);", value)
    try:
        args = shlex.split(match[1]) if match else []
    except ValueError as err:
        raise MigrationError("cannot interpret the source T-Pot service command") from err
    if len(args) != 5 or os.path.basename(args[0]) != "docker" or args[1:] != [
        "compose", "-f", os.path.join(source, "docker-compose.yml"), "up",
    ]:
        raise MigrationError("the requested source does not match the standard tpot.service command")


def _space_needed(client: snapshots.SnapshotClient) -> int:
    value = client.request("GET", "/_stats/store?expand_wildcards=all&filter_path=_all.primaries.store.size_in_bytes")
    try:
        size = value["_all"]["primaries"]["store"]["size_in_bytes"]
    except (KeyError, TypeError) as err:
        raise MigrationError("cannot determine the source Elasticsearch storage size") from err
    if type(size) is not int or size < 0:
        raise MigrationError("source Elasticsearch returned an invalid storage size")
    return size


def _check_space(size: int, journal: runtime.Journal, destination: str) -> None:
    parent = Path(destination).parent
    if not stat.S_ISDIR(parent.lstat().st_mode):
        raise MigrationError("migration package directory must be a real directory")
    with tempfile.TemporaryFile(dir=parent):
        pass
    same = parent.stat().st_dev == journal.directory.stat().st_dev
    reserve = 512 * 1024 * 1024
    for path, copies in ((journal.directory, 3 if same else 2), (parent, 3 if same else 1)):
        needed = copies * size + reserve
        if shutil.disk_usage(path).free < needed:
            raise MigrationError(f"not enough free space at {path}; at least {needed} bytes are required")


_ATTEMPT_FOLDER = re.compile(r"(?:configuration|repository)-[0-9a-f]{32}")
_ATTEMPT_FILE = re.compile(r"(?:repository-[0-9a-f]{32}\.tar|history-[0-9a-f]{32}\.json)")


def _discard_attempts(journal: runtime.Journal, docker: DockerRuntime, image: str, keep: set) -> None:
    """Remove what failed attempts of this run left in its private folder: only names it makes itself."""
    for entry in os.scandir(journal.directory):
        if entry.name in keep:
            continue
        if _ATTEMPT_FOLDER.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False):
            if entry.name.startswith("repository-"):
                # an interrupted snapshot leaves its repository owned by the Elasticsearch user
                docker.permissions(entry.path, image, f"{os.getuid()}:{os.getgid()}", recursive=True)
            shutil.rmtree(entry.path)
        elif _ATTEMPT_FILE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
            os.unlink(entry.path)


def _discard_snapshot(journal: runtime.Journal, docker: DockerRuntime, image: str) -> None:
    """Give the space of the snapshot repository and its archive back once the package holds them or the
    source runs again: each is as large as the history."""
    keep = {entry.name for entry in os.scandir(journal.directory) if not entry.name.startswith("repository-")}
    _discard_attempts(journal, docker, image, keep)


def _frozen_files(inventory: migration.SourceInventory, journal: runtime.Journal) -> dict:
    folder = journal.directory / ("configuration-" + uuid.uuid4().hex)
    folder.mkdir(mode=0o700)
    files = {}
    for logical, source in inventory.files.items():
        migration._logical_path(logical, inventory.scope)
        target = folder / logical
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        handle, before = migration._open_regular(source, "selected source identity or configuration")
        with handle:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                shutil.copyfileobj(handle, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
                after = os.fstat(handle.fileno())
                if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                    raise MigrationError("source identity or configuration changed while being frozen")
        files[logical] = str(target.relative_to(journal.directory))
    return files


def _run_file(journal: runtime.Journal, name: str) -> str:
    migration._logical_path("run/" + name, ("run",))
    path = journal.directory / name
    migration._private_file(str(path), "migration checkpoint file")
    return str(path)


def _source_unchanged(inventory: migration.SourceInventory, journal: runtime.Journal) -> dict:
    facts = journal.state["facts"]
    if facts.get("source") != inventory.source:
        raise MigrationError("source metadata changed after the history migration began")
    if not isinstance(facts.get("configuration"), dict):
        raise MigrationError("the migration has no frozen source configuration record")
    for name, digest in facts["configuration"].items():
        if name not in inventory.files or migration._hash_file(inventory.files[name])[1] != digest:
            raise MigrationError("source configuration changed after the history migration began")
    return facts


def _adopt_publication(destination: str, inventory: migration.SourceInventory, files: dict) -> dict:
    manifest = migration.validate_package(destination)
    records = {entry["path"]: (entry["size"], entry["sha256"]) for entry in manifest["files"]}
    if manifest["source"] != inventory.source or manifest["scope"] != [*inventory.scope, "history"] \
            or set(records) != set(files):
        raise MigrationError("existing package does not belong to this migration checkpoint")
    if any(migration._hash_file(path) != records[name] for name, path in files.items()):
        raise MigrationError("existing package differs from the checkpointed migration data")
    return manifest


def _capture(inventory: migration.SourceInventory, journal: runtime.Journal, docker: DockerRuntime) -> None:
    plan = journal.state["facts"]["plan"]
    repository = docker.directory("repository-" + uuid.uuid4().hex)
    image = plan["es_image"]
    metadata = None
    try:
        docker.permissions(repository, image, docker.image_user(image))
        name, client = docker.elasticsearch(image, inventory.source["elasticsearch_version"], "source",
                                              plan["es_data"], {"original": (repository, False)})
        client.pause_ilm()
        selected, ignored = select_indices(client)
        before = client.inventory(selected)
        kibana = {}
        if plan["kibana_image"] is not None:
            kb_name, url, opener = docker.kibana(plan["kibana_image"], inventory.source["kibana_version"],
                                                "kibana", name)
            kibana = kibana_inventory(url, opener.open, version=inventory.source["kibana_version"])
            docker.stop(kb_name)
        client.repository("migration", "/snapshots/original", readonly=False)
        snapshot = client.snapshot("migration", "original", selected, kibana=bool(kibana))
        included = set(snapshot["indices"])
        ignored = [index for index in ignored if index not in included]
        snapshots.verify_inventory(before, client.inventory(selected))
        client.verify_samples(before)
        # The productive source has no path.repo; a registration left in its cluster state fails on every start.
        _acknowledged(client.request("DELETE", "/_snapshot/migration"), "the repository removal")
        metadata = {
            "format": "tpot-migration-history", "schema": 2, "run": journal.run_id,
            "elasticsearch_version": inventory.source["elasticsearch_version"],
            "kibana_version": inventory.source["kibana_version"], "snapshot": "original",
            "indices": selected, "inventory": before, "kibana": kibana, "ignored_indices": ignored,
        }
    finally:
        try:
            docker.cleanup()
        finally:
            docker.permissions(repository, image, f"{os.getuid()}:{os.getgid()}", recursive=True)
    _validate_history(metadata, inventory.source)
    encoded = json.dumps(metadata, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_HISTORY_METADATA:
        raise MigrationError("history inventory exceeds the supported metadata size")
    filename = "history-" + uuid.uuid4().hex + ".json"
    migration._write_private(str(journal.directory / filename), encoded)
    journal.record("snapshot", repository=Path(repository).name, metadata=filename)
    if metadata["ignored_indices"]:
        names = metadata["ignored_indices"]
        say.warn(f"{len(names)} indices are not included in the history snapshot: "
                 f"{json.dumps(names[:20], ensure_ascii=True)}. The full list is in the private history inventory.")


def export_history(source_dir: str, destination: str, become_file: Optional[str] = None) -> dict:
    if platform.system() != "Linux":
        raise MigrationError("history export must run on the original Linux T-Pot host")
    source_dir = os.path.realpath(os.path.expanduser(source_dir))
    destination = os.path.abspath(os.path.expanduser(destination))
    inventory = migration.inspect_source(source_dir)
    identity = {"operation": "export", "source": source_dir, "host": _host_id(), "uid": os.getuid()}
    try:
        with runtime.Journal(_state_directory(source_dir), identity) as journal:
            if journal.state["phase"] == "released":
                journal.next_run()
            host = runtime.SystemdHold(journal.run_id, become_file)
            host.check_access()
            docker = _host_docker(journal, host)
            _check_unit(source_dir, host)
            with runtime.administration_locks(Path.home(), Path(inventory.source["data_path"])):
                facts = journal.state["facts"]
                if journal.state["phase"] == "new":
                    if os.path.lexists(destination):
                        raise MigrationError("migration package destination already exists")
                    plan = source_plan(inventory, docker)
                    size = _space_needed(snapshots.SnapshotClient(plan["es_url"]))
                    _check_space(size, journal, destination)
                    hashes = {name: migration._hash_file(path)[1] for name, path in inventory.files.items()
                              if name.startswith("configuration/")}
                    journal.record("new", source=inventory.source, plan=plan, bytes=size, configuration=hashes)
                    if not host.guard("hold"):
                        raise MigrationError("the source start guard could not be installed")
                    host.command("systemctl", "daemon-reload")
                    journal.record("held")
                elif journal.state["phase"] not in ("held", "snapshot", "packaged"):
                    raise MigrationError("this migration must finish its recovery before another export")
                elif not host.guard("status"):
                    raise MigrationError("the source migration guard was removed; refusing to reuse its snapshot")
                facts = _source_unchanged(inventory, journal)
                docker.cleanup()
                if journal.state["phase"] in ("snapshot", "packaged"):
                    active = host.command("systemctl", "show", "--property=ActiveState", "--value", host.unit).strip()
                    if active not in ("inactive", "failed"):
                        raise MigrationError("the source is no longer stopped; its old snapshot cannot be a final export")
                else:
                    if not facts.get("ilm_paused"):
                        snapshots.SnapshotClient(facts["plan"]["es_url"]).pause_ilm()
                        journal.record("held", ilm_paused=True)
                    host.hold_and_stop()
                assert_data_idle(facts["plan"]["es_data"], docker)
                if journal.state["phase"] == "packaged":
                    if destination != facts["destination"]:
                        raise MigrationError("the final export already exists at " + facts["destination"])
                    manifest = migration.validate_package(destination)
                    if manifest != facts["manifest"]:
                        raise MigrationError("the final export no longer matches its checkpoint")
                    with tarfile.open(destination, "r:") as archive, \
                            archive.extractfile("payload/history/inventory.json") as stream:
                        _read_history_metadata(stream, inventory.source)
                    _discard_snapshot(journal, docker, facts["plan"]["es_image"])
                    return manifest
                if journal.state["phase"] == "held":
                    if os.path.lexists(destination):
                        raise MigrationError("migration package destination already exists")
                    _discard_attempts(journal, docker, facts["plan"]["es_image"], set())
                    _check_space(facts["bytes"], journal, destination)
                    files = _frozen_files(inventory, journal)
                    for name, digest in facts["configuration"].items():
                        if migration._hash_file(_run_file(journal, files[name]))[1] != digest:
                            raise MigrationError("source configuration changed during the source stop")
                    journal.record("held", files=files)
                    _capture(inventory, journal, docker)
                facts = journal.state["facts"]
                with open(_run_file(journal, facts["metadata"]), "rb") as stream:
                    _read_history_metadata(stream, inventory.source)
                archive = facts.get("archive")
                if archive is None:
                    keep = {facts["repository"], facts["metadata"],
                            *(Path(name).parts[0] for name in facts["files"].values())}
                    _discard_attempts(journal, docker, facts["plan"]["es_image"], keep)
                    filename = "repository-" + uuid.uuid4().hex + ".tar"
                    pack_repository(str(journal.directory / facts["repository"]), str(journal.directory / filename))
                    size, digest = migration._hash_file(str(journal.directory / filename))
                    archive = {"file": filename, "size": size, "sha256": digest}
                    journal.record("snapshot", archive=archive)
                path = _run_file(journal, archive["file"])
                if migration._hash_file(path) != (archive["size"], archive["sha256"]):
                    raise MigrationError("the checkpointed snapshot archive changed")
                files = {name: _run_file(journal, filename) for name, filename in facts["files"].items()}
                files.update({"history/repository.tar": path,
                              "history/inventory.json": _run_file(journal, facts["metadata"])})
                if os.path.lexists(destination):
                    manifest = _adopt_publication(destination, inventory, files)
                else:
                    manifest = migration.create_package(destination, inventory.source,
                                                         (*inventory.scope, "history"), files)
                journal.record("packaged", destination=destination, manifest=manifest)
                _discard_snapshot(journal, docker, facts["plan"]["es_image"])
                return manifest
    except (runtime.RuntimeError, snapshots.SnapshotError, OSError, ValueError, tarfile.TarError, KeyError) as err:
        raise MigrationError(f"history export failed; any installed source guard remains in place: {err}") from err


def _forget_export_repository(client: snapshots.SnapshotClient) -> None:
    """Unregister the repository a failed export left in the source cluster; one of the user's stays."""
    repositories = client.request("GET", "/_snapshot")
    if not isinstance(repositories, dict):
        raise runtime.RuntimeError("Elasticsearch returned an invalid repository list")
    entry = repositories.get("migration")
    if isinstance(entry, dict) and entry.get("type") == "fs" \
            and (entry.get("settings") or {}).get("location") == "/snapshots/original":
        _acknowledged(client.request("DELETE", "/_snapshot/migration"), "the repository removal")


def resume_source(source_dir: str, become_file: Optional[str] = None) -> None:
    if platform.system() != "Linux":
        raise MigrationError("the source can only be resumed on its original Linux host")
    source_dir = os.path.realpath(os.path.expanduser(source_dir))
    state = export_state(source_dir)
    directory = os.path.dirname(state)
    if not os.path.lexists(state):
        raise MigrationError("there is no history migration to resume on this source")
    inventory = migration.inspect_source(source_dir)
    identity = {"operation": "export", "source": source_dir, "host": _host_id(), "uid": os.getuid()}
    try:
        with runtime.Journal(directory, identity) as journal:
            if journal.state["phase"] not in RESUMABLE:
                raise MigrationError("this migration has no source hold to release")
            if not holds_source(journal.state):
                raise MigrationError("the export never stopped the source; it runs as before and needs no resume")
            facts = _source_unchanged(inventory, journal)
            host = runtime.SystemdHold(journal.run_id, become_file)
            docker = _host_docker(journal, host)
            _check_unit(source_dir, host)
            with runtime.administration_locks(Path.home(), Path(inventory.source["data_path"])):
                held = host.guard("status")
                if not held and journal.state["phase"] != "resuming":
                    raise MigrationError("the source hold is missing; refusing an unrelated service start")
                if held:
                    host.hold_and_stop()
                docker.cleanup()
                active = host.command("systemctl", "show", "--property=ActiveState", "--value", host.unit).strip()
                if active in ("inactive", "failed"):
                    assert_data_idle(facts["plan"]["es_data"], docker)
                elif journal.state["phase"] != "resuming" or active != "active":
                    raise MigrationError("cannot confirm that the source can be safely resumed")
                # before the start: the running source must not meet a disk the snapshot copies fill
                _discard_snapshot(journal, docker, facts["plan"]["es_image"])
                journal.record("resuming")
                if held:
                    host.release()
                host.start()
                deadline = time.monotonic() + 600
                last = "source stack is not ready"
                while time.monotonic() < deadline:
                    try:
                        current = source_plan(inventory, docker)
                    except (MigrationError, runtime.RuntimeError, snapshots.SnapshotError) as err:
                        last = str(err)
                        time.sleep(2)
                        continue
                    if current["es_data"] != facts["plan"]["es_data"]:
                        raise MigrationError("the restarted source uses a different Elasticsearch data path")
                    client = snapshots.SnapshotClient(current["es_url"])
                    if client.version() != inventory.source["elasticsearch_version"]:
                        raise MigrationError("the restarted source uses another Elasticsearch version")
                    _forget_export_repository(client)
                    result = client.request("POST", "/_ilm/start")
                    if not isinstance(result, dict) or result.get("acknowledged") is not True \
                            or client.request("GET", "/_ilm/status").get("operation_mode") != "RUNNING":
                        raise MigrationError("the source has not confirmed that ILM is running again")
                    journal.record("released")
                    return
                raise MigrationError("source restart did not become ready: " + last)
    except (runtime.RuntimeError, snapshots.SnapshotError, OSError, ValueError) as err:
        raise MigrationError(f"source recovery failed; the migration state was retained: {err}") from err


_WORK = ("original", "intermediate-data", "intermediate")
_RESTORE_ERRORS = (MigrationError, runtime.RuntimeError, snapshots.SnapshotError, OSError, ValueError,
                   tarfile.TarError, KeyError)


def _restore_journal(target_dir: str, state_dir: str, marker: dict) -> runtime.Journal:
    identity = {"operation": "import", "host": _host_id(), "uid": os.getuid(), "target": target_dir,
                "package_sha256": marker["package_sha256"]}
    return runtime.Journal(os.path.join(os.path.abspath(state_dir), "history"), identity)


def _pending_history(target_dir: str, state_dir: str) -> Tuple[str, dict, dict, str]:
    from tpotctl import migration_import

    if platform.system() != "Linux":
        raise MigrationError("the history restore runs on the Linux target only")
    target_dir = migration_import._target_checkout(target_dir)
    marker, manifest, package_path = migration_import._read_pending(target_dir, state_dir)
    return target_dir, marker, manifest, package_path


def claimed_target(target_dir: str, state_dir: str, marker: dict) -> Optional[str]:
    """The target Elastic data folder this import claimed, verified against its recorded identity."""
    if not os.path.lexists(os.path.join(os.path.abspath(state_dir), "history")):
        return None
    try:
        with _restore_journal(target_dir, state_dir, marker) as journal:
            claim = journal.state["facts"].get("target_data")
    except runtime.RuntimeError as err:
        raise MigrationError(f"cannot read the history restore state: {err}") from err
    if not isinstance(claim, dict) or claim.get("state") not in ("creating", "ready") \
            or not isinstance(claim.get("path"), str):
        return None
    if claim["state"] == "ready":
        try:
            info = os.lstat(claim["path"])
        except OSError as err:
            raise MigrationError(f"the claimed target data is gone: {err}") from err
        if not stat.S_ISDIR(info.st_mode) or info.st_dev != claim.get("device") \
                or info.st_ino != claim.get("inode"):
            raise MigrationError("the claimed target data was replaced after the history restore began")
    return claim["path"]


def history_released(state_dir: str, marker: dict) -> bool:
    """Whether the restore of this staged package passed its acceptance and released the target."""
    directory = os.path.join(os.path.abspath(state_dir), "history")
    path = os.path.join(directory, "state.json")
    if not os.path.lexists(path):
        return False
    try:
        migration._private_file(path, "history restore state")
        with open(path, encoding="utf-8") as handle:
            identity = json.load(handle).get("identity")
        if not isinstance(identity, dict) or identity.get("operation") != "import" \
                or identity.get("uid") != os.getuid() \
                or identity.get("package_sha256") != marker.get("package_sha256"):
            return False
        with runtime.Journal(directory, identity) as journal:
            return journal.state["phase"] == "released"
    except (OSError, ValueError, AttributeError, runtime.RuntimeError) as err:
        raise MigrationError(f"cannot read the history restore state: {err}") from err


def hold_history(target_dir: str, state_dir: str, become_file: Optional[str] = None) -> bool:
    """Guard tpot.service before the installer can start the target; False without history."""
    target_dir, marker, manifest, _package = _pending_history(target_dir, state_dir)
    if "history" not in manifest["scope"]:
        return False
    try:
        with _restore_journal(target_dir, state_dir, marker) as journal:
            host = runtime.SystemdHold(journal.run_id, become_file)
            if journal.state["phase"] == "new":
                host.hold()
                journal.record("held")
            elif journal.state["phase"] not in ("releasing", "released") and not host.guard("status"):
                raise runtime.RuntimeError("the target start guard of this migration is missing")
    except runtime.RuntimeError as err:
        raise MigrationError(f"cannot hold the target for the history restore: {err}") from err
    return True


def _reclaim(docker: DockerRuntime, path: str, image: str) -> None:
    if os.path.lexists(path):
        docker.permissions(path, image, f"{os.getuid()}:{os.getgid()}", recursive=True)
        shutil.rmtree(path)


def _empty(path: str) -> None:
    for entry in os.scandir(path):
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path)
        else:
            os.unlink(entry.path)


def _unpack_original(journal: runtime.Journal, package_path: str) -> str:
    destination = str(journal.directory / "original")
    fd, temporary = tempfile.mkstemp(prefix=".repository-", dir=journal.directory)
    try:
        with os.fdopen(fd, "wb") as output, tarfile.open(package_path, "r:") as outer, \
                outer.extractfile("payload/history/repository.tar") as stream:
            shutil.copyfileobj(stream, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        unpack_repository(temporary, destination)
    finally:
        os.unlink(temporary)
    return destination


def _accept(docker: DockerRuntime, client, es_name: str, metadata: dict, version: str,
            kibana_image: Optional[str], suffix: str) -> None:
    url = opener = name = None
    if kibana_image is not None:
        name, url, kibana_opener = docker.kibana(kibana_image, version, suffix, es_name)
        opener = kibana_opener.open
    verify_restored(client, metadata, target_version=version, kibana_url=url, opener=opener)
    if name is not None:
        docker.stop(name)


def _acknowledged(value, what: str) -> None:
    if not isinstance(value, dict) or value.get("acknowledged") is not True:
        raise runtime.RuntimeError(f"Elasticsearch did not acknowledge {what}")


def _ilm_running(client) -> None:
    status = client.request("GET", "/_ilm/status")
    if not isinstance(status, dict) or status.get("operation_mode") != "RUNNING":
        raise runtime.RuntimeError("index lifecycle management of the restored cluster is not running")


def _intermediate(journal: runtime.Journal, docker: DockerRuntime, metadata: dict, version: str,
                  package_path: str, helper: str, kibana: bool, target: str, size: int) -> None:
    paths = {name: str(journal.directory / name) for name in _WORK}
    for path in paths.values():
        _reclaim(docker, path, helper)
    es_image = "docker.elastic.co/elasticsearch/elasticsearch:" + version
    kibana_image = "docker.elastic.co/kibana/kibana:" + version if kibana else None
    for image in filter(None, (es_image, kibana_image)):
        docker.pull(image)
    # apply checked before the T-Pot and these images were pulled; the same figures as there
    _require_space(str(journal.directory), target, 4 * size, size, "for the history restore")
    user = docker.image_user(es_image)
    if not re.fullmatch(r"[0-9]+:[0-9]+", user):
        raise runtime.RuntimeError("the intermediate Elasticsearch image must run as a numeric user")
    original = _unpack_original(journal, package_path)
    data = docker.directory("intermediate-data")
    repository = docker.directory("intermediate")
    docker.permissions(original, helper, user, recursive=True)
    docker.permissions(data, helper, user)
    docker.permissions(repository, helper, user)
    try:
        name, client = docker.elasticsearch(es_image, version, "intermediate", data, {
            "original": (original, True), "intermediate": (repository, False)})
        client.pause_ilm()
        client.repository("original", "/snapshots/original", readonly=True)
        client.restore("original", metadata["snapshot"], metadata["indices"], kibana=kibana)
        _accept(docker, client, name, metadata, version, kibana_image, "intermediate-kibana")
        client.repository("intermediate", "/snapshots/intermediate", readonly=False)
        client.snapshot("intermediate", "intermediate", metadata["indices"], kibana=kibana)
    finally:
        docker.cleanup()
        for name in ("original", "intermediate-data"):
            _reclaim(docker, paths[name], helper)


def _restore(journal: runtime.Journal, host: runtime.SystemdHold, docker: DockerRuntime, metadata: dict,
             route: list, data: str, package_path: str, images: dict, size: int) -> None:
    kibana = bool(metadata["kibana"])
    helper = images["elasticsearch"]
    es_user = docker.image_user(helper)
    if kibana:
        docker.image_info(images["kibana"])
    docker.cleanup()
    phase = journal.state["phase"]
    claim = journal.state["facts"].get("target_data")
    claimed = isinstance(claim, dict) and claim.get("state") == "ready"
    target = docker.target_directory(os.path.join(data, "elk", "data"))
    version = route[-1]
    two = len(route) == 2
    repository, snapshot = ("intermediate", "intermediate") if two else ("original", metadata["snapshot"])
    if phase != "verified":
        if claimed:
            docker.permissions(target, helper, f"{os.getuid()}:{os.getgid()}", recursive=True)
            _empty(target)
        if two and phase == "held":
            _intermediate(journal, docker, metadata, route[0], package_path, helper, kibana, target, size)
            journal.record("snapshot", intermediate=True)
        if two:
            folder = str(journal.directory / repository)
            if not os.path.isdir(folder):
                raise runtime.RuntimeError("the intermediate snapshot repository is missing")
            _require_space(str(journal.directory), target, 0, size, "for the history restore")
        else:
            _reclaim(docker, str(journal.directory / "original"), helper)
            _require_space(str(journal.directory), target, 2 * size, size, "for the history restore")
            folder = _unpack_original(journal, package_path)
        docker.permissions(target, helper, es_user)
        docker.permissions(folder, helper, es_user, recursive=True)
        name, client = docker.elasticsearch(helper, version, "target", target, {repository: (folder, True)})
        client.pause_ilm()
        journal.record("restoring")
        client.repository(repository, "/snapshots/" + repository, readonly=True)
        client.restore(repository, snapshot, metadata["indices"], kibana=kibana)
        _accept(docker, client, name, metadata, version, images["kibana"] if kibana else None, "target-kibana")
        _acknowledged(client.request("DELETE", "/_snapshot/" + repository), "the repository removal")
        _acknowledged(client.request("POST", "/_ilm/start"), "the index lifecycle restart")
        _ilm_running(client)
        journal.record("verified")
    else:
        name, client = docker.elasticsearch(helper, version, "target", target, {})
        _accept(docker, client, name, metadata, version, images["kibana"] if kibana else None, "target-kibana")
        if client.request("GET", "/_snapshot") != {}:
            raise runtime.RuntimeError("the restored cluster still names a snapshot repository")
        _ilm_running(client)
    docker.cleanup()
    _release(journal, host, docker, helper)


def _release(journal: runtime.Journal, host: runtime.SystemdHold, docker: DockerRuntime, helper: str) -> None:
    # recorded first: the guard may be gone before the reload, a retry only finishes the release then
    journal.record("releasing")
    host.release()
    journal.record("released")
    for name in _WORK:
        _reclaim(docker, str(journal.directory / name), helper)


def restore_history(target_dir: str, state_dir: str, become_file: Optional[str] = None) -> dict:
    """Restore and accept the source history in isolation, then release the held target service."""
    from contextlib import ExitStack

    from tpotctl import migration_import
    from tpotctl.envfile import EnvFile

    target_dir, marker, manifest, package_path = _pending_history(target_dir, state_dir)
    if "history" not in manifest["scope"]:
        raise MigrationError("the staged migration carries no Elastic history")
    metadata, size = read_history(manifest, package_path)
    prepared, report = migration_import._load_prepared(state_dir, marker)
    data = migration_import._target_data_path(target_dir)
    if report["target_data_path"] != data:
        raise MigrationError("target data path changed after import preparation")
    env_path = os.path.join(target_dir, ".env")
    if not migration_import._same_file(os.path.join(prepared, "env"), env_path, environment=True):
        raise MigrationError("apply the prepared import before restoring its history")
    env = EnvFile(env_path)
    repo, version = env.get("TPOT_REPO"), env.get("TPOT_VERSION")
    if not repo or not re.fullmatch(r"[a-z0-9][a-z0-9._/:-]*", repo) \
            or not version or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", version):
        raise MigrationError("the target .env names no valid T-Pot image repository and version")
    images = {"elasticsearch": f"{repo}/elasticsearch:{version}", "kibana": f"{repo}/kibana:{version}"}
    summary = report["history"]
    with ExitStack() as stack:
        try:
            journal = stack.enter_context(_restore_journal(target_dir, state_dir, marker))
        except runtime.RuntimeError as err:
            raise MigrationError(f"cannot open the history restore: {err}") from err
        if journal.state["phase"] == "new":
            raise MigrationError("hold the target service before restoring the history")
        docker = None
        try:
            host = runtime.SystemdHold(journal.run_id, become_file)
            docker = _system_docker(journal, host)
            if journal.state["phase"] == "released":
                for name in _WORK:
                    _reclaim(docker, str(journal.directory / name), images["elasticsearch"])
            elif journal.state["phase"] == "releasing":
                with runtime.administration_locks(Path.home(), Path(data)):
                    docker.cleanup()
                    _release(journal, host, docker, images["elasticsearch"])
            else:
                if not host.guard("status"):
                    raise runtime.RuntimeError("the target start guard of this migration is missing")
                with runtime.administration_locks(Path.home(), Path(data)):
                    _restore(journal, host, docker, metadata, list(summary["route"]), data, package_path, images,
                             size)
        except _RESTORE_ERRORS as err:
            detail = str(err)
            if docker is not None:
                try:
                    docker.cleanup()
                except runtime.RuntimeError as problem:
                    detail += f"; {problem}"
            if journal.state["phase"] == "releasing":
                raise MigrationError(f"the Elastic history was restored and verified, but releasing the target "
                                     f"failed: {detail}; run the installer again to finish the release") from err
            raise MigrationError(
                f"history restore failed; the target start guard remains in place: {detail}") from err
    keys = ("route", "indices", "documents", "first", "last", "spaces", "objects", "retention")
    return dict({key: summary[key] for key in keys}, phase="released")
