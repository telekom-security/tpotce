"""T-Pot's own Kibana objects: their normalised form, their digest, the manifest,
and how they get into Kibana and stay current there.

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

The plan compares Kibana with the shipped file: an object is *new* (missing),
*current* (the shipped version), *update* (a version T-Pot shipped, or one T-Pot
imported and nobody changed since: the record of `<data>/kibana_objects/record.json`)
or *yours* (changed in Kibana). The modes of TPOT_KIBANA_OBJECTS import from it:
keep nothing, add the new, update the new and the unchanged, overwrite all but the
advanced settings. tpotinit runs them:

    python3 kibana_objects.py apply --mode M --data DIR --objects DIR --exports DIR [--kibana URL]
    python3 kibana_objects.py first --data DIR --objects DIR --exports DIR [--kibana URL]

The first import (`<data>/kibana_objects/pending`) takes the whole shipped file on its
first attempt, the advanced settings only on a fresh install; its retries take only what
mode update would, so they never touch what you changed. Every attempt is counted before
its request; after three it gives up (`given_up`) until the next release.

Kibana 9.5 writes nothing of an import that has one error in it (its successResults
then name what it would have written): the import goes again without the objects that
failed, so the others still get in.

tpotinit runs a byte-identical copy of this module with a plain python3: standard
library only, no import of `tpotctl`, Python 3.9. The default paths are those of
the checkout; the copy hands its own as parameters.
"""

import argparse
import contextlib
import copy
import datetime
import functools
import hashlib
import inspect
import http.client
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

try:
    import fcntl
except ImportError:          # Windows: the imports run on a Linux host only, the lock is not needed there
    fcntl = None

RULES = 2
SKIP_TYPES = frozenset({"config", "config-global"})

SHIPPED_NAME = "kibana_export.ndjson"
MANIFEST_NAME = "kibana_hashes.json"
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJECTS_DIR = os.path.join(REPO_DIR, "docker", "tpotinit", "dist", "etc", "objects")
SHIPPED = os.path.join(OBJECTS_DIR, SHIPPED_NAME)
MANIFEST = os.path.join(OBJECTS_DIR, MANIFEST_NAME)

KIBANA_URL = "http://127.0.0.1:64296"
MODES = ("keep", "add", "update", "overwrite")
EXPORT_PREFIX = "kibana_objects_"      # the exports of the T-Pot Manager in ~/tpot_backups
AUTO_PREFIX = "auto_"                  # the exports of tpotinit in <data>/kibana_objects/exports
AUTO_KEEP = 10
EXPORT_BODY = {"type": "*", "excludeExportDetails": True}
WINDOW = 60                            # seconds after a recorded import in which Kibana's updated_at is that import
TIMEOUT = 30
STATUS_TIMEOUT = 5
ROUNDS = 5                             # imports of one call, each without the objects the last one refused
LOCK_WAIT = 60                         # seconds a run waits for another one (tpotinit or tpot elastic)

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


# ---------------------------------------------------------------------------
# where things are
# ---------------------------------------------------------------------------

def state_dir(data_dir: str) -> str:
    """<data>/kibana_objects: the record, `pending` and the exports of tpotinit."""
    return os.path.join(data_dir, "kibana_objects")


def pending_path(data_dir: str) -> str:
    """There while the first import has not worked yet; tpotinit tries it on every start."""
    return os.path.join(state_dir(data_dir), "pending")


def auto_exports_dir(data_dir: str) -> str:
    return os.path.join(state_dir(data_dir), "exports")


def lock_path(data_dir: str) -> str:
    return os.path.join(state_dir(data_dir), "lock")


BUSY = "another Kibana import is running (tpotinit or tpot elastic)"
_HELD: Dict[str, int] = {}             # the locks this process holds, and how deep


@contextlib.contextmanager
def import_lock(data_dir: str):
    """One run at a time that reads a plan and imports: an exclusive flock on
    <data>/kibana_objects/lock, waited for at most LOCK_WAIT seconds (then BUSY). A run
    inside a run of the same process holds it already."""
    path = os.path.abspath(lock_path(data_dir))
    if _HELD.get(path):
        _HELD[path] += 1
        try:
            yield
        finally:
            _HELD[path] -= 1
        return
    if fcntl is None:
        yield
        return
    try:
        _make_dir(state_dir(data_dir), 0o770)
        handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o660)
    except OSError as error:
        raise KibanaError(f"cannot open {path}: {error}") from None
    try:
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise KibanaError(BUSY) from None
                time.sleep(0.2)
        _HELD[path] = 1
        try:
            yield
        finally:
            _HELD.pop(path, None)
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


