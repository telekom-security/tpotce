"""Private, integrity-checked migration packages."""

import datetime
import hashlib
import io
import json
import os
import platform
import re
import shlex
import stat
import struct
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from tpotctl.envfile import EnvFile

FORMAT = "tpot-migration"
SCHEMA = 1
SCOPES = frozenset(("configuration", "identity", "history"))
SOURCE_FIELDS = frozenset((
    "release", "commit", "role", "edition", "os_id", "os_version", "architecture", "userland_bits",
    "elasticsearch_version", "kibana_version", "image_digests", "data_path",
))
MAX_FILES = 10000
MAX_MANIFEST_SIZE = 1024 * 1024
OS_RELEASE_PATH = "/etc/os-release"
_COMMIT = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
_IMAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_IMAGE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,254}\Z")
_ELASTIC_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
_IDENTITY_FILES = (
    "uuid",
    "sensors.json",
    "nginx/conf/nginxpasswd",
    "nginx/conf/nginxpasswd.lost",
    "nginx/conf/lswebpasswd",
    "nginx/conf/lswebpasswd.lost",
    "nginx/cert/nginx.crt",
    "nginx/cert/nginx.key",
    "hive.crt",
)
_IDENTITY_DIRECTORIES = (
    "ciscoasa/identity",
    "conpot/identity",
    "cowrie/keys",
    "endlessh/identity",
    "rdphoneypot/cert",
    "galah/cert",
)


class MigrationError(Exception):
    """A migration package is invalid or could not be written safely."""


@dataclass(frozen=True)
class SourceInventory:
    source: dict
    files: Dict[str, str]
    scope: Tuple[str, ...] = ("configuration", "identity")
    compose: dict = field(default_factory=dict, repr=False)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise MigrationError(f"invalid {field}")
    return value


def source_role(tpot_type: Optional[str], edition: str) -> str:
    """Legacy Mobile used HIVE in its env; the package records its actual role."""
    value = (tpot_type or "HIVE").strip()
    if edition == "MOBILE":
        if value not in ("HIVE", "MOBILE"):
            raise MigrationError("source MOBILE edition must have TPOT_TYPE=HIVE or MOBILE")
        return "MOBILE"
    if value not in ("HIVE", "SENSOR"):
        raise MigrationError("source T-Pot role must be HIVE or SENSOR")
    return value


def _validate_source(source: Mapping[str, object]) -> dict:
    if not isinstance(source, Mapping) or set(source) != SOURCE_FIELDS:
        raise MigrationError("source metadata has missing or unrecognized fields")

    release = _text(source["release"], "source release")
    if release not in ("24.04.1", "24.04.2"):
        raise MigrationError("unsupported source release")
    commit = _text(source["commit"], "source commit")
    if not _COMMIT.fullmatch(commit):
        raise MigrationError("source commit must be a full hexadecimal hash")
    role = _text(source["role"], "source role")
    if role not in ("HIVE", "SENSOR", "MOBILE"):
        raise MigrationError("source role must be HIVE, SENSOR or MOBILE")
    edition = _text(source["edition"], "source edition")
    if (role == "MOBILE") != (edition == "MOBILE"):
        raise MigrationError("source role and edition do not match")
    os_id = _text(source["os_id"], "source OS id")
    os_version = _text(source["os_version"], "source OS version")
    architecture = _text(source["architecture"], "source architecture")
    if architecture not in ("amd64", "arm64"):
        raise MigrationError("source architecture must be amd64 or arm64")
    userland_bits = source["userland_bits"]
    if type(userland_bits) is not int or userland_bits != 64:
        raise MigrationError("source userland must be 64-bit")

    result = {
        "release": release,
        "commit": commit,
        "role": role,
        "edition": edition,
        "os_id": os_id,
        "os_version": os_version,
        "architecture": architecture,
        "userland_bits": userland_bits,
    }
    for field in ("elasticsearch_version", "kibana_version"):
        value = source[field]
        result[field] = None if value is None else _text(value, field.replace("_", " "))

    images = source["image_digests"]
    if not isinstance(images, Mapping) or not images:
        raise MigrationError("source image digests are required")
    validated_images = {}
    for name, digest in images.items():
        if not isinstance(name, str) or not _IMAGE_NAME.fullmatch(name):
            raise MigrationError("invalid image name in source metadata")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise MigrationError(f"invalid image digest for {name}")
        validated_images[name] = digest
    result["image_digests"] = validated_images

    data_path = _text(source["data_path"], "source data path")
    if not os.path.isabs(data_path) or os.path.normpath(data_path) != data_path or data_path == os.path.sep:
        raise MigrationError("source data path must be an absolute normalized path")
    result["data_path"] = data_path
    return result


