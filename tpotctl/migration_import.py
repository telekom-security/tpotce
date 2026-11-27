"""Stage and apply a private migration package on a fresh Debian target."""

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Callable, Dict, Optional, Tuple

from tpotctl import installer, migration, ops
from tpotctl.envfile import EnvError, EnvFile
from tpotctl.migration import _fsync_directory, _private_directory, _private_file, _write_private

PACKAGE_NAME = "package.tar"
PENDING_NAME = "pending.json"
PREPARED_NAME = "prepared"
_PENDING_FORMAT = "tpot-migration-pending"
_PENDING_SCHEMA = 1
_TARGET_OWNED_KEYS = frozenset((
    "TPOT_TYPE", "TPOT_DOCKER_COMPOSE", "TPOT_VERSION", "TPOT_REPO", "TPOT_DATA_PATH",
))
_REQUIRED_CONFIGURATION = frozenset(("configuration/.env", "configuration/compose.yml"))
_HISTORY_FILES = frozenset(("history/inventory.json", "history/repository.tar"))
_HISTORY_REPORT = frozenset(("route", "indices", "documents", "first", "last", "spaces", "objects",
                             "ignored_indices", "storage", "retention"))


def default_state_dir() -> str:
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "tpotce", "migration")


def _migration_error(message: str) -> migration.MigrationError:
    return migration.MigrationError(message)


def _target_checkout(target_dir: str) -> str:
    if not isinstance(target_dir, str) or not target_dir:
        raise _migration_error("target checkout path is invalid")
    target_dir = os.path.abspath(target_dir)
    try:
        info = os.lstat(target_dir)
    except OSError as err:
        raise _migration_error(f"cannot inspect target checkout: {err}") from err
    if not stat.S_ISDIR(info.st_mode):
        raise _migration_error("target checkout must be a real directory")
    version_path = os.path.join(target_dir, "version")
    try:
        handle, _version_info = migration._open_regular(version_path, "target release file")
        with handle:
            version = handle.read(64).decode("ascii").strip()
    except (OSError, UnicodeDecodeError, migration.MigrationError) as err:
        raise _migration_error("target checkout has no valid release file") from err
    target_release = ops.tpot_version()
    if version != target_release:
        raise _migration_error(f"migration import requires a {target_release} target checkout")
    try:
        env_handle, _env_info = migration._open_regular(os.path.join(target_dir, "env.example"),
                                                        "target environment template")
        env_handle.close()
    except (OSError, migration.MigrationError) as err:
        raise _migration_error("target checkout has no valid env.example") from err
    for relative in ("compose", "tpotctl"):
        path = os.path.join(target_dir, relative)
        if not os.path.isdir(path) or os.path.islink(path):
            raise _migration_error(f"target checkout is missing {relative}")
    return target_dir


def _architecture() -> str:
    machine = platform.machine().lower()
    value = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if value is None:
        raise _migration_error("target architecture must be amd64 or arm64")
    return value


def _target_data_path(target_dir: str, env_path: Optional[str] = None) -> str:
    config = EnvFile(env_path or os.path.join(target_dir, "env.example"))
    value = config.get("TPOT_DATA_PATH")
    if not value or "\0" in value or "$" in value or value == "~":
        raise _migration_error("target TPOT_DATA_PATH must be a concrete path")
    path = os.path.realpath(value if os.path.isabs(value) else os.path.join(target_dir, value))
    if path == os.path.sep or path == target_dir:
        raise _migration_error("target TPOT_DATA_PATH is unsafe for a fresh import")
    return path


