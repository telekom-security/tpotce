"""Local Elasticsearch snapshot operations and data-preservation checks.

The caller owns the maintenance window, repository mounts and process lifetime.
No operation here stops services, deletes indices or enables productive ingestion.
"""

import http.client
import ipaddress
import json
import re
import time
from typing import Callable, Optional, Sequence, Tuple
import urllib.error
import urllib.parse
import urllib.request


class SnapshotError(Exception):
    """The requested snapshot operation or preservation check did not succeed."""


def _version(value: str) -> Tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise SnapshotError("an exact Elasticsearch runtime version is required")
    return tuple(int(part) for part in value.split("."))


def restore_route(source: str, target: str, created: Sequence[str], *,
                  kibana: bool, intermediate: str) -> Tuple[str, ...]:
    """Select a restore route, never treating snapshot version ranges as runtime versions."""
    before, after, middle = _version(source), _version(target), _version(intermediate)
    if before[0] not in (8, 9) or after[0] not in (8, 9) or before > after:
        raise SnapshotError("unsupported Elasticsearch restore direction")
    for value in created:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{7}", value):
            raise SnapshotError("unknown index creation version")
        major = int(value) // 1000000
        if major < 8 or major > before[0]:
            raise SnapshotError("index creation version requires a separately qualified reindex migration")
    if kibana and before < (8, 19, 0) and after[0] == 9:
        if middle[:2] != (8, 19) or middle < before:
            raise SnapshotError("Kibana migration requires a compatible pinned Elasticsearch 8.19 intermediate")
        return intermediate, target
    return (target,)


def _name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value):
        raise SnapshotError("invalid snapshot or repository name")
    return value


def _indices(values: Sequence[str]) -> list:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise SnapshotError("an explicit list of concrete index names is required")
    names = []
    for value in values:
        if not isinstance(value, str) or value in (".", "..") or \
                not re.fullmatch(r"[a-z0-9.][a-z0-9._+-]*", value):
            raise SnapshotError("index selection must contain concrete names, not wildcards or paths")
        if value in names:
            raise SnapshotError("duplicate index selection")
        names.append(value)
    return names


def _shards(value: object) -> int:
    if not isinstance(value, dict):
        raise SnapshotError("missing shard result")
    numbers = [value.get(key) for key in ("total", "successful", "failed")]
    if any(type(number) is not int or number < 0 for number in numbers):
        raise SnapshotError("invalid shard result")
    total, successful, failed = numbers
    if failed or successful != total:
        raise SnapshotError("not all selected shards succeeded")
    return total


def _feature_indices(info: dict) -> list:
    features = info.get("feature_states")
    if not isinstance(features, list):
        raise SnapshotError("snapshot has no valid feature-state inventory")
    indices = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("feature_name") != "kibana":
            raise SnapshotError("snapshot contains an unselected feature state")
        indices.extend(_indices(feature.get("indices")))
    return _indices(indices)


def _complete(info: dict) -> None:
    if info.get("state") != "SUCCESS":
        raise SnapshotError(f"snapshot is not complete: {info.get('state', 'missing state')}")
    count = _shards(info.get("shards"))
    names = _indices(info.get("indices"))
    if names and count < len(names):
        raise SnapshotError("snapshot is missing primary shards")