def _read_source_file(path: str, what: str) -> str:
    handle, _info = _open_regular(path, what)
    try:
        with handle:
            return handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as err:
        raise MigrationError(f"cannot read {what}") from err


def _run_output(command: Sequence[str], *, cwd: Optional[str] = None, timeout: int = 60,
                run: Optional[Callable] = None, what: str) -> str:
    runner = subprocess.run if run is None else run
    try:
        result = runner(list(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        universal_newlines=True, cwd=cwd, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as err:
        raise MigrationError(f"cannot run {what}") from err
    if result.returncode != 0:
        raise MigrationError(f"{what} failed")
    return result.stdout


def _os_release(path: str) -> Tuple[str, str]:
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError) as err:
        raise MigrationError("cannot read host OS metadata") from err
    values = {}
    for line in lines:
        key, separator, raw = line.partition("=")
        if not separator or key not in ("ID", "VERSION_ID"):
            continue
        try:
            parts = shlex.split(raw, comments=False, posix=True)
        except ValueError as err:
            raise MigrationError("host OS metadata is malformed") from err
        if len(parts) != 1:
            raise MigrationError("host OS metadata is malformed")
        values[key] = parts[0]
    os_id = values.get("ID", "").strip()
    version = values.get("VERSION_ID", "").strip()
    if not os_id or not version:
        raise MigrationError("host OS metadata must name ID and VERSION_ID")
    return os_id, version


def _architecture() -> str:
    machine = platform.machine().lower()
    architecture = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if architecture is None or struct.calcsize("P") * 8 != 64:
        raise MigrationError("source host must have amd64 or arm64 64-bit userland")
    return architecture


def _image_metadata(service: str, image: str, *, run: Optional[Callable]) -> Tuple[str, Optional[str]]:
    if not isinstance(image, str) or not _IMAGE_REF.fullmatch(image):
        raise MigrationError(f"invalid image reference for {service}")
    output = _run_output(
        ["docker", "image", "inspect", "--format", "{{json .}}", image],
        run=run, what=f"inspecting the {service} image",
    )
    try:
        value = json.loads(output)
    except ValueError as err:
        raise MigrationError(f"invalid metadata for the {service} image") from err
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict) or not isinstance(value.get("Id"), str) \
            or not _DIGEST.fullmatch(value["Id"]):
        raise MigrationError(f"the {service} image has no valid local image ID")
    version_key = {"elasticsearch": "ES_VER", "kibana": "KB_VER"}.get(service)
    if version_key is None:
        return value["Id"], None
    config = value.get("Config")
    environment = config.get("Env") if isinstance(config, dict) else None
    matches = [item.partition("=")[2] for item in environment
               if isinstance(item, str) and item.startswith(version_key + "=")] if isinstance(environment, list) else []
    if len(matches) != 1 or not _ELASTIC_VERSION.fullmatch(matches[0]):
        raise MigrationError(f"the {service} image does not identify its actual version")
    return value["Id"], matches[0]


def _identity_path(data_path: str, relative: str, *, directory: bool) -> Optional[str]:
    path = data_path
    parts = relative.split(os.sep)
    for index, part in enumerate(parts):
        path = os.path.join(path, part)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as err:
            raise MigrationError("cannot inspect selected identity data") from err
        if stat.S_ISLNK(info.st_mode):
            raise MigrationError("selected identity data cannot contain links")
        is_directory = stat.S_ISDIR(info.st_mode)
        if index < len(parts) - 1 and not is_directory:
            raise MigrationError("selected identity data must use directories")
        if index == len(parts) - 1 and is_directory != directory:
            raise MigrationError(
                "selected identity data must be a directory" if directory
                else "selected identity data must be regular files"
            )
        if index == len(parts) - 1 and not directory and not stat.S_ISREG(info.st_mode):
            raise MigrationError("selected identity data must be regular files")
    return path


def _add_identity_file(data_path: str, relative: str, files: dict) -> None:
    path = _identity_path(data_path, relative, directory=False)
    if path is None:
        return
    files[f"identity/{relative}"] = path