def _preflight_target(target_dir: str, source: dict, *, allow_applied: bool = False) -> str:
    host = installer.os_release()
    pi_os = installer.is_raspberry_pi_os()
    for check in (installer.check_distro(host, pi_os), installer.check_arch(raspberry_pi_os=pi_os),
                  installer.check_root()):
        if check.state != "ok":
            raise _migration_error(check.detail)
    if source["architecture"] != _architecture():
        raise _migration_error("source and target architectures do not match")
    if pi_os and source["role"] != "MOBILE":
        raise _migration_error("Raspberry Pi OS accepts only a MOBILE source")
    if installer.installed() and not allow_applied:
        raise _migration_error("T-Pot is already installed on the target")
    for relative in (".env", "docker-compose.yml"):
        path = os.path.join(target_dir, relative)
        if os.path.lexists(path) and not allow_applied:
            raise _migration_error(f"target already has {relative}; migration requires a fresh installation")
    data_path = _target_data_path(target_dir)
    if os.path.lexists(data_path) and not allow_applied:
        raise _migration_error("target data path already exists; migration requires an empty target")
    parent = data_path
    while not os.path.exists(parent):
        parent = os.path.dirname(parent)
    if not os.path.isdir(parent) or not os.access(parent, os.W_OK | os.X_OK):
        raise _migration_error("target data path is not writable by this user; choose a writable target path")
    if parent != data_path:
        # the same rule as the claim of the restore, which creates the data folder there
        info = os.lstat(parent)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise _migration_error("target data path must be created in a folder this user owns and others "
                                   f"cannot write: {parent}")
    return data_path


def _digest(path: str) -> str:
    _size, value = migration._hash_file(path)
    return value


def _pending_data(manifest: dict, package_hash: str) -> dict:
    source = manifest["source"]
    return {
        "format": _PENDING_FORMAT,
        "schema": _PENDING_SCHEMA,
        "package_sha256": package_hash,
        "source": {
            "release": source["release"],
            "commit": source["commit"],
            "role": source["role"],
            "architecture": source["architecture"],
        },
    }


def _check_scope(manifest: dict) -> None:
    if not {"configuration", "identity"}.issubset(manifest["scope"]):
        raise _migration_error("migration package must include configuration and identity")
    files = {item["path"] for item in manifest["files"]}
    if not _REQUIRED_CONFIGURATION.issubset(files):
        raise _migration_error("migration package is missing its configuration files")
    if "history" in manifest["scope"]:
        if {path for path in files if path.startswith("history/")} != _HISTORY_FILES:
            raise _migration_error("history package must contain its inventory and snapshot repository")
        # without the source uuid tpotinit would treat the restored Kibana as a fresh install
        if "identity/uuid" not in files:
            raise _migration_error("history package must include the source T-Pot identity")


def _read_pending(target_dir: str, state_dir: str) -> Tuple[dict, dict, str]:
    _target_checkout(target_dir)
    _private_directory(state_dir, create=False)
    marker_path = os.path.join(state_dir, PENDING_NAME)
    package_path = os.path.join(state_dir, PACKAGE_NAME)
    _private_file(marker_path, "pending marker")
    _private_file(package_path, "staged migration package")
    try:
        with open(marker_path, encoding="utf-8") as handle:
            marker = json.load(handle)
    except (OSError, UnicodeDecodeError, ValueError) as err:
        raise _migration_error("pending migration marker is invalid") from err
    required = {"format", "schema", "package_sha256", "source"}
    if not isinstance(marker, dict) or set(marker) != required or marker["format"] != _PENDING_FORMAT \
            or marker["schema"] != _PENDING_SCHEMA or not isinstance(marker["source"], dict):
        raise _migration_error("pending migration marker is invalid")
    if not isinstance(marker["package_sha256"], str) or len(marker["package_sha256"]) != 64:
        raise _migration_error("pending migration marker has an invalid package hash")
    package_hash = _digest(package_path)
    if package_hash != marker["package_sha256"]:
        raise _migration_error("staged migration package does not match its pending marker")
    manifest = migration.validate_package(package_path)
    if marker != _pending_data(manifest, package_hash):
        raise _migration_error("pending migration marker does not match the staged package")
    _check_scope(manifest)
    return marker, manifest, package_path