def _one_at_a_time(run):
    """`run` under import_lock of its data_dir; mode keep makes no request, so no lock."""
    signature = inspect.signature(run)

    @functools.wraps(run)
    def locked(*args, **kwargs):
        given = signature.bind(*args, **kwargs).arguments
        if given.get("mode") == "keep":
            return run(*args, **kwargs)
        with import_lock(given["data_dir"]):
            return run(*args, **kwargs)
    return locked


def _make_dir(folder: str, mode: int) -> None:
    """The folder with its parents; one it makes gets `mode` whatever the umask, one that is
    there stays as it is."""
    if os.path.isdir(folder):
        return
    os.makedirs(folder, mode=mode, exist_ok=True)
    os.chmod(folder, mode)


def _write(path: str, text: str, mode: int, prefix: str) -> None:
    """`text` into `path` through a temporary file of `mode` next to it and a rename."""
    handle, temp = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Kibana
# ---------------------------------------------------------------------------

def _request(method: str, path: str, url: str, opener: Optional[Callable], data: Optional[bytes] = None,
             content_type: Optional[str] = None, timeout: int = TIMEOUT) -> bytes:
    headers = {"kbn-xsrf": "true"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url.rstrip("/") + path, data=data, method=method, headers=headers)
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException):
            body = ""
        finally:
            error.close()
        body = " ".join(body.split())[:200]
        raise KibanaError(f"Kibana at {url} answered {error.code}" + (f": {body}" if body else "")) from None
    except (OSError, http.client.HTTPException) as error:    # no connection, a timeout, a broken answer
        raise KibanaError(f"Kibana at {url} is not reachable ({getattr(error, 'reason', error)})") from None


def status(url: str = KIBANA_URL, opener: Optional[Callable] = None) -> Optional[str]:
    """None when Kibana answers its status with 200, else why not."""
    try:
        _request("GET", "/api/status", url, opener, timeout=STATUS_TIMEOUT)
    except KibanaError as error:
        return str(error)
    return None


def _export(url: str, opener: Optional[Callable], allow_empty: bool = False) -> Tuple[str, List[dict]]:
    raw = _request("POST", "/api/saved_objects/_export", url, opener,
                   json.dumps(EXPORT_BODY).encode(), "application/json")
    source = f"the export of Kibana at {url}"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise KibanaError(f"{source} is no UTF-8 text") from None
    objects = parse_ndjson(text, source)
    if not objects and not allow_empty:
        raise KibanaError(f"Kibana at {url} exported no objects")
    return text, objects


def export_objects(url: str = KIBANA_URL, opener: Optional[Callable] = None) -> str:
    """Every saved object of the default space as ndjson. An answer without objects, or
    with a line that is no object, counts as failed: it is no export to go back to."""
    return _export(url, opener)[0]


def export_modes(prefix: str) -> Tuple[int, int]:
    """Folder and file mode of an export: tpotinit's auto_ exports are for the group tpot
    too (the T-Pot Manager lists and restores them), the Manager's own are private."""
    return (0o770, 0o660) if prefix == AUTO_PREFIX else (0o700, 0o600)


def save_export(text: str, folder: str, prefix: str = EXPORT_PREFIX, now: Optional[float] = None) -> str:
    """`text` as <folder>/<prefix><YYYYmmddHHMMSS>.ndjson, the time in UTC, and its path; a
    second export in the same second gets _2, _3, ... (modes: export_modes; a folder that is
    there keeps its own)."""
    stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(time.time() if now is None else now))
    folder_mode, file_mode = export_modes(prefix)
    try:
        _make_dir(folder, folder_mode)
        path, number = os.path.join(folder, f"{prefix}{stamp}.ndjson"), 1
        while os.path.exists(path):
            number += 1
            path = os.path.join(folder, f"{prefix}{stamp}_{number}.ndjson")
        _write(path, text, file_mode, ".kibana-export-")
    except OSError as error:
        raise KibanaError(f"the export cannot be written into {folder}: {error}") from None
    return path


def _name_order(name: str, prefix: str) -> Tuple[str, int]:
    """The UTC stamp and the number of an export's name (_10 after _2); ("", 0) for another name."""
    match = re.fullmatch(re.escape(prefix) + r"(\d{14})(?:_(\d+))?\.ndjson", name)
    return (match.group(1), int(match.group(2) or 1)) if match else ("", 0)