def _walk_identity_directory(data_path: str, relative: str, files: dict) -> None:
    root = _identity_path(data_path, relative, directory=True)
    if root is None:
        return

    def visit(path: str, prefix: str) -> None:
        try:
            with os.scandir(path) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as err:
            raise MigrationError("cannot read selected identity data") from err
        for entry in entries:
            child = os.path.join(path, entry.name)
            logical = f"{prefix}/{entry.name}"
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as err:
                raise MigrationError("cannot inspect selected identity data") from err
            if stat.S_ISLNK(info.st_mode):
                raise MigrationError("selected identity data cannot contain links")
            if stat.S_ISDIR(info.st_mode):
                visit(child, logical)
            elif stat.S_ISREG(info.st_mode):
                files[f"identity/{logical}"] = child
            else:
                raise MigrationError("selected identity data must contain only regular files")

    visit(root, relative)


def inspect_source(repo_dir: str, *, os_release_path: Optional[str] = None,
                   run: Optional[Callable] = None) -> SourceInventory:
    """Inspect an existing T-Pot checkout and inventory only supported configuration/identity files."""
    if not isinstance(repo_dir, str) or not repo_dir:
        raise MigrationError("source checkout path is required")
    root = os.path.abspath(os.path.expanduser(repo_dir))
    try:
        root_info = os.lstat(root)
    except OSError as err:
        raise MigrationError("source checkout cannot be inspected") from err
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise MigrationError("source checkout must be a real directory")
    root = os.path.realpath(root)

    env_path = os.path.join(root, ".env")
    try:
        env_info = os.lstat(env_path)
        if not stat.S_ISREG(env_info.st_mode):
            raise MigrationError("source .env must be a regular file")
        env = EnvFile(env_path).values()
    except MigrationError:
        raise
    except (OSError, UnicodeDecodeError) as err:
        raise MigrationError("source .env cannot be read") from err

    release = _read_source_file(os.path.join(root, "version"), "source release file").strip()
    if release not in ("24.04.1", "24.04.2"):
        raise MigrationError("source release must be 24.04.1 or 24.04.2")
    # an update.sh that stopped half way leaves the checkout of the new release with the .env of the
    # old one: the export would describe a release that never ran
    if env.get("TPOT_VERSION") != release:
        raise MigrationError(
            f"source .env names TPOT_VERSION {env.get('TPOT_VERSION') or '(none)'}, but the checkout is "
            f"{release}; finish the update or go back to the release before, then export again")
    commit = _run_output(
        ["git", "-C", root, "rev-parse", "--verify", "HEAD^{commit}"],
        timeout=10, run=run, what="reading the source Git commit",
    ).strip()
    if not _COMMIT.fullmatch(commit):
        raise MigrationError("source checkout has no valid Git commit")

    compose_value = env.get("TPOT_DOCKER_COMPOSE") or "./docker-compose.yml"
    compose_path = os.path.normpath(os.path.join(root, os.path.expanduser(compose_value)))
    compose_handle, _compose_info = _open_regular(compose_path, "active Compose file")
    compose_handle.close()

    from tpotctl import ops
    edition = ops.edition(root)
    if edition in ("none", "unknown"):
        raise MigrationError("source checkout has no recognized T-Pot edition")

    compose_output = _run_output(
        ["docker", "compose", "--project-directory", root, "--env-file", env_path,
         "-f", compose_path, "config", "--format", "json"],
        cwd=root, run=run, what="reading the source Compose configuration",
    )
    try:
        config = json.loads(compose_output)
    except ValueError as err:
        raise MigrationError("source Compose configuration did not return valid JSON") from err
    services = config.get("services") if isinstance(config, dict) else None
    if not isinstance(services, dict) or "tpotinit" not in services:
        raise MigrationError("source Compose configuration has no tpotinit service")
    tpotinit = services["tpotinit"]
    volumes = tpotinit.get("volumes") if isinstance(tpotinit, dict) else None
    data_mounts = [volume for volume in volumes if isinstance(volume, dict) and volume.get("target") == "/data"] \
        if isinstance(volumes, list) else []
    if len(data_mounts) != 1 or data_mounts[0].get("type") != "bind" \
            or not isinstance(data_mounts[0].get("source"), str):
        raise MigrationError("source tpotinit must bind-mount its data path at /data")
    data_path = data_mounts[0]["source"]
    if not os.path.isabs(data_path):
        data_path = os.path.join(root, data_path)
    data_path = os.path.realpath(data_path)

    image_digests = {}
    versions = {"elasticsearch": None, "kibana": None}
    for service in ("tpotinit", "elasticsearch", "kibana"):
        definition = services.get(service)
        if definition is None:
            continue
        if not isinstance(definition, dict):
            raise MigrationError(f"source Compose service {service} is invalid")
        digest, version = _image_metadata(service, definition.get("image"), run=run)
        image_digests[service] = digest
        if service in versions:
            versions[service] = version
    if "tpotinit" not in image_digests:
        raise MigrationError("source tpotinit image is unavailable")

    role = source_role(env.get("TPOT_TYPE"), edition)
    host_os, host_version = _os_release(OS_RELEASE_PATH if os_release_path is None else os_release_path)
    architecture = _architecture()

    source = {
        "release": release,
        "commit": commit,
        "role": role,
        "edition": edition,
        "os_id": host_os,
        "os_version": host_version,
        "architecture": architecture,
        "userland_bits": 64,
        "elasticsearch_version": versions["elasticsearch"],
        "kibana_version": versions["kibana"],
        "image_digests": image_digests,
        "data_path": data_path,
    }
    _validate_source(source)

    files = {
        "configuration/.env": env_path,
        "configuration/compose.yml": compose_path,
    }
    for relative in _IDENTITY_FILES:
        _add_identity_file(data_path, relative, files)
    for relative in _IDENTITY_DIRECTORIES:
        _walk_identity_directory(data_path, relative, files)
    if len(files) > MAX_FILES:
        raise MigrationError("source inventory contains too many files")
    return SourceInventory(source, files, compose=config)