def _copy_package(source_path: str, destination: str) -> None:
    source, initial = migration._open_regular(source_path, "migration package")
    fd, temporary = tempfile.mkstemp(prefix=".package-", dir=os.path.dirname(destination))
    try:
        with source, os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), 0o600)
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
            final = os.fstat(source.fileno())
        if initial.st_size != final.st_size or initial.st_mtime_ns != final.st_mtime_ns:
            raise _migration_error("migration package changed while it was being staged")
        migration.validate_package(temporary)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if _digest(destination) != _digest(temporary):
                raise _migration_error("a different migration package is already staged")
        _fsync_directory(os.path.dirname(destination))
    except OSError as err:
        raise _migration_error(f"cannot stage migration package: {err}") from err
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def stage_import(package_path: str, target_dir: str, state_dir: str) -> dict:
    """Validate a package and publish a private pending marker without changing the checkout."""
    manifest = migration.validate_package(package_path)
    source = manifest["source"]
    _check_scope(manifest)
    if "history" in manifest["scope"]:
        from tpotctl import migration_history

        migration_history.read_history(manifest, package_path)
    target_dir = _target_checkout(target_dir)
    data_path = _preflight_target(target_dir, source)
    state_dir = os.path.abspath(state_dir)
    if os.path.commonpath((target_dir, state_dir)) == target_dir:
        raise _migration_error("migration state must be stored outside the target checkout")
    if os.path.lexists(state_dir):
        _private_directory(state_dir, create=False)
    else:
        os.makedirs(os.path.dirname(state_dir), mode=0o700, exist_ok=True)
        _private_directory(state_dir, create=True)

    package_destination = os.path.join(state_dir, PACKAGE_NAME)
    marker_path = os.path.join(state_dir, PENDING_NAME)
    source_hash = _digest(package_path)
    if os.path.lexists(marker_path):
        marker, staged, _path = _read_pending(target_dir, state_dir)
        if marker["package_sha256"] != source_hash or staged != manifest:
            raise _migration_error("a different migration is already pending")
    else:
        if os.path.lexists(package_destination):
            _private_file(package_destination, "staged migration package")
            if _digest(package_destination) != source_hash:
                raise _migration_error("a different migration package is already staged")
        else:
            _copy_package(package_path, package_destination)
        staged_hash = _digest(package_destination)
        marker = _pending_data(manifest, staged_hash)
        _write_private(marker_path, json.dumps(marker, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return {
        "source_role": source["role"],
        "source_release": source["release"],
        "package_sha256": source_hash,
        "target_data_path": data_path,
    }


def pending_source(target_dir: str, state_dir: Optional[str] = None) -> dict:
    """Read the verified source metadata needed by install.sh before its edition questions."""
    state_dir = os.path.abspath(state_dir or default_state_dir())
    target_dir = _target_checkout(target_dir)
    marker, manifest, _package = _read_pending(target_dir, state_dir)
    prepared_path = os.path.join(state_dir, PREPARED_NAME)
    if not os.path.lexists(prepared_path):
        _preflight_target(target_dir, manifest["source"])
        return manifest["source"]
    prepared, report = _load_prepared(state_dir, marker)
    data_path = _preflight_target(target_dir, manifest["source"], allow_applied=True)
    if report["target_data_path"] != data_path:
        raise _migration_error("target data path changed after import preparation")
    _precheck_destination(os.path.join(prepared, "env"), os.path.join(target_dir, ".env"), environment=True)
    _precheck_destination(os.path.join(prepared, "compose"),
                          os.path.join(target_dir, "docker-compose.yml"))
    _check_data_tree(data_path, prepared, report, _claimed_target(target_dir, state_dir, manifest, marker))
    for relative in sorted(path for path in report["files"] if path.startswith("identity/")):
        destination = os.path.join(data_path, relative[len("identity/"):])
        _precheck_destination(os.path.join(prepared, relative), destination)
    return manifest["source"]


def _extract_member(archive: tarfile.TarFile, name: str, path: str) -> None:
    member = archive.getmember(name)
    stream = archive.extractfile(member)
    if stream is None:
        raise _migration_error(f"migration package payload cannot be read: {name}")
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with stream, os.fdopen(fd, "wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except OSError as err:
        raise _migration_error(f"cannot prepare migration payload: {err}") from err


def _compose_adoption(target_dir: str, source_compose: str, output: str, role: str,
                      run: Callable) -> list:
    command = [
        "python3", os.path.join(target_dir, "compose", "customizer.py"),
        "--adopt", source_compose, "-o", output, "--report",
    ]
    if role in ("HIVE", "SENSOR"):
        command.extend(("--tpot-type", role))
    try:
        result = run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120,
                     cwd=target_dir)
    except (OSError, subprocess.SubprocessError) as err:
        raise _migration_error("the target Compose customizer could not be run") from err
    if result.returncode != 0:
        raise _migration_error(f"the target Compose customizer could not adopt the package (exit {result.returncode})")
    report = []
    for line in result.stdout.splitlines():
        label, separator, detail = line.partition(": ")
        if separator and label in ("new", "dropped", "moved", "port") and detail:
            report.append(f"{label}: {detail}")
    return report


def _prepare_report(target_dir: str, manifest: dict, package_path: str, work: str, run: Callable,
                    state_dir: str) -> dict:
    source = manifest["source"]
    role = source["role"]
    with_history = "history" in manifest["scope"]
    source_env = os.path.join(work, "source.env")
    source_compose = os.path.join(work, "source-compose.yml")
    output_compose = os.path.join(work, "compose")
    files = {item["path"] for item in manifest["files"]}
    with tarfile.open(package_path, "r:") as archive:
        _extract_member(archive, "payload/configuration/.env", source_env)
        _extract_member(archive, "payload/configuration/compose.yml", source_compose)
        identities = sorted(path for path in files if path.startswith("identity/"))
        for relative in identities:
            _extract_member(archive, f"payload/{relative}", os.path.join(work, relative))

    try:
        source_values = EnvFile(source_env).values()
        try:
            matches = migration.source_role(source_values.get("TPOT_TYPE"), source["edition"]) == role
        except migration.MigrationError:
            matches = False
        if not matches:
            raise _migration_error("source configuration role does not match its package metadata")
        target_env_path = os.path.join(target_dir, "env.example")
        target_env = EnvFile(target_env_path)
        if role == "MOBILE":
            shutil.copyfile(os.path.join(target_dir, "compose", "mobile.yml"), output_compose)
            os.chmod(output_compose, 0o600)
            compose_report = ["MOBILE uses the current built-in Compose; source Compose customizations are not carried."]
        else:
            customizer = os.path.join(target_dir, "compose", "customizer.py")
            if not os.path.isfile(customizer):
                raise _migration_error("target checkout has no Compose customizer")
            compose_report = _compose_adoption(target_dir, source_compose, output_compose, role, run)
            os.chmod(output_compose, 0o600)
        generated = os.path.join(target_dir, "tpotctl", "generated.py")
        if os.path.isfile(generated):
            try:
                stamped = subprocess.run(
                    ["python3", generated, "stamp-compose", "--file", output_compose, "--root", target_dir],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, cwd=target_dir,
                )
            except (OSError, subprocess.SubprocessError) as err:
                raise _migration_error("the target Compose file could not be stamped") from err
            if stamped.returncode != 0:
                detail = stamped.stderr.strip().splitlines()
                suffix = f": {detail[-1]}" if detail else f" (exit {stamped.returncode})"
                raise _migration_error(f"the target Compose file could not be stamped{suffix}")

        from tpotctl import envschema

        schema = envschema.load_schema()
        services = envschema.compose_services(output_compose)
        defaults = {key: rule.default for key, rule in schema.items()}
        values = target_env.values()
        values["TPOT_TYPE"] = role
        target_env.set("TPOT_TYPE", role)
        if role == "HIVE" and "nginx" in services:
            web_user = source_values.get("WEB_USER", "")
            web_user_rule = schema.get("WEB_USER")
            source_values_for_check = dict(values, WEB_USER=web_user)
            if not web_user or web_user_rule is None or \
                    not envschema.applies(web_user_rule, source_values_for_check, services) or \
                    any(problem.level == "error" for problem in
                        envschema.check(web_user_rule, source_values_for_check, defaults)):
                raise _migration_error("source HIVE WEB_USER is missing or invalid")
        skipped = []
        for key, value in source_values.items():
            rule = schema.get(key)
            if key in _TARGET_OWNED_KEYS or rule is None or not value:
                if key not in _TARGET_OWNED_KEYS and value and rule is None:
                    skipped.append(key)
                continue
            if not rule.editable and not rule.secret:
                skipped.append(key)
                continue
            candidate = dict(values)
            candidate[key] = value
            if not envschema.applies(rule, candidate, services) or \
                    any(problem.level == "error" for problem in envschema.check(rule, candidate, defaults)):
                skipped.append(key)
                continue
            target_env.set(key, value)
            values[key] = value
        # restored Kibana objects stay as they are unless the source chose a mode, as update.sh does
        carried = source_values.get("TPOT_KIBANA_OBJECTS")
        if with_history and (not carried or values.get("TPOT_KIBANA_OBJECTS") != carried):
            target_env.set("TPOT_KIBANA_OBJECTS", "keep")
    except (OSError, UnicodeError, EnvError, ValueError) as err:
        if isinstance(err, migration.MigrationError):
            raise
        raise _migration_error(f"migration configuration could not be prepared: {err}") from err

    data_path = _target_data_path(target_dir, target_env_path)
    env_output = target_env.text().encode("utf-8")
    with open(os.path.join(work, "env"), "xb") as handle:
        os.chmod(handle.name, 0o600)
        handle.write(env_output)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.unlink(source_env)
        os.unlink(source_compose)
    except OSError as err:
        raise _migration_error(f"cannot remove temporary migration configuration: {err}") from err

    prepared_files = {"env": _digest(os.path.join(work, "env")),
                      "compose": _digest(output_compose)}
    for relative in identities:
        prepared_files[relative] = _digest(os.path.join(work, relative))
    report = {
        "source_role": role,
        "target_data_path": data_path,
        "compose_report": compose_report,
        "settings_skipped": sorted(set(skipped)),
        "files": prepared_files,
    }
    if with_history:
        from tpotctl import migration_history

        report["history"] = migration_history.prepare_history(
            target_dir, state_dir, manifest, package_path, env_path=os.path.join(work, "env"))
    with open(os.path.join(work, "prepared.json"), "x", encoding="utf-8") as handle:
        os.chmod(handle.name, 0o600)
        json.dump(report, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    return report


def _load_prepared(state_dir: str, marker: dict) -> Tuple[str, dict]:
    prepared = os.path.join(state_dir, PREPARED_NAME)
    _private_directory(prepared, create=False)
    manifest_path = os.path.join(prepared, "prepared.json")
    _private_file(manifest_path, "prepared migration manifest")
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, UnicodeDecodeError, ValueError) as err:
        raise _migration_error("prepared migration metadata is invalid") from err
    if not isinstance(report, dict) or not isinstance(report.get("files"), dict) \
            or report.get("source_role") not in ("HIVE", "SENSOR", "MOBILE"):
        raise _migration_error("prepared migration metadata is invalid")
    if any(not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64
           for path, digest in report["files"].items()):
        raise _migration_error("prepared migration metadata is invalid")
    for relative, digest in report["files"].items():
        if relative not in ("env", "compose") and not relative.startswith("identity/"):
            raise _migration_error("prepared migration metadata contains an unsafe path")
        if relative.startswith("identity/"):
            migration._logical_path(relative, ("identity",))
        path = os.path.join(prepared, relative)
        _private_file(path, f"prepared migration payload {relative}")
        if _digest(path) != digest:
            raise _migration_error(f"prepared migration payload changed: {relative}")
    package = migration.validate_package(os.path.join(state_dir, PACKAGE_NAME))
    expected_files = {"env", "compose"} | {
        item["path"] for item in package["files"] if item["path"].startswith("identity/")
    }
    if set(report["files"]) != expected_files:
        raise _migration_error("prepared migration metadata does not match the package")
    history = report.get("history")
    if ("history" in package["scope"]) != (history is not None) \
            or history is not None and (not isinstance(history, dict) or set(history) != _HISTORY_REPORT):
        raise _migration_error("prepared migration history does not match the package")
    expected_role = marker["source"]["role"]
    if report["source_role"] != expected_role:
        raise _migration_error("prepared migration role does not match the pending package")
    return prepared, report


def prepare_import(target_dir: str, state_dir: str, run: Callable = subprocess.run) -> dict:
    """Verify a pending package and prepare new target files in private staging."""
    target_dir = _target_checkout(target_dir)
    marker, manifest, package_path = _read_pending(target_dir, state_dir)
    if os.path.lexists(os.path.join(state_dir, PREPARED_NAME)):
        _prepared, report = _load_prepared(state_dir, marker)
        return {key: value for key, value in report.items() if key != "files"}
    data_path = _preflight_target(target_dir, manifest["source"])
    work = tempfile.mkdtemp(prefix=".prepared-", dir=state_dir)
    try:
        report = _prepare_report(target_dir, manifest, package_path, work, run, state_dir)
        if report["target_data_path"] != data_path:
            raise _migration_error("target data path changed while preparing the import")
        os.rename(work, os.path.join(state_dir, PREPARED_NAME))
        _fsync_directory(state_dir)
        return {key: value for key, value in report.items() if key != "files"}
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _same_file(source: str, destination: str, *, environment: bool = False) -> bool:
    try:
        info = os.lstat(destination)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return False
        if environment:
            expected = EnvFile(source).values()
            actual = EnvFile(destination).values()
            expected_auto_updates = expected.pop("TPOT_AUTO_UPDATES", None)
            actual_auto_updates = actual.pop("TPOT_AUTO_UPDATES", None)
            return expected == actual and actual_auto_updates in (expected_auto_updates, "DISABLED")
        source_hash = _digest(source)
        destination_hash = _digest(destination)
    except (FileNotFoundError, EnvError, UnicodeError, migration.MigrationError):
        return False
    return source_hash == destination_hash


def _precheck_destination(source: str, destination: str, *, environment: bool = False) -> None:
    try:
        os.lstat(destination)
    except FileNotFoundError:
        return
    except OSError as err:
        raise _migration_error(f"cannot inspect migration destination: {err}") from err
    if not _same_file(source, destination, environment=environment):
        raise _migration_error(f"migration will not replace an existing target file: {destination}")


def _publish(source: str, destination: str, mode: int, *, environment: bool = False) -> None:
    if os.path.lexists(destination):
        if _same_file(source, destination, environment=environment):
            return
        raise _migration_error(f"migration will not replace an existing target file: {destination}")
    parent = os.path.dirname(destination)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=parent)
    try:
        with open(source, "rb") as src, os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), mode)
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.link(temporary, destination)
        _fsync_directory(parent)
    except FileExistsError:
        if not _same_file(source, destination, environment=environment):
            raise _migration_error(f"migration will not replace an existing target file: {destination}")
    except OSError as err:
        raise _migration_error(f"cannot publish migration file {destination}: {err}") from err
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _claimed_target(target_dir: str, state_dir: str, manifest: dict, marker: dict) -> Optional[str]:
    """The Elastic data folder a begun history restore of this package owns, None without one."""
    if "history" not in manifest["scope"]:
        return None
    from tpotctl import migration_history

    return migration_history.claimed_target(target_dir, state_dir, marker)