def exports(folder: str, prefix: str) -> List[str]:
    """The exports <prefix>*.ndjson of `folder`, newest first: by the time of the file, then
    by the stamp and number of its name."""
    try:
        names = [n for n in os.listdir(folder) if n.startswith(prefix) and n.endswith(".ndjson")]
    except OSError:
        return []
    found = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            found.append(((os.path.getmtime(path), *_name_order(name, prefix)), path))
        except OSError:
            pass
    return [path for _, path in sorted(found, reverse=True)]


def rotate(folder: str, prefix: str, keep: int = AUTO_KEEP, spare: Optional[str] = None) -> List[str]:
    """Removes all but the newest `keep` exports of `prefix` and returns what it removed;
    `spare`, the export just written, always stays (a clock that went back cannot remove it)."""
    removed = []
    paths = exports(folder, prefix)
    if spare in paths:
        paths.remove(spare)
        keep -= 1
    for path in paths[max(keep, 0):]:
        try:
            os.unlink(path)
            removed.append(path)
        except OSError:
            pass
    return removed


@dataclass
class Result:
    """What an import did: (type, id) of what Kibana wrote, (type, id, title, error type) of
    what it refused, and in add mode (type, id) of what was there already."""
    imported: List[Tuple[str, str]] = field(default_factory=list)
    errors: List[Tuple[str, str, str, str]] = field(default_factory=list)
    existing: List[Tuple[str, str]] = field(default_factory=list)

    def keys(self) -> List[str]:
        return [f"{t}:{i}" for t, i in self.imported]


def _title(obj: dict) -> str:
    attributes = obj.get("attributes") or {}
    return str(attributes.get("title") or attributes.get("name") or key(obj))


def _import_once(objs: List[dict], overwrite: bool, url: str, opener: Optional[Callable]) -> dict:
    boundary = uuid.uuid4().hex
    ndjson = "\n".join(json.dumps(o, ensure_ascii=False) for o in objs)
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="tpot.ndjson"\r\n'
            f"Content-Type: application/ndjson\r\n\r\n"
            f"{ndjson}\r\n--{boundary}--\r\n").encode("utf-8")
    raw = _request("POST", f"/api/saved_objects/_import?overwrite={'true' if overwrite else 'false'}", url,
                   opener, body, f"multipart/form-data; boundary={boundary}")
    try:
        answer = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        answer = None
    if not isinstance(answer, dict):
        raise KibanaError(f"Kibana at {url} answered the import with no JSON object")
    return answer


def _entries(value) -> List[dict]:
    return [e for e in (value if isinstance(value, list) else []) if isinstance(e, dict)]


def import_objects(objs: List[dict], overwrite: bool, url: str = KIBANA_URL,
                   opener: Optional[Callable] = None) -> Result:
    """Imports `objs` into the default space; overwrite=False leaves every object that is
    there as it is (a conflict, which counts as there, no error).

    Kibana writes nothing of a file with one error in it, so the objects it did not refuse
    go in again on their own, at most ROUNDS times; what then is still out is an error.
    Imported is only an object that was sent and that an answer of success true without
    errors names; a sent object such an answer leaves out is not_imported."""
    result = Result()
    titles = {key(o): _title(o) for o in objs}
    todo = list(objs)
    for _ in range(ROUNDS):
        if not todo:
            return result
        answer = _import_once(todo, overwrite, url, opener)
        errors = _entries(answer.get("errors"))
        if not errors:
            named = set()
            if answer.get("success") is True:
                named = {f"{r.get('type')}:{r.get('id')}" for r in _entries(answer.get("successResults"))}
            result.imported = [(o["type"], o["id"]) for o in todo if key(o) in named]
            result.errors += [(o["type"], o["id"], titles[key(o)], "not_imported") for o in todo
                              if key(o) not in named]
            return result
        refused = set()
        for error in errors:
            kind, ident = str(error.get("type") or ""), str(error.get("id") or "")
            name = f"{kind}:{ident}"
            refused.add(name)
            what = error.get("error") if isinstance(error.get("error"), dict) else {}
            reason = str(what.get("type") or "unknown")
            meta = error.get("meta") if isinstance(error.get("meta"), dict) else {}
            title = str(meta.get("title") or error.get("title") or titles.get(name) or name)
            if reason == "conflict" and not overwrite:
                result.existing.append((kind, ident))
            else:
                result.errors.append((kind, ident, title, reason))
        left = [o for o in todo if key(o) not in refused]
        if len(left) == len(todo):        # errors of no object in the file: nothing more to try
            break
        todo = left
    result.errors += [(o["type"], o["id"], titles[key(o)], "not_imported") for o in todo]
    return result


