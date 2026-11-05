"""Source fingerprints for runtime configuration generated from this checkout."""

import argparse
import hashlib
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from typing import Iterable, Optional

STAMP_PREFIX = "# T-Pot sources sha256: "
_STAMP = re.compile(r"^# T-Pot sources sha256: ([0-9a-f]{64})\r?$")
_EDITION = re.compile(r"^# T-Pot:\s*([A-Z_]+)\s*$")
_CUSTOMIZER = re.compile(r"^# customizer: (.*)$")
_CUSTOMIZER_HASH = re.compile(r"^(# customizer: sha256=)[0-9a-f]{64}(\r?\n)?$")


class GeneratedError(Exception):
    """The sources or stamp of generated runtime configuration are invalid."""


class UnmanagedCompose(GeneratedError):
    """A deliberately self-managed compose file has no release sources to stamp."""


@dataclass
class ConfigurationStatus:
    drift: list[str]
    warnings: list[str]


def leading_comments(text: str) -> list[str]:
    header = []
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            break
        header.append(line)
    return header


def unmanaged_notice(text: str, name: str) -> str:
    for line in leading_comments(text):
        if re.match(r"^#\s*(T-Pot:|customizer:|T-Pot sources sha256:)", line):
            return ""
    return f"{name} is self-managed; release compatibility is not checked."


def source_fingerprint(root: str, sources: Iterable[str], selection: str = "") -> str:
    """Hash named source files and a configuration selection, independent of input order."""
    digest = hashlib.sha256()
    root = os.path.realpath(root)
    paths = sorted(set(sources))
    if not paths:
        raise GeneratedError("at least one source file is required")
    for relative in paths:
        if os.path.isabs(relative) or relative in ("", ".", "..") or relative.startswith(".." + os.sep):
            raise GeneratedError(f"source path must be relative to the checkout: {relative}")
        path = os.path.realpath(os.path.join(root, relative))
        if os.path.commonpath((root, path)) != root:
            raise GeneratedError(f"source path leaves the checkout: {relative}")
        try:
            with open(path, "rb") as source:
                content = source.read()
        except OSError as err:
            raise GeneratedError(f"cannot read source {relative}: {err}") from err
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    digest.update(b"selection\0")
    digest.update(selection.encode("utf-8"))
    return digest.hexdigest()


def stamp_text(text: str, fingerprint: str) -> str:
    """Add or replace the source marker in the leading comment block."""
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise GeneratedError("source fingerprint must be a SHA-256 hex digest")
    original = text.splitlines(keepends=True)
    checksum_valid = False
    for index, line in enumerate(original):
        match = _CUSTOMIZER_HASH.match(line)
        if match:
            checksum_valid = line.rstrip("\r\n") == (
                match.group(1) + hashlib.sha256("".join(original[index + 1:]).encode("utf-8")).hexdigest())
            break
    lines = without_stamp(text).splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    marker = f"{STAMP_PREFIX}{fingerprint}{newline}"
    index = 0
    while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
        if _CUSTOMIZER_HASH.match(lines[index]):
            break
        index += 1
    if index and not lines[index - 1].endswith(("\n", "\r")):
        lines[index - 1] += newline
    lines.insert(index, marker)
    for index, line in enumerate(lines):
        match = _CUSTOMIZER_HASH.match(line)
        if match and checksum_valid:
            body = "".join(lines[index + 1:]).encode("utf-8")
            lines[index] = f"{match.group(1)}{hashlib.sha256(body).hexdigest()}{match.group(2) or ''}"
            break
    return "".join(lines)


def without_stamp(text: str) -> str:
    """Compare runtime files with their templates without counting the source marker as an edit."""
    return "".join(line for line in text.splitlines(keepends=True) if not _STAMP.match(line.rstrip("\n")))


def has_fingerprint(text: str, fingerprint: str) -> bool:
    """Whether exactly one source marker names the expected fingerprint."""
    found = [match.group(1) for line in text.splitlines() if (match := _STAMP.match(line))]
    return found == [fingerprint]


def source_spec(text: str, kind: str):
    """Source paths and stable selection metadata for an env or edition/customizer compose file."""
    if kind == "env":
        return ["env.example"], ""
    if kind != "compose":
        raise GeneratedError(f"unknown generated configuration type: {kind}")
    header = leading_comments(text)
    edition = next((match.group(1) for line in header if (match := _EDITION.match(line))), "")
    if edition == "CUSTOM":
        fields = {}
        for line in header:
            match = _CUSTOMIZER.match(line)
            if match:
                fields.update(re.findall(r"(?:^|\s)([a-z]+)=([^\s]*)", match.group(1)))
        if not all(name in fields for name in ("version", "base", "add", "remove", "port")):
            raise GeneratedError("customizer compose file has no complete selection header")
        base = fields["base"]
        if not re.fullmatch(r"[A-Z_]+", base):
            raise GeneratedError("customizer compose file has an invalid base edition")
        selection = (
            f"version={fields['version']} base={fields['base']}\n"
            f"add={fields['add']}\nremove={fields['remove']}\nport={fields['port']}"
        )
        return [f"compose/{base.lower()}.yml", "compose/tpot_services.yml"], selection
    if not re.fullmatch(r"[A-Z_]+", edition):
        raise GeneratedError("compose file has no supported T-Pot edition header")
    return [f"compose/{edition.lower()}.yml"], edition