def _check_data_tree(data_path: str, prepared: str, report: dict, claimed: Optional[str] = None) -> None:
    claim = os.path.join("elk", "data")
    if claimed is not None and os.path.abspath(claimed) != os.path.join(data_path, claim):
        raise _migration_error("the history restore claimed another target data folder")
    expected_files = {path[len("identity/"):] for path in report["files"] if path.startswith("identity/")}
    expected_dirs = set()
    for path in expected_files:
        parent = os.path.dirname(path)
        while parent:
            expected_dirs.add(parent)
            parent = os.path.dirname(parent)
    if claimed is not None:
        expected_dirs.update(("elk", claim))
    if not os.path.lexists(data_path):
        return
    if not os.path.isdir(data_path) or os.path.islink(data_path):
        raise _migration_error("target data path is not a real directory")
    for current, dirs, files in os.walk(data_path, followlinks=False):
        relative = os.path.relpath(current, data_path)
        prefix = "" if relative == "." else relative + os.sep
        if claimed is not None and relative == "elk":
            if os.path.islink(claimed):
                raise _migration_error("the claimed target Elastic data is no real directory")
            dirs[:] = [name for name in dirs if name != "data"]
        for name in dirs:
            path = os.path.join(current, name)
            if os.path.islink(path) or prefix + name not in expected_dirs:
                raise _migration_error("target data path contains files not created by this migration")
        for name in files:
            path = os.path.join(current, name)
            logical = prefix + name
            if os.path.islink(path) or logical not in expected_files or \
                    not _same_file(os.path.join(prepared, "identity", logical), path):
                raise _migration_error("target data path contains files not created by this migration")