# ---------------------------------------------------------------------------
# the record: when T-Pot imported each object
# ---------------------------------------------------------------------------

class Records:
    """<data>/kibana_objects/record.json: `type:id` -> the time of T-Pot's last import of it.

    A missing file is an empty record (the hashes alone decide then); a file that cannot be
    read is an error, never an empty record."""

    def __init__(self, data_dir: str):
        self.folder = state_dir(data_dir)
        self.path = os.path.join(self.folder, "record.json")
        self.objects: Dict[str, str] = {}
        try:
            with open(self.path, encoding="utf-8") as handle:
                text = handle.read()
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError) as error:
            raise KibanaError(f"{self.path} cannot be read: {error}") from None
        try:
            data = json.loads(text)
        except ValueError:
            raise KibanaError(f"{self.path} is no JSON; move it away to start a new record") from None
        objects = data.get("objects") if isinstance(data, dict) else None
        if not (isinstance(data, dict) and data.get("version") == 1 and isinstance(objects, dict)
                and all(isinstance(k, str) and isinstance(v, str) for k, v in objects.items())):
            raise KibanaError(f"{self.path} is no record of the Kibana objects")
        self.objects = dict(objects)

    def mark(self, keys, when: str) -> None:
        for name in keys:
            self.objects[name] = when

    def drop(self, keys) -> None:
        for name in keys:
            self.objects.pop(name, None)

    def save(self) -> None:
        text = json.dumps({"version": 1, "objects": self.objects}, indent=1, sort_keys=True) + "\n"
        try:
            _make_dir(self.folder, 0o770)
            _write(self.path, text, 0o660, ".record-")
        except OSError as error:
            raise KibanaError(f"cannot write {self.path}: {error}") from None


_TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)")


def _time(value) -> Optional[datetime.datetime]:
    """An ISO 8601 time with its zone (Kibana's 2026-10-15T08:08:24.513Z); None for anything else."""
    match = _TIME.fullmatch(value.strip()) if isinstance(value, str) else None
    if not match:
        return None
    fraction = ((match.group(2) or "") + "000000")[:6]
    zone = "+00:00" if match.group(3) == "Z" else match.group(3)
    try:
        return datetime.datetime.fromisoformat(f"{match.group(1)}.{fraction}{zone}")
    except ValueError:
        return None


