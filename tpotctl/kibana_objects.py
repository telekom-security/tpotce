"""T-Pot's own Kibana objects: their normalised form, their digest and the manifest.

An object in Kibana is T-Pot's when its digest is in the manifest of every version
T-Pot ever shipped, `docker/tpotinit/dist/etc/objects/kibana_hashes.json`. The
manifest is generated from the git history of the objects files:

    python3 -m tpotctl.kibana_objects manifest            writes the manifest
    python3 -m tpotctl.kibana_objects manifest --check    lists shipped objects it lacks (exit 1)
    python3 -m tpotctl.kibana_objects manifest --verify   the file equals the history (exit 1)

The normalisation leaves out what Kibana changes on its own (times, versions, the
order of references, the shape of a saved search in Kibana 9.5, empty field
attributes, the Discover counts of the fields), so an object counts as changed only
when its content changed. A change
of these rules is a new `RULES` and a new manifest.

tpotinit runs a byte-identical copy of this module with a plain python3: standard
library only, no import of `tpotctl`, Python 3.9. The default paths are those of
the checkout; the copy hands its own as parameters.
"""

import argparse
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from typing import Dict, List, Optional, Set

RULES = 2
SKIP_TYPES = frozenset({"config", "config-global"})

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJECTS_DIR = os.path.join(REPO_DIR, "docker", "tpotinit", "dist", "etc", "objects")
SHIPPED = os.path.join(OBJECTS_DIR, "kibana_export.ndjson")
MANIFEST = os.path.join(OBJECTS_DIR, "kibana_hashes.json")

# the files in the repository whose every committed version goes into the manifest
HISTORY_PATHS = ("docker/tpotinit/dist/etc/objects/kibana_export.ndjson",
                 "docker/tpotinit/dist/etc/objects/kibana_export.ndjson.zip")

DROP = {"updated_at", "created_at", "updated_by", "created_by", "version", "typeMigrationVersion",
        "coreMigrationVersion", "migrationVersion", "managed", "namespaces", "sort"}
SEARCH_TOP = ("title", "description", "tabs")
SEARCH_DEAD = ("hits", "version")


class KibanaError(Exception):
    """A file, an answer or a manifest that cannot be used; the text says which and why."""


def key(obj: dict) -> str:
    return f"{obj['type']}:{obj['id']}"