def _number(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _recovered(shard: dict) -> tuple:
    stats = shard.get("index")
    stats = stats if isinstance(stats, dict) else {}
    size = stats.get("size") if isinstance(stats.get("size"), dict) else {}
    files = stats.get("files") if isinstance(stats.get("files"), dict) else {}
    return _number(size.get("recovered_in_bytes")), _number(files.get("recovered"))


def _snapshot_progress(status: object):
    """Progress of a running snapshot, or False when Elasticsearch names none."""
    try:
        entry = status["snapshots"][0]
        processed = entry["stats"]["processed"]
        return (_number(entry["shards_stats"]["done"]), _number(processed["file_count"]),
                _number(processed["size_in_bytes"]))
    except (KeyError, IndexError, TypeError):
        return False


def verify_inventory(expected: dict, actual: dict) -> None:
    """Compare frozen data, separately from samples whose search order can change."""
    if not isinstance(expected, dict) or not isinstance(actual, dict) or set(expected) != set(actual):
        raise SnapshotError("restored index set differs from the source")
    for name in expected:
        for key in ("created", "count", "first", "last", "aliases"):
            if key not in expected[name] or key not in actual[name] or expected[name][key] != actual[name][key]:
                raise SnapshotError(f"restored {key} differs for index {name}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SnapshotClient:
    def __init__(self, url: str, timeout: int = 60, wait: int = 1200):
        try:
            parsed = urllib.parse.urlsplit(url)
            local = ipaddress.ip_address(parsed.hostname or "").is_loopback
            port = parsed.port
        except ValueError as err:
            raise SnapshotError("snapshot API must use a loopback address") from err
        if parsed.scheme != "http" or not local or not port or parsed.username is not None or \
                parsed.password is not None or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise SnapshotError("snapshot API must use a plain loopback HTTP endpoint")
        if type(timeout) is not int or timeout < 1 or type(wait) is not int or wait < 1:
            raise SnapshotError("snapshot timeouts must be positive whole seconds")
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.wait = wait
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def request(self, method: str, path: str, body: Optional[dict] = None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            code = err.code
            err.close()
            raise SnapshotError(f"Elasticsearch {method} {path} answered HTTP {code}") from None
        except (OSError, http.client.HTTPException) as err:
            raise SnapshotError(f"Elasticsearch {method} {path} failed: {getattr(err, 'reason', err)}") from None
        except (ValueError, UnicodeError):
            raise SnapshotError(f"Elasticsearch {method} {path} returned invalid JSON") from None

    def _wait(self, check: Callable[[], object], what: str) -> None:
        # check returns True when done, else False or a progress value: wait bounds the time without progress
        deadline = time.monotonic() + self.wait
        last = None
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise SnapshotError(f"{what} timed out; the maintenance hold must remain in place")
            state = check()
            if state is True:
                return
            if state is not False and state != last:
                if last is not None:
                    deadline = now + self.wait
                last = state
            time.sleep(2)

    def version(self) -> str:
        value = self.request("GET", "/")
        try:
            number = value["version"]["number"]
            _version(number)
        except (KeyError, TypeError):
            raise SnapshotError("Elasticsearch did not report its runtime version") from None
        return number

    def pause_ilm(self) -> None:
        result = self.request("POST", "/_ilm/stop")
        if not isinstance(result, dict) or result.get("acknowledged") is not True:
            raise SnapshotError("Elasticsearch did not acknowledge the ILM pause")

        def stopped():
            status = self.request("GET", "/_ilm/status")
            if not isinstance(status, dict) or status.get("operation_mode") not in ("STOPPING", "STOPPED"):
                raise SnapshotError("Elasticsearch did not confirm that ILM is stopping")
            return status["operation_mode"] == "STOPPED"

        self._wait(stopped, "ILM pause")

    def repository(self, name: str, location: str, *, readonly: bool) -> None:
        _name(name)
        if not isinstance(location, str) or not location.startswith("/") or "\0" in location:
            raise SnapshotError("snapshot repository needs an absolute container path")
        result = self.request("PUT", f"/_snapshot/{name}",
                              {"type": "fs", "settings": {"location": location, "readonly": readonly}})
        if not isinstance(result, dict) or result.get("acknowledged") is not True:
            raise SnapshotError("snapshot repository was not acknowledged")
        result = self.request("POST", f"/_snapshot/{name}/_verify")
        if not isinstance(result, dict) or not isinstance(result.get("nodes"), dict) or not result["nodes"]:
            raise SnapshotError("snapshot repository was not verified by any node")

    def info(self, repository: str, name: str) -> dict:
        value = self.request("GET", f"/_snapshot/{_name(repository)}/{_name(name)}")
        found = value.get("snapshots") if isinstance(value, dict) else None
        if not isinstance(found, list) or len(found) != 1 or not isinstance(found[0], dict) or \
                found[0].get("snapshot") != name:
            raise SnapshotError("Elasticsearch did not return the requested snapshot")
        return found[0]

    def snapshot(self, repository: str, name: str, indices: Sequence[str], *, kibana: bool) -> dict:
        selected = _indices(indices)
        path = f"/_snapshot/{_name(repository)}/{_name(name)}"
        result = self.request("PUT", path + "?wait_for_completion=false", {
            "indices": ",".join(selected) or "-*", "partial": False,
            "include_global_state": False, "feature_states": ["kibana"] if kibana else ["none"],
        })
        if not isinstance(result, dict) or result.get("accepted") is not True:
            raise SnapshotError("snapshot creation was not accepted")
        info = {}

        def complete():
            info.clear()
            info.update(self.info(repository, name))
            if info.get("state") in ("IN_PROGRESS", "STARTED"):
                return _snapshot_progress(self.request("GET", path + "/_status"))
            _complete(info)
            return True

        self._wait(complete, "snapshot creation")
        features = _feature_indices(info)
        if bool(features) != kibana or set(info["indices"]) != set(selected) | set(features):
            raise SnapshotError("snapshot index or feature selection differs from the requested scope")
        return info

    def restore(self, repository: str, name: str, indices: Sequence[str], *, kibana: bool) -> dict:
        selected = _indices(indices)
        info = self.info(repository, name)
        _complete(info)
        features = _feature_indices(info)
        if not set(selected).issubset(info["indices"]) or kibana and not features:
            raise SnapshotError("snapshot does not contain the requested indices or Kibana feature state")
        existing = self.request("GET", "/_cat/indices?format=json&expand_wildcards=all&h=index")
        if not isinstance(existing, list) or existing:
            raise SnapshotError("snapshot restore requires an empty target cluster")
        needed = selected + (features if kibana else [])
        if not needed:
            return info
        path = f"/_snapshot/{_name(repository)}/{_name(name)}"
        status = self.request("GET", path + "/_status")
        try:
            entries = status["snapshots"]
            if len(entries) != 1 or entries[0]["snapshot"] != name:
                raise SnapshotError("snapshot shard inventory does not match")
            counts = {}
            for index in needed:
                stats = entries[0]["indices"][index]["shards_stats"]
                total = stats["total"]
                if type(total) is not int or total < 1 or stats["done"] != total:
                    raise SnapshotError("snapshot is missing completed primary shards")
                counts[index] = total
        except (KeyError, TypeError):
            raise SnapshotError("snapshot has no complete per-index shard inventory") from None
        result = self.request("POST", path + "/_restore?wait_for_completion=false", {
            "indices": ",".join(selected) or "-*", "include_global_state": False,
            "feature_states": ["kibana"] if kibana else ["none"],
        })
        if not isinstance(result, dict) or result.get("accepted") is not True:
            raise SnapshotError("snapshot restore was not accepted")

        def recovered():
            progress = []
            recovery = self.request("GET", "/_recovery?active_only=false&expand_wildcards=all")
            if not isinstance(recovery, dict):
                raise SnapshotError("invalid snapshot recovery status")
            done = True
            for index, count in counts.items():
                entry = recovery.get(index, {})
                if not isinstance(entry, dict) or not isinstance(entry.get("shards", []), list):
                    raise SnapshotError("invalid snapshot shard recovery")
                primaries = {}
                for shard in entry.get("shards", []):
                    if not isinstance(shard, dict):
                        raise SnapshotError("invalid snapshot shard recovery")
                    if shard.get("primary") is not True:
                        continue
                    source = shard.get("source")
                    if shard.get("type") != "SNAPSHOT" or not isinstance(source, dict) or \
                            source.get("repository") != repository or source.get("snapshot") != name:
                        raise SnapshotError("primary shard was recovered from a different source")
                    shard_id = shard.get("id")
                    if type(shard_id) is not int or shard_id < 0 or shard_id in primaries:
                        raise SnapshotError("invalid or duplicate recovered shard")
                    primaries[shard_id] = shard.get("stage")
                    progress.append((index, shard_id, shard.get("stage"), _recovered(shard)))
                if len(primaries) != count or any(stage != "DONE" for stage in primaries.values()):
                    done = False
            return True if done else tuple(progress)

        self._wait(recovered, "snapshot recovery")
        return info

    def inventory(self, indices: Sequence[str]) -> dict:
        found = {}
        for index in _indices(indices):
            path = "/" + urllib.parse.quote(index, safe="")
            settings = self.request("GET", path + "/_settings?flat_settings=true")
            try:
                if set(settings) != {index}:
                    raise SnapshotError("index settings did not match the selected index")
                created = settings[index]["settings"]["index.version.created"]
                if not isinstance(created, str) or not re.fullmatch(r"[0-9]{7}", created):
                    raise SnapshotError("unknown index creation version")
                search = self.request("POST", path + "/_search", {
                    "size": 3, "track_total_hits": True, "query": {"match_all": {}},
                    "aggs": {"first": {"min": {"field": "@timestamp"}},
                             "last": {"max": {"field": "@timestamp"}}},
                })
                if search.get("timed_out") is not False:
                    raise SnapshotError("index inventory search timed out")
                _shards(search.get("_shards"))
                count = search["hits"]["total"]
                if count["relation"] != "eq" or type(count["value"]) is not int or count["value"] < 0:
                    raise SnapshotError("index inventory did not return an exact document count")
                bounds = {}
                for key in ("first", "last"):
                    bound = search["aggregations"][key]
                    bounds[key] = None if bound["value"] is None else bound["value_as_string"]
                    if bounds[key] is not None and not isinstance(bounds[key], str):
                        raise SnapshotError("index inventory has invalid time bounds")
                samples = []
                for hit in search["hits"]["hits"]:
                    if not isinstance(hit["_id"], str) or not isinstance(hit["_source"], dict):
                        raise SnapshotError("index inventory has an invalid document sample")
                    samples.append({"id": hit["_id"], "source": hit["_source"]})
                if count["value"] and not samples:
                    raise SnapshotError("nonempty index returned no document samples")
                aliases = self.request("GET", path + "/_alias")
                if set(aliases) != {index} or not isinstance(aliases[index]["aliases"], dict):
                    raise SnapshotError("index inventory has invalid aliases")
                found[index] = dict(created=created, count=count["value"], samples=samples,
                                    aliases=aliases[index]["aliases"], **bounds)
            except (KeyError, TypeError, AttributeError):
                raise SnapshotError(f"Elasticsearch returned an incomplete inventory for {index}") from None
        return found

    def verify_samples(self, inventory: dict) -> None:
        for index in _indices(list(inventory)):
            samples = inventory[index]["samples"]
            if not samples:
                continue
            expected = {sample["id"]: sample["source"] for sample in samples}
            path = "/" + urllib.parse.quote(index, safe="") + "/_mget"
            result = self.request("POST", path, {"ids": list(expected)})
            try:
                docs = result["docs"]
                if len(docs) != len(expected) or any(doc.get("found") is not True for doc in docs):
                    raise SnapshotError(f"restored sample is missing from {index}")
                actual = {doc["_id"]: doc["_source"] for doc in docs}
                if actual != expected:
                    raise SnapshotError(f"restored sample differs for {index}")
            except (KeyError, TypeError, AttributeError):
                raise SnapshotError(f"restored sample check failed for {index}") from None