def stamp(now: Optional[float] = None) -> str:
    """`now` as the UTC time of the record, in Kibana's form."""
    moment = datetime.datetime.fromtimestamp(time.time() if now is None else now, datetime.timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _within(updated, recorded) -> bool:
    """Kibana's updated_at is that of the recorded import: at most WINDOW seconds after it."""
    changed, imported = _time(updated), _time(recorded)
    if changed is None or imported is None:
        return False
    # T-Pot takes its time before the request, so its own write never lies before it; a time
    # before the record is a clock that went back, or someone else: yours
    return imported <= changed <= imported + datetime.timedelta(seconds=WINDOW)


# ---------------------------------------------------------------------------
# the plan and the modes
# ---------------------------------------------------------------------------

@dataclass
class Item:
    key: str
    type: str
    id: str
    title: str
    action: str          # new, update, current or yours


_MODE_ACTIONS = {"keep": (), "add": ("new",), "update": ("new", "update"),
                 "overwrite": ("new", "update", "yours")}


@dataclass
class Plan:
    items: List[Item]
    export_path: str = ""

    def _of(self, action: str) -> List[Item]:
        return [i for i in self.items if i.action == action]

    @property
    def new(self) -> List[Item]:
        return self._of("new")

    @property
    def update(self) -> List[Item]:
        return self._of("update")

    @property
    def current(self) -> List[Item]:
        return self._of("current")

    @property
    def yours(self) -> List[Item]:
        return self._of("yours")

    def to_import(self, mode: str) -> List[Item]:
        """What `mode` imports; current never, it is the shipped version already."""
        if mode not in _MODE_ACTIONS:
            raise KibanaError(f"no mode {mode!r}, one of {', '.join(MODES)}")
        return [i for i in self.items if i.action in _MODE_ACTIONS[mode]]


def plan(installed: List[dict], shipped_objs: List[dict], manifest: Dict[str, Set[str]],
         records: Dict[str, str]) -> Plan:
    """What each shipped object is in Kibana. Objects that T-Pot does not ship, and the
    advanced settings, are not in it at all."""
    by_key = {key(o): o for o in installed}
    items = []
    for obj in shipped_objs:
        if obj["type"] in SKIP_TYPES:
            continue
        name = key(obj)
        have = by_key.get(name)
        title = _title(have or obj)
        if have is None:
            action = "new"
        elif digest(have) == digest(obj):
            action = "current"
        elif digest(have) in manifest.get(name, set()) or _within(have.get("updated_at"), records.get(name)):
            action = "update"
        else:
            action = "yours"
        items.append(Item(name, obj["type"], obj["id"], title, action))
    return Plan(items)


def needs_first_import(installed: List[dict], shipped_objs: List[dict]) -> bool:
    """Kibana holds none of the objects T-Pot ships: the first import never worked."""
    ours = {key(o) for o in shipped_objs if o["type"] not in SKIP_TYPES}
    return bool(ours) and not ours & {key(o) for o in installed}


def _local(data_dir: str, objects_dir: str) -> Tuple["Records", Dict[str, Set[str]], List[dict]]:
    """The record, the manifest and the shipped objects: read before any request."""
    return (Records(data_dir), load_manifest(os.path.join(objects_dir, MANIFEST_NAME)),
            shipped(os.path.join(objects_dir, SHIPPED_NAME)))


def preview(data_dir: str, objects_dir: str, opener: Optional[Callable] = None, url: str = KIBANA_URL) -> Plan:
    """The plan for Kibana as it is now; nothing is written."""
    records, manifest, shipped_objs = _local(data_dir, objects_dir)
    return plan(_export(url, opener)[1], shipped_objs, manifest, records.objects)


def _fresh_export(url: str, opener: Optional[Callable], export, allow_empty: bool = False) -> Tuple[str, List[dict]]:
    """The export a caller made already (one per run), or a new one; the same rules."""
    if export is None:
        return _export(url, opener, allow_empty)
    text, installed = export
    if not installed and not allow_empty:
        raise KibanaError(f"Kibana at {url} exported no objects")
    return text, installed


@_one_at_a_time
def run_apply(mode: str, data_dir: str, objects_dir: str, export_dir: str, opener: Optional[Callable] = None,
              now: Optional[float] = None, url: str = KIBANA_URL, prefix: str = EXPORT_PREFIX,
              keep: Optional[int] = None, export: Optional[Tuple[str, List[dict]]] = None) -> Tuple[Plan, Result, str]:
    """Imports what `mode` takes of the plan and records it.

    keep makes no request. With nothing to import there is neither an export nor an
    import. update and overwrite save the export first (into `export_dir`, the newest
    `keep` of `prefix` stay when keep is given); without it there is no import. `export`
    is the (text, objects) of an export this run made already."""
    if mode not in MODES:
        raise KibanaError(f"no mode {mode!r}, one of {', '.join(MODES)}")
    if mode == "keep":
        return Plan([]), Result(), ""
    records, manifest, shipped_objs = _local(data_dir, objects_dir)
    text, installed = _fresh_export(url, opener, export)
    chosen = plan(installed, shipped_objs, manifest, records.objects)
    todo = chosen.to_import(mode)
    if not todo:
        if not chosen.new:
            clear_given_up(data_dir)
        return chosen, Result(), ""
    if mode in ("update", "overwrite"):
        chosen.export_path = save_export(text, export_dir, prefix, now)
        if keep is not None:
            rotate(export_dir, prefix, keep, spare=chosen.export_path)
    by_key = {key(o): o for o in shipped_objs}
    when = stamp(now)                    # before the request: Kibana's updated_at comes after it
    result = import_objects([by_key[i.key] for i in todo], mode != "add", url, opener)
    if result.imported:
        records.mark(result.keys(), when)
        records.save()
    if all(i.action != "new" or i.key in set(result.keys()) for i in chosen.items):
        clear_given_up(data_dir)        # every shipped object is in Kibana now
    return chosen, result, chosen.export_path


@_one_at_a_time
def run_restore(path: str, data_dir: str, export_dir: str, opener: Optional[Callable] = None,
                now: Optional[float] = None, url: str = KIBANA_URL,
                prefix: str = EXPORT_PREFIX) -> Tuple[str, Result]:
    """Imports every object of the export `path`, the advanced settings too, after a fresh
    export. What it restored is no longer T-Pot's import: its records go. A Kibana that
    holds no objects has nothing to export: the export path is "" then."""
    objs = parse_ndjson(_read_text(path), path)
    if not objs:
        raise KibanaError(f"{path} holds no Kibana objects")
    records = Records(data_dir)
    text, installed = _export(url, opener, allow_empty=True)
    export_path = save_export(text, export_dir, prefix, now) if installed else ""
    result = import_objects(objs, True, url, opener)
    if result.imported:
        records.drop(result.keys())
        records.save()
    return export_path, result


# The first import is pending until every shipped object is in Kibana. `pending` holds
# {"version": 1, "reason": "fresh"|"no-objects", "attempt": N, "refused": [...]}: why it is
# pending, how many attempts were started (counted before each request), and what the last
# answer that came back refused. tpotinit's first run writes {"reason": "fresh", "attempt": 0};
# the "no T-Pot objects" check writes "no-objects", which never imports the advanced settings.
GIVE_UP = 3            # attempts; after them the first import gives up on what is still missing
REASONS = ("fresh", "no-objects")


@dataclass
class FirstImport:
    result: Result
    attempt: int                     # 1 is the whole file, every later one a retry
    missing: List[str]               # shipped objects not in Kibana after it
    done: bool                       # pending is gone: every object is there, or GIVE_UP
    given_up: bool = False
    export_path: str = ""


def pending_state(data_dir: str) -> Optional[dict]:
    """The state of a pending first import, None when none is pending. A `pending` without a
    reason (empty, old, broken) counts as "no-objects"; one that cannot be read, or without a
    number of attempts, counts as a retry: a retry never touches yours."""
    try:
        with open(pending_path(data_dir), encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        text = "?"
    if not text.strip():
        return {"version": 1, "reason": "no-objects", "attempt": 0, "refused": []}
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {"version": 1, "reason": "no-objects", "attempt": 1, "refused": []}
    attempt = data.get("attempt")
    refused = data.get("refused")
    return {"version": 1,
            "reason": data.get("reason") if data.get("reason") in REASONS else "no-objects",
            "attempt": attempt if type(attempt) is int and attempt >= 0 else 1,
            "refused": [k for k in refused if isinstance(k, str)] if isinstance(refused, list) else []}


def _write_pending(data_dir: str, state: Optional[dict]) -> None:
    path = pending_path(data_dir)
    try:
        if state is None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            return
        _make_dir(state_dir(data_dir), 0o770)
        _write(path, json.dumps(state, sort_keys=True) + "\n", 0o660, ".pending-")
    except OSError as error:
        raise KibanaError(f"cannot write {path}: {error}") from None


@_one_at_a_time
def run_first_import(data_dir: str, objects_dir: str, export_dir: str, opener: Optional[Callable] = None,
                     now: Optional[float] = None, url: str = KIBANA_URL,
                     export: Optional[Tuple[str, List[dict]]] = None) -> FirstImport:
    """One attempt of the pending first import.

    A Kibana that holds objects is exported into `export_dir` first (auto_, the newest
    AUTO_KEEP stay); without that export no import. The attempt is counted in `pending`
    before the request, so an answer that never comes counts too. Attempt 1 imports the
    whole shipped file, the advanced settings only for a "fresh" install. Every later
    attempt is a retry: what mode update would import (missing objects and T-Pot's
    unchanged ones, never yours), and the advanced settings only when an answer refused
    them. `pending` goes when every shipped object is there, whoever wrote it; after GIVE_UP
    attempts the first import gives up on what is missing (`given_up`)."""
    path = os.path.join(objects_dir, SHIPPED_NAME)
    objs = parse_ndjson(_read_text(path), path)
    records = Records(data_dir)
    manifest = load_manifest(os.path.join(objects_dir, MANIFEST_NAME))
    state = pending_state(data_dir)
    if state is None:
        raise KibanaError(f"no first import is pending ({pending_path(data_dir)})")
    text, installed = _fresh_export(url, opener, export, allow_empty=True)
    export_path = ""
    if installed:
        export_path = save_export(text, export_dir, AUTO_PREFIX, now)
        rotate(export_dir, AUTO_PREFIX, AUTO_KEEP, spare=export_path)
    ours = [o for o in objs if o["type"] not in SKIP_TYPES]
    settings = [o for o in objs if o["type"] in SKIP_TYPES]
    result = Result()
    attempt = state["attempt"]
    if attempt < GIVE_UP:
        attempt += 1
        if attempt == 1 and needs_first_import(installed, objs):
            # the whole file only into a Kibana that holds none of T-Pot's objects: one that has
            # some (an import of yours came first) gets a retry, so nothing of yours is touched
            todo = ours + (settings if state["reason"] == "fresh" else [])
        else:
            wanted = {i.key for i in plan(installed, ours, manifest, records.objects).to_import("update")}
            todo = [o for o in ours if key(o) in wanted] + [o for o in settings if key(o) in state["refused"]]
        # before the request: the attempt counts, and a refusal is used up once it is sent again
        _write_pending(data_dir, dict(state, attempt=attempt, refused=[]))
        when = stamp(now)
        result = import_objects(todo, True, url, opener)
        if result.imported:
            records.mark(result.keys(), when)
            records.save()
    refused = sorted({f"{t}:{i}" for t, i, _, why in result.errors if why != "not_imported"})
    there = {key(o) for o in installed} | set(result.keys())
    missing = sorted([key(o) for o in ours if key(o) not in there]
                     + [key(o) for o in settings if key(o) in refused])
    done = not missing or attempt >= GIVE_UP
    if missing and done:
        _write_json(given_up_path(data_dir), {"version": 1, "refused": missing, "at": stamp(now),
                                              "shipped": shipped_sha(objects_dir)}, ".given-up-")
    elif not missing:
        clear_given_up(data_dir)
    _write_pending(data_dir, None if done else dict(state, attempt=attempt, refused=refused))
    return FirstImport(result, attempt, missing, done, bool(missing) and done, export_path)


def given_up_path(data_dir: str) -> str:
    """There when the first import gave up: the objects it gave up on, when, and the sha256
    of the shipped file it gave up with."""
    return os.path.join(state_dir(data_dir), "given_up")


def shipped_sha(objects_dir: str) -> str:
    path = os.path.join(objects_dir, SHIPPED_NAME)
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError as error:
        raise KibanaError(f"{path}: {error}") from None


def given_up(data_dir: str, objects_dir: str) -> Optional[List[str]]:
    """The objects the first import gave up on, while its marker is for this shipped file;
    None without one. A marker of another shipped file (a new release) goes. A marker that
    cannot be read still counts: the first import does not run again on its own then."""
    path = given_up_path(data_dir)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.loads(handle.read())
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    if not (isinstance(data, dict) and isinstance(data.get("refused"), list)):
        return []
    if data.get("shipped") != shipped_sha(objects_dir):
        clear_given_up(data_dir)
        return None
    return [k for k in data["refused"] if isinstance(k, str)]


def clear_given_up(data_dir: str) -> None:
    try:
        os.unlink(given_up_path(data_dir))
    except FileNotFoundError:
        pass
    except OSError as error:
        raise KibanaError(f"cannot remove {given_up_path(data_dir)}: {error}") from None


def _write_json(path: str, data: dict, prefix: str) -> None:
    try:
        _make_dir(os.path.dirname(path), 0o770)
        _write(path, json.dumps(data, sort_keys=True) + "\n", 0o660, prefix)
    except OSError as error:
        raise KibanaError(f"cannot write {path}: {error}") from None


def mark_pending(data_dir: str, reason: str = "no-objects") -> None:
    """A first import pending for `reason`, no attempt yet: the next start makes attempt 1.
    "fresh" (tpotinit's first run) takes the advanced settings along, "no-objects" never."""
    if reason not in REASONS:
        raise KibanaError(f"no reason {reason!r}, one of {', '.join(REASONS)}")
    _write_pending(data_dir, {"version": 1, "reason": reason, "attempt": 0, "refused": []})


# ---------------------------------------------------------------------------
# what the runs say
# ---------------------------------------------------------------------------

LOG = "# Kibana objects: "


def _error_lines(result: Result) -> List[str]:
    return [f"{LOG}not imported: {t}:{i} \"{title}\" ({reason})" for t, i, title, reason in result.errors]


def summary(mode: str, chosen: Plan, result: Result, export_path: str = "") -> List[str]:
    """One line of counts, then one line per object Kibana refused."""
    if mode == "keep":
        return [f"{LOG}TPOT_KIBANA_OBJECTS=keep, nothing changed"]
    done = set(result.keys())

    def count(action):
        return sum(1 for i in chosen.items if i.action == action and i.key in done)
    parts = [f"{count('new')} new"]
    if mode == "add":
        there = len(chosen.items) - len(chosen.new) + len(result.existing)
        parts.append(f"{there} already there")
    else:
        parts += [f"{count('update')} updated", f"{len(chosen.current)} the same"]
        parts.append(f"{count('yours')} of yours replaced" if mode == "overwrite"
                     else f"{len(chosen.yours)} of yours kept")
    if result.errors:
        parts.append(f"{len(result.errors)} failed")
    line = LOG + ", ".join(parts)
    if export_path:
        line += f" (export: {export_path})"
    return [line] + _error_lines(result)


def first_summary(first: FirstImport) -> List[str]:
    result = first.result
    line = f"{LOG}first import"
    if first.attempt > 1:
        line += f", attempt {first.attempt}"
    line += f", {len(result.imported)} imported"
    if result.errors:
        line += f", {len(result.errors)} failed"
    if not first.done:
        line += f", {len(first.missing)} not there yet; tpotinit tries again on the next start"
    if first.export_path:
        line += f" (export: {first.export_path})"
    lines = [line] + _error_lines(result)
    if first.given_up:
        lines.append(f"{LOG}warning: still not there after {GIVE_UP} attempts: {', '.join(first.missing)}; "
                     "tpotinit stops trying them, tpot elastic import tries them again")
    return lines


def _first_exit(first: FirstImport) -> int:
    print("\n".join(first_summary(first)), flush=True)
    return 1 if first.result.errors or first.missing else 0


def _cli_apply(args, opener: Optional[Callable]) -> int:
    """tpotinit's start, with one export: the first import while it is pending or when Kibana
    holds none of T-Pot's objects (in every mode, unless the first import gave up with this
    shipped file), else the mode, with its exports rotated."""
    export = _export(args.kibana, opener, allow_empty=True)
    if not os.path.exists(pending_path(args.data)):
        shipped_objs = shipped(os.path.join(args.objects, SHIPPED_NAME))
        if not needs_first_import(export[1], shipped_objs):
            chosen, result, path = run_apply(args.mode, args.data, args.objects, args.exports, opener,
                                             url=args.kibana, prefix=AUTO_PREFIX, keep=AUTO_KEEP, export=export)
            lines = summary(args.mode, chosen, result, path)
            if args.mode != "keep" and not chosen.to_import(args.mode):
                lines[0] = f"{LOG}nothing to import ({lines[0][len(LOG):]})"
            print("\n".join(lines), flush=True)
            return 1 if result.errors else 0
        refused = given_up(args.data, args.objects)
        if refused is not None:          # it gave up with this shipped file: until the release or you
            print(f"{LOG}the first import gave up on {len(refused)} objects, tpot elastic import retries them",
                  flush=True)
            return 0
        print(f"{LOG}Kibana holds none of T-Pot's objects, the first import runs again", flush=True)
        mark_pending(args.data, "no-objects")
    return _first_exit(run_first_import(args.data, args.objects, args.exports, opener, url=args.kibana,
                                        export=export))


def _cli_first(args, opener: Optional[Callable]) -> int:
    """One attempt of a pending first import; without `pending` nothing (apply decides)."""
    if pending_state(args.data) is None:
        print(f"{LOG}no first import is pending ({pending_path(args.data)}); "
              "run apply, which starts one when Kibana needs it", file=sys.stderr)
        return 2
    return _first_exit(run_first_import(args.data, args.objects, args.exports, opener, url=args.kibana))


def main(argv: Optional[List[str]] = None, opener: Optional[Callable] = None) -> int:
    """Exit 0, 1 on an error (also an object Kibana refused), 2 on a wrong call."""
    parser = argparse.ArgumentParser(prog="python3 -m tpotctl.kibana_objects",
                                     description="T-Pot's Kibana objects: the hash manifest, and the "
                                                 "imports tpotinit runs.")
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("manifest", help="write the manifest from the git history")
    mode = manifest.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="only list the shipped objects the manifest lacks (exit 1 if any)")
    mode.add_argument("--verify", action="store_true",
                      help="only compare the manifest with the one the history gives (exit 1 if they differ)")
    apply = commands.add_parser("apply", help="the first import while it is pending or when Kibana holds none "
                                              "of T-Pot's objects, else what the mode imports")
    apply.add_argument("--mode", required=True, choices=MODES, help="TPOT_KIBANA_OBJECTS")
    first = commands.add_parser("first", help="the first import: the whole shipped file, the advanced settings "
                                              "too; a retry only what mode update would import")
    for command in (apply, first):
        command.add_argument("--data", required=True, metavar="DIR", help="the data folder (TPOT_DATA_PATH)")
        command.add_argument("--objects", required=True, metavar="DIR",
                             help=f"the folder of {SHIPPED_NAME} and {MANIFEST_NAME}")
        command.add_argument("--exports", required=True, metavar="DIR",
                             help=f"where the export before an import goes ({AUTO_PREFIX}*, the newest "
                                  f"{AUTO_KEEP} stay)")
        command.add_argument("--kibana", default=KIBANA_URL, metavar="URL", help=f"default {KIBANA_URL}")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    try:
        if args.command == "apply":
            with import_lock(args.data):            # its export, its plan and its import: one run
                return _cli_apply(args, opener)
        if args.command == "first":
            return _cli_first(args, opener)
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
        print(f"{LOG}{error}" if args.command != "manifest" else error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