def _validate_scope(scope: Sequence[str]) -> list:
    if isinstance(scope, (str, bytes)) or not isinstance(scope, Sequence) or not scope:
        raise MigrationError("scope must be a non-empty list")
    values = list(scope)
    if any(not isinstance(value, str) or value not in SCOPES for value in values):
        raise MigrationError("scope contains an unsupported value")
    if len(set(values)) != len(values):
        raise MigrationError("scope contains duplicates")
    return values


def _logical_path(value: object, scope: Sequence[str]) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise MigrationError("payload path must be a relative POSIX path")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts) or any(ord(char) < 32 for char in value):
        raise MigrationError("payload path contains an unsafe component")
    if parts[0] not in scope:
        raise MigrationError("payload path does not match the selected scope")
    return value


def _validate_manifest(manifest: object) -> dict:
    required = {"format", "schema", "created_at", "source", "scope", "files"}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise MigrationError("migration manifest has missing or unrecognized fields")
    if manifest["format"] != FORMAT or type(manifest["schema"]) is not int or manifest["schema"] != SCHEMA:
        raise MigrationError("unsupported migration manifest format")

    created_at = _text(manifest["created_at"], "manifest timestamp")
    if not created_at.endswith("Z"):
        raise MigrationError("manifest timestamp must be UTC")
    try:
        parsed = datetime.datetime.fromisoformat(created_at[:-1] + "+00:00")
    except ValueError as err:
        raise MigrationError("invalid manifest timestamp") from err
    if parsed.utcoffset() != datetime.timedelta(0):
        raise MigrationError("manifest timestamp must be UTC")

    source = _validate_source(manifest["source"])
    scope = _validate_scope(manifest["scope"])
    files = manifest["files"]
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise MigrationError("manifest file list is empty or too large")

    seen = set()
    validated_files = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise MigrationError("invalid file record in migration manifest")
        path = _logical_path(item["path"], scope)
        if path in seen:
            raise MigrationError("duplicate payload path in migration manifest")
        seen.add(path)
        size = item["size"]
        digest = item["sha256"]
        if type(size) is not int or size < 0:
            raise MigrationError(f"invalid size for payload {path}")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise MigrationError(f"invalid SHA-256 for payload {path}")
        validated_files.append({"path": path, "size": size, "sha256": digest})

    return {
        "format": FORMAT,
        "schema": SCHEMA,
        "created_at": created_at,
        "source": source,
        "scope": scope,
        "files": validated_files,
    }