def configuration_fingerprint(text: str, root: str, kind: str) -> str:
    sources, selection = source_spec(text, kind)
    return source_fingerprint(root, sources, selection)


def configuration_is_current(text: str, root: str, kind: str) -> bool:
    """Whether a generated file carries the current source fingerprint."""
    return has_fingerprint(text, configuration_fingerprint(text, root, kind))


def configuration_status(root: str, compose_setting: Optional[str] = None) -> ConfigurationStatus:
    """Source drift and nonblocking notices; a draft may select a different compose path."""
    root = os.path.realpath(root)
    env_path = os.path.join(root, ".env")
    try:
        with open(env_path, encoding="utf-8") as handle:
            env_text = handle.read()
        if __package__:
            from .envfile import EnvFile
        else:
            from envfile import EnvFile
        if compose_setting is None:
            compose_setting = EnvFile(env_path).values().get("TPOT_DOCKER_COMPOSE", "")
    except (OSError, UnicodeDecodeError):
        return ConfigurationStatus([".env"], [])
    drift = []
    warnings = []
    try:
        if not configuration_is_current(env_text, root, "env"):
            drift.append(".env")
    except GeneratedError:
        drift.append(".env")

    compose_path = os.path.normpath(os.path.join(root, compose_setting or "./docker-compose.yml"))
    try:
        with open(compose_path, encoding="utf-8") as handle:
            compose_text = handle.read()
    except (OSError, UnicodeDecodeError):
        drift.append(os.path.basename(compose_path) or "docker-compose.yml")
    else:
        notice = unmanaged_notice(compose_text, os.path.basename(compose_path))
        if notice:
            warnings.append(notice)
        else:
            try:
                if not configuration_is_current(compose_text, root, "compose"):
                    drift.append(os.path.basename(compose_path) or "docker-compose.yml")
            except GeneratedError:
                drift.append(os.path.basename(compose_path) or "docker-compose.yml")
    return ConfigurationStatus(drift, warnings)


def configuration_drift(root: str) -> list[str]:
    """Names of missing or stale runtime files; self-managed compose files only warn."""
    return configuration_status(root).drift


def stamp_file(path: str, root: str, kind: str) -> bool:
    """Stamp a generated file atomically, making env files private; return whether it changed."""
    try:
        real_path = os.path.realpath(path)
        old_stat = os.stat(real_path)
        with open(real_path, encoding="utf-8", newline="") as source:
            text = source.read()
    except (OSError, UnicodeDecodeError) as err:
        raise GeneratedError(f"cannot read generated configuration {path}: {err}") from err
    if kind == "compose":
        notice = unmanaged_notice(text, os.path.basename(path))
        if notice:
            raise UnmanagedCompose(notice)
    fingerprint = configuration_fingerprint(text, root, kind)
    updated = stamp_text(text, fingerprint)
    mode = 0o600 if kind == "env" else stat.S_IMODE(old_stat.st_mode)
    if updated == text and stat.S_IMODE(old_stat.st_mode) == mode:
        return False
    directory = os.path.dirname(real_path)
    handle, temporary = tempfile.mkstemp(prefix=".tpot-config-", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        if (old_stat.st_uid, old_stat.st_gid) != (os.getuid(), os.getgid()):
            try:
                os.chown(temporary, old_stat.st_uid, old_stat.st_gid)
            except PermissionError as err:
                raise GeneratedError(f"{path} belongs to another user; stamp it as that user") from err
        os.replace(temporary, real_path)
    except OSError as err:
        raise GeneratedError(f"cannot stamp generated configuration {path}: {err}") from err
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Stamp generated T-Pot runtime configuration.")
    commands = parser.add_subparsers(dest="command", required=True)
    env = commands.add_parser("stamp-env", help="stamp .env from env.example and set mode 0600")
    env.add_argument("--file", required=True)
    env.add_argument("--root", required=True)
    compose = commands.add_parser("stamp-compose", help="stamp docker-compose.yml from its edition or selection")
    compose.add_argument("--file", required=True)
    compose.add_argument("--root", required=True)
    check = commands.add_parser("check", help="check whether runtime configuration matches this checkout")
    check.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        status = configuration_status(args.root)
        for warning in status.warnings:
            print(f"[WARNING] - {warning}", file=sys.stderr)
        if status.drift:
            print(f"{' and '.join(dict.fromkeys(status.drift))} is out of date with this checkout. "
                  "Run tpot update first.", file=sys.stderr)
            return 1
        return 0
    kind = "env" if args.command == "stamp-env" else "compose"
    try:
        stamp_file(args.file, args.root, kind)
    except UnmanagedCompose as err:
        print(f"[WARNING] - {err}", file=sys.stderr)
        return 0
    except (GeneratedError, OSError) as err:
        print(f"generated config: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