def apply_import(target_dir: str, state_dir: str) -> None:
    """Publish prepared files without overwriting target data; matching partial runs are resumable."""
    target_dir = _target_checkout(target_dir)
    marker, manifest, _package = _read_pending(target_dir, state_dir)
    data_path = _target_data_path(target_dir)
    _prepared, report = _load_prepared(state_dir, marker)
    if report["target_data_path"] != data_path:
        raise _migration_error("target data path changed after import preparation")
    _precheck_destination(os.path.join(_prepared, "env"), os.path.join(target_dir, ".env"), environment=True)
    _precheck_destination(os.path.join(_prepared, "compose"), os.path.join(target_dir, "docker-compose.yml"))
    _check_data_tree(data_path, _prepared, report, _claimed_target(target_dir, state_dir, manifest, marker))
    for relative in sorted(path for path in report["files"] if path.startswith("identity/")):
        destination = os.path.join(data_path, relative[len("identity/"):])
        _precheck_destination(os.path.join(_prepared, relative), destination)
    _publish(os.path.join(_prepared, "env"), os.path.join(target_dir, ".env"), 0o600, environment=True)
    _publish(os.path.join(_prepared, "compose"), os.path.join(target_dir, "docker-compose.yml"), 0o644)
    for relative in sorted(path for path in report["files"] if path.startswith("identity/")):
        destination = os.path.join(data_path, relative[len("identity/"):])
        _publish(os.path.join(_prepared, relative), destination, 0o600)


def finish_import(state_dir: str) -> None:
    """Remove only the verified private stage after the standard installation succeeds."""
    state_dir = os.path.abspath(state_dir)
    _private_directory(state_dir, create=False)
    marker_path = os.path.join(state_dir, PENDING_NAME)
    package_path = os.path.join(state_dir, PACKAGE_NAME)
    _private_file(marker_path, "pending marker")
    _private_file(package_path, "staged migration package")
    with open(marker_path, encoding="utf-8") as handle:
        marker = json.load(handle)
    if _digest(package_path) != marker.get("package_sha256"):
        raise _migration_error("staged migration package changed; refusing to remove it")
    manifest = migration.validate_package(package_path)
    if "history" in manifest["scope"]:
        from tpotctl import migration_history

        if not migration_history.history_released(state_dir, marker):
            raise _migration_error("the Elastic history has not been restored and released yet; "
                                   "the private stage is kept")
    prepared = os.path.join(state_dir, PREPARED_NAME)
    if os.path.lexists(prepared):
        _private_directory(prepared, create=False)
        shutil.rmtree(prepared)
    os.unlink(package_path)
    os.unlink(marker_path)
    _fsync_directory(state_dir)
    try:
        os.rmdir(state_dir)
    except OSError:
        pass