def _parsed(value):
    """Every string that starts with { or [ and is JSON becomes {"__json__": <it, parsed>}."""
    if isinstance(value, dict):
        return {k: _parsed(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_parsed(v) for v in value]
    if isinstance(value, str) and value[:1] in ("{", "["):
        try:
            return {"__json__": _parsed(json.loads(value))}
        except ValueError:
            return value
    return value


def _search(attributes: dict) -> dict:
    """A saved search as Kibana 9.5 keeps it: title, description and its tabs.

    A search of an earlier Kibana (no tabs) is one tab of its own fields. The id of a
    tab is random per install; hits and version mean nothing."""
    tabs = attributes.get("tabs") or [{"label": "Untitled", "attributes": {
        k: v for k, v in attributes.items() if k not in SEARCH_TOP + SEARCH_DEAD}}]
    canon = []
    for tab in tabs:
        tab = {k: v for k, v in tab.items() if k != "id"}
        tab["attributes"] = {k: v for k, v in (tab.get("attributes") or {}).items() if k not in SEARCH_DEAD}
        canon.append(tab)
    keep = {k: attributes[k] for k in ("title", "description") if k in attributes}
    keep["tabs"] = canon
    return keep


def normalise(obj: dict) -> dict:
    """The object without what Kibana changes on its own; `obj` itself stays as it is."""
    out = {k: copy.deepcopy(v) for k, v in obj.items() if k not in DROP and not k.endswith("MigrationVersion")}
    attributes = out.get("attributes") or {}
    if out.get("type") == "index-pattern" and isinstance(attributes.get("fieldAttrs"), str):
        try:
            field_attrs = json.loads(attributes["fieldAttrs"])
        except ValueError:
            field_attrs = None
        if isinstance(field_attrs, dict):
            # count is the popularity Discover raises on every use, not a choice of yours;
            # an entry with nothing else in it means nothing
            entries = {k: ({a: b for a, b in v.items() if a != "count"} if isinstance(v, dict) else v)
                       for k, v in field_attrs.items()}
            attributes["fieldAttrs"] = json.dumps({k: v for k, v in entries.items() if v != {}})
    if out.get("type") == "search":
        attributes = _search(attributes)
    out["attributes"] = _parsed(attributes)
    out["references"] = sorted(out.get("references") or [],
                               key=lambda r: (r.get("type", ""), r.get("id", ""), r.get("name", "")))
    return out


def digest(obj: dict) -> str:
    text = json.dumps(normalise(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_ndjson(text: str, source: str) -> List[dict]:
    """The objects of an export, in their order; the export details and empty lines left out."""
    objects = []
    if text.startswith("﻿"):
        text = text[1:]
    for number, line in enumerate(text.split("\n"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            raise KibanaError(f"{source}, line {number}: not a JSON object") from None
        if not isinstance(obj, dict):
            raise KibanaError(f"{source}, line {number}: not a JSON object")
        if "exportedCount" in obj and "type" not in obj:
            continue
        if not all(isinstance(obj.get(field), str) and obj.get(field) for field in ("type", "id")):
            raise KibanaError(f"{source}, line {number}: an object without type or id")
        objects.append(obj)
    return objects


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError) as error:
        raise KibanaError(f"{path}: {error}") from None


def shipped(path: str = SHIPPED) -> List[dict]:
    """The objects T-Pot ships, without the advanced settings."""
    return [o for o in parse_ndjson(_read_text(path), path) if o["type"] not in SKIP_TYPES]


def _ndjson_texts(data: bytes, path: str, source: str) -> List[str]:
    """The ndjson text of a file of the history: the file itself or the members of the zip."""
    if not path.endswith(".zip"):
        return [data.decode("utf-8")]
    texts = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith(".ndjson") and not name.startswith("__MACOSX/"):
                texts.append(archive.read(name).decode("utf-8"))
    if not texts:
        raise KibanaError(f"{source}: no .ndjson in the archive")
    return texts


def _git(repo: str, *args: str, data: Optional[bytes] = None) -> bytes:
    return subprocess.run(["git", "-C", repo, *args], input=data, capture_output=True, check=True).stdout


def _add(objects: Dict[str, Set[str]], data: bytes, path: str, source: str) -> None:
    try:
        for text in _ndjson_texts(data, path, source):
            for obj in parse_ndjson(text, source):
                if obj["type"] not in SKIP_TYPES:
                    objects.setdefault(key(obj), set()).add(digest(obj))
    except (UnicodeDecodeError, zipfile.BadZipFile) as error:
        raise KibanaError(f"{source}: {error}") from None


def history_problem(repo: str) -> Optional[str]:
    """Why the history of `repo` cannot give the manifest (no git, no repository with a
    HEAD, a shallow clone), or None."""
    try:
        if _git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true":
            return f"{repo}: a shallow clone lacks the history"
        _git(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    except OSError as error:
        return f"git cannot be run: {error}"
    except subprocess.CalledProcessError:
        return f"{repo}: no git repository with a HEAD"
    return None


def _blobs(repo: str, commits: List[str], path: str) -> Dict[str, str]:
    """blob id -> the first commit that has it, of every commit in `commits` that has `path`."""
    request = "".join(f"{commit}:{path}\n" for commit in commits).encode()
    lines = _git(repo, "cat-file", "--batch-check", data=request).decode().splitlines()
    if len(lines) != len(commits):
        raise KibanaError(f"{repo}: git cat-file answered {len(lines)} lines for {len(commits)} commits")
    blobs: Dict[str, str] = {}
    for commit, line in zip(commits, lines):
        if line.endswith(" missing"):
            continue
        fields = line.split()
        if len(fields) != 3 or fields[1] != "blob":
            raise KibanaError(f"commit {commit}: {path} is no file ({line})")
        blobs.setdefault(fields[0], commit)
    return blobs


def _read_blobs(repo: str, blobs: List[str]) -> Dict[str, bytes]:
    """The content of each blob, read in one `git cat-file --batch`."""
    out = _git(repo, "cat-file", "--batch", data="".join(f"{blob}\n" for blob in blobs).encode())
    contents: Dict[str, bytes] = {}
    position = 0
    for blob in blobs:
        newline = out.find(b"\n", position)
        fields = out[position:newline].decode().split() if newline >= 0 else []
        if len(fields) != 3 or fields[0] != blob or fields[1] != "blob" or not fields[2].isdigit():
            raise KibanaError(f"blob {blob} cannot be read")
        size = int(fields[2])
        contents[blob] = out[newline + 1:newline + 1 + size]
        if len(contents[blob]) != size:
            raise KibanaError(f"blob {blob} is cut short")
        position = newline + 1 + size + 1
    return contents


def build_manifest(repo: str) -> dict:
    """The digests of every version of the objects files in any commit HEAD of `repo` reaches.

    Every commit counts, merges included; a commit without the file is skipped. The
    working tree does not count: only what was committed can have been shipped."""
    problem = history_problem(repo)
    if problem:
        raise KibanaError(problem)
    objects: Dict[str, Set[str]] = {}
    try:
        commits = _git(repo, "rev-list", "HEAD").decode().split()
        for path in HISTORY_PATHS:
            blobs = _blobs(repo, commits, path)
            contents = _read_blobs(repo, sorted(blobs))
            for blob in sorted(blobs):
                _add(objects, contents[blob], path, f"commit {blobs[blob]}: {path}")
    except (OSError, subprocess.CalledProcessError) as error:
        raise KibanaError(f"{repo}: the history cannot be read: {error}") from None
    return {"rules": RULES, "objects": {k: sorted(objects[k]) for k in sorted(objects)}}


def verify_manifest(path: str = MANIFEST, repo: str = REPO_DIR) -> bool:
    """The manifest file is the one the history gives."""
    try:
        text = _read_text(path)
    except KibanaError:
        return False
    return text == manifest_text(build_manifest(repo))


def manifest_text(manifest: dict) -> str:
    return json.dumps(manifest, indent=1, sort_keys=True) + "\n"


def load_manifest(path: str = MANIFEST) -> Dict[str, Set[str]]:
    """`type:id` -> the digests of every version T-Pot shipped of it."""
    try:
        data = json.loads(_read_text(path))
    except ValueError:
        raise KibanaError(f"{path}: not JSON") from None
    if not isinstance(data, dict) or type(data.get("rules")) is not int or data["rules"] != RULES:
        found = data.get("rules") if isinstance(data, dict) else None
        raise KibanaError(f"{path}: made with the rules {found}, this T-Pot has {RULES}")
    objects = data.get("objects")
    if not isinstance(objects, dict) or not all(
            isinstance(v, list) and all(isinstance(h, str) for h in v) for v in objects.values()):
        raise KibanaError(f"{path}: no list of objects")
    return {k: set(v) for k, v in objects.items()}


def check_manifest(path: str = MANIFEST, shipped_path: str = SHIPPED) -> List[str]:
    """The keys of the shipped objects whose digest is not in the manifest; [] is fine."""
    manifest = load_manifest(path)
    return [key(o) for o in shipped(shipped_path) if digest(o) not in manifest.get(key(o), ())]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tpotctl.kibana_objects",
                                     description="The hash manifest of T-Pot's Kibana objects.")
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("manifest", help="write the manifest from the git history")
    mode = manifest.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="only list the shipped objects the manifest lacks (exit 1 if any)")
    mode.add_argument("--verify", action="store_true",
                      help="only compare the manifest with the one the history gives (exit 1 if they differ)")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            if verify_manifest():
                return 0
            print(f"{MANIFEST} is not the manifest of the history", file=sys.stderr)
            print("run: python3 -m tpotctl.kibana_objects manifest", file=sys.stderr)
            return 1
        if args.check:
            missing = check_manifest()
            for name in missing:
                print(f"not in the manifest: {name}")
            if missing:
                print("run: python3 -m tpotctl.kibana_objects manifest", file=sys.stderr)
            return 1 if missing else 0
        data = build_manifest(REPO_DIR)
        with open(MANIFEST, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(manifest_text(data))
        versions = sum(len(v) for v in data["objects"].values())
        print(f"{MANIFEST}: {len(data['objects'])} objects, {versions} versions")
        return 0
    except KibanaError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