def _open_regular(path: str, what: str):
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
    except OSError as err:
        raise MigrationError(f"cannot open {what}: {err}") from err
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise MigrationError(f"{what} must be a regular file")
        return os.fdopen(fd, "rb"), info
    except (OSError, MigrationError):
        os.close(fd)
        raise


def _hash_file(path: str) -> Tuple[int, str]:
    handle, initial = _open_regular(path, "payload source")
    digest = hashlib.sha256()
    size = 0
    with handle:
        try:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
            final = os.fstat(handle.fileno())
        except OSError as err:
            raise MigrationError(f"cannot read payload source: {err}") from err
    if size != initial.st_size or final.st_size != initial.st_size \
            or final.st_mtime_ns != initial.st_mtime_ns:
        raise MigrationError("payload source changed while it was being read")
    return size, digest.hexdigest()


def _private_directory(path: str, *, create: bool) -> None:
    if create:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError as err:
            raise MigrationError(f"cannot create private migration directory: {err}") from err
    try:
        info = os.lstat(path)
    except OSError as err:
        raise MigrationError(f"cannot inspect private migration directory: {err}") from err
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise MigrationError("migration directory must be a directory owned by the current user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        try:
            os.chmod(path, 0o700)
        except OSError as err:
            raise MigrationError(f"cannot protect migration directory: {err}") from err


def _private_file(path: str, what: str) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as err:
        raise MigrationError(f"cannot inspect {what}: {err}") from err
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 \
            or info.st_mode & 0o077:
        raise MigrationError(f"{what} must be a private regular file owned by the current user")
    return info


def _fsync_directory(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as err:
        raise MigrationError(f"cannot sync migration directory: {err}") from err


def _write_private(path: str, data: bytes, *, mode: int = 0o600) -> None:
    parent = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.link(temporary, path)
        os.unlink(temporary)
        _fsync_directory(parent)
    except FileExistsError as err:
        raise MigrationError(f"migration destination already exists: {path}") from err
    except OSError as err:
        raise MigrationError(f"cannot write migration file: {err}") from err
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class _HashingReader:

    def __init__(self, handle):
        self.handle = handle
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.handle.read(size)
        self.digest.update(chunk)
        self.size += len(chunk)
        return chunk


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.size = size
    info.mode = 0o600
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    return info


def _manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def create_package(destination: str, source: Mapping[str, object], scope: Sequence[str],
                   files: Mapping[str, str]) -> dict:
    """Write a new private tar package and return its verified manifest."""
    source_info = _validate_source(source)
    selected = _validate_scope(scope)
    if not isinstance(files, Mapping) or not files or len(files) > MAX_FILES:
        raise MigrationError("payload file list is empty or too large")
    if not isinstance(destination, str) or not destination:
        raise MigrationError("invalid package destination")
    destination = os.path.abspath(destination)
    parent = os.path.dirname(destination)
    if not os.path.isdir(parent) or os.path.islink(parent):
        raise MigrationError("package destination directory must exist and must not be a symlink")
    try:
        os.lstat(destination)
    except FileNotFoundError:
        pass
    except OSError as err:
        raise MigrationError(f"cannot inspect package destination: {err}") from err
    else:
        raise MigrationError("package destination already exists")

    file_sources = {}
    records = []
    for logical, path in files.items():
        logical = _logical_path(logical, selected)
        if logical in file_sources:
            raise MigrationError("duplicate payload path")
        if not isinstance(path, str) or not path:
            raise MigrationError(f"invalid source path for payload {logical}")
        file_sources[logical] = path
        size, digest = _hash_file(path)
        records.append({"path": logical, "size": size, "sha256": digest})
    records.sort(key=lambda item: item["path"])

    manifest = _validate_manifest({
        "format": FORMAT,
        "schema": SCHEMA,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": source_info,
        "scope": selected,
        "files": records,
    })
    manifest_data = _manifest_bytes(manifest)
    if len(manifest_data) > MAX_MANIFEST_SIZE:
        raise MigrationError("migration manifest is too large")

    try:
        fd, temporary = tempfile.mkstemp(prefix=".tpot-migration-", suffix=".tmp", dir=parent)
    except OSError as err:
        raise MigrationError(f"cannot create private package temporary file: {err}") from err

    try:
        with os.fdopen(fd, "w+b") as output:
            os.fchmod(output.fileno(), 0o600)
            with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
                archive.addfile(_tar_info("manifest.json", len(manifest_data)), io.BytesIO(manifest_data))
                for record in records:
                    logical = record["path"]
                    handle, initial = _open_regular(file_sources[logical], f"payload source {logical}")
                    with handle:
                        if initial.st_size != record["size"]:
                            raise MigrationError(f"payload source changed while creating package: {logical}")
                        reader = _HashingReader(handle)
                        archive.addfile(_tar_info(f"payload/{logical}", record["size"]), reader)
                        final = os.fstat(handle.fileno())
                    if reader.size != record["size"] or reader.digest.hexdigest() != record["sha256"] \
                            or final.st_size != initial.st_size or final.st_mtime_ns != initial.st_mtime_ns:
                        raise MigrationError(f"payload source changed while creating package: {logical}")
            output.flush()
            os.fsync(output.fileno())

        verified = validate_package(temporary)
        if verified != manifest:
            raise MigrationError("written migration package did not match its manifest")
        try:
            os.link(temporary, destination)
        except FileExistsError as err:
            raise MigrationError("package destination already exists") from err
        except OSError as err:
            raise MigrationError(f"cannot publish migration package: {err}") from err
        try:
            os.unlink(temporary)
        except OSError as err:
            raise MigrationError(f"package was published but its temporary file could not be removed: {err}") from err
        return manifest
    except OSError as err:
        raise MigrationError(f"cannot write migration package: {err}") from err
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _member_is_private_regular(member: tarfile.TarInfo) -> bool:
    # tarfile stores the size of a member of 8 GiB or more as a pax record; it is checked against the manifest
    return member.isfile() and stat.S_IMODE(member.mode) == 0o600 and member.uid == 0 and member.gid == 0 \
        and not (set(member.pax_headers) - {"path", "size"})


def _read_manifest_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> dict:
    if member.name != "manifest.json" or not _member_is_private_regular(member) \
            or member.size > MAX_MANIFEST_SIZE:
        raise MigrationError("invalid migration manifest archive member")
    stream = archive.extractfile(member)
    if stream is None:
        raise MigrationError("migration manifest cannot be read")
    with stream:
        raw = stream.read(MAX_MANIFEST_SIZE + 1)
    if len(raw) != member.size or len(raw) > MAX_MANIFEST_SIZE:
        raise MigrationError("invalid migration manifest size")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, ValueError) as err:
        raise MigrationError("migration manifest is not valid UTF-8 JSON") from err
    return _validate_manifest(value)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def validate_package(path: str) -> dict:
    """Validate a migration archive without extracting any member."""
    if not isinstance(path, str) or not path:
        raise MigrationError("invalid migration package path")
    handle, package_info = _open_regular(path, "migration package")
    if package_info.st_uid != os.getuid() or package_info.st_nlink != 1 \
            or package_info.st_mode & 0o077 or not package_info.st_mode & stat.S_IRUSR:
        handle.close()
        raise MigrationError("migration package must be a private regular file owned by the current user")

    try:
        with handle:
            with tarfile.open(fileobj=handle, mode="r:") as archive:
                first = archive.next()
                if first is None:
                    raise MigrationError("migration package is empty")
                manifest = _read_manifest_member(archive, first)
                expected = {f"payload/{item['path']}": item for item in manifest["files"]}
                seen = set()
                while True:
                    member = archive.next()
                    if member is None:
                        break
                    name = member.name
                    if name in seen:
                        raise MigrationError("duplicate member in migration package")
                    seen.add(name)
                    record = expected.get(name)
                    if record is None or not _member_is_private_regular(member):
                        raise MigrationError("unexpected or unsafe member in migration package")
                    if member.size != record["size"]:
                        raise MigrationError(f"payload size does not match manifest: {record['path']}")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise MigrationError(f"payload cannot be read: {record['path']}")
                    digest = hashlib.sha256()
                    size = 0
                    with stream:
                        while True:
                            chunk = stream.read(1024 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            digest.update(chunk)
                    if size != record["size"] or digest.hexdigest() != record["sha256"]:
                        raise MigrationError(f"payload integrity check failed: {record['path']}")
                if seen != set(expected):
                    raise MigrationError("migration package is missing payload members")
                return manifest
    except (OSError, EOFError, tarfile.TarError) as err:
        raise MigrationError(f"cannot read migration package: {err}") from err
