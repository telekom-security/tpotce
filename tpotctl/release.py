"""The version number of a release: the file `version` is the one source.

Scripts and the T-Pot Manager read that file (or TPOT_VERSION of the `.env` of an
installed T-Pot). A few places cannot read it, because they are static files or
defaults of other tools; they are listed in PLACES and this module keeps them in step:

    python3 -m tpotctl.release check                    every place has the number of `version`
    python3 -m tpotctl.release set-version X.Y.Z        writes `version` and every place (all or none)

Places that name the version on purpose and must not follow a release are listed in
EXCLUDED, each with its reason; the tests (`tpotctl/tests/test_release.py`) turn red on a
hard coded number that is in neither list. Standard library only, Python 3.9.
"""

import argparse
import contextlib
import errno
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
# the unit of an installed T-Pot names the compose file of its checkout
SERVICE_FILE = Path("/etc/systemd/system/tpot.service")

# the number a place carries: digits first, then word characters, dashes and inner dots
# (a dot that ends a sentence is not part of it)
V = r"([0-9][\w-]*(?:\.[\w-]+)*)"
RELEASE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
IMAGE = r"(?:dtagdevsec|ghcr\.io/telekom-security)/[\w.-]+:" + V


class ReleaseError(Exception):
    """check or set-version could not do its work.

    set-version writes every file or none: a refusal (a bad number, a lost place, a
    target it cannot open or that changed after it was read) leaves every file as it was.
    A write that fails puts the files back as they were; then `failed` is the file whose
    write failed, `restored` the files put back and `unrestored` the ones that could not
    be (they need a look by hand, the message names them)."""

    def __init__(self, message, failed=None, restored=(), unrestored=()):
        super().__init__(message)
        self.failed = failed
        self.restored = list(restored)
        self.unrestored = list(unrestored)


class Place(NamedTuple):
    """Files (globs relative to the checkout) and a regex for one line, group 1 is the version.

    Every file of a place must have at least one such line."""
    paths: Tuple[str, ...]
    pattern: str
    what: str
    skip: Tuple[str, ...] = ()


class Exclusion(NamedTuple):
    """Lines that name the version and stay as they are (pattern None: the whole file)."""
    paths: Tuple[str, ...]
    pattern: Optional[str]
    why: str


class Hit(NamedTuple):
    path: str
    line: int
    value: str
    start: int
    end: int


SMOKE_HELP = tuple(f"docker/_tests/tests/{name}.sh" for name in
                   ("hellpot", "heralding", "honeytrap", "log4pot", "miniprint"))

PLACES = (
    Place(("env.example",), r"^TPOT_VERSION=" + V + r"\s*$", "TPOT_VERSION of the defaults (image tag)"),
    Place((".env",), r"^TPOT_VERSION=" + V + r"\s*$", "TPOT_VERSION of the tracked .env, same as env.example"),
    Place(("docker/_builder/.env",), r"^TPOT_VERSION=" + V + r"\s*$", "the tag the builder builds and pushes"),
    Place(("docker/*/docker-compose.yml", "docker/*/*/docker-compose.yml"),
          r'^\s*image:\s*"?' + IMAGE + r'"?\s*$', "image tags of the standalone compose file of each service",
          skip=("docker/_builder/docker-compose.yml",           # takes ${TPOT_VERSION} of docker/_builder/.env
                "docker/deprecated/*/docker-compose.yml",       # see EXCLUDED
                "docker/tpotinit/macvlan/docker-compose.yml")),  # a macvlan example with the upstream nginx
    Place(("CITATION.cff",), r"^title: T-Pot " + V + r"\s*$", "CITATION.cff title"),
    Place(("CITATION.cff",), r"^\s*https://github\.com/telekom-security/tpotce/releases/tag/" + V + r"\s*$",
          "CITATION.cff release URL"),
    Place(("CITATION.cff",), r"^\s*description: T-Pot Release " + V + r"\s*$", "CITATION.cff release description"),
    Place(("CITATION.cff",), r"^version: " + V + r"\s*$", "CITATION.cff version (date-released is set by hand)"),
    Place(("docker/nginx/dist/html/index.html",), r'<div class="dynamic-text">T-Pot ' + V + r"</div>",
          "the version on the landing page (static HTML)"),
    Place(("genuserwin.ps1",), r"dtagdevsec/tpotinit:" + V, "the tpotinit image of the Windows web user script"),
    Place(("docker/p0f/dist/run.sh",), r"dtagdevsec/p0f:" + V, "the offline example in the header of run.sh"),
    Place(("docker/p0f/tools/build_os_confidence.py",), r'default="dtagdevsec/p0f:' + V + r'"',
          "the default image of the p0f confidence builder"),
    Place(("docker/_tests/tests/*.sh",), r'^DEFAULT_\w*IMAGE="' + IMAGE + r'"\s*$',
          "the image a smoke test checks by default (it does not read the compose file)"),
    Place(SMOKE_HELP, r"Defaults to " + IMAGE, "the default image in the help of a smoke test"),
)

# Logic that carries the version but has to read it instead (none left since stage 8e:
# update.sh and users.py read the file `version`). Until a reader is changed check and
# set-version treat it like a place (a release must not leave it behind) and the tests let
# it pass. The test test_pending_readers_are_still_pending fails as soon as one reads the
# file: remove it here.
PENDING_READERS = ()

EXCLUDED = (
    Exclusion(("version",), None, "the source itself"),
    Exclusion(("CHANGELOG.md",), None, "release notes name the release they describe"),
    Exclusion(("README.md", "docker/_tests/README.md", "docker/p0f/README.md"), None,
              "documentation examples, the docs are reviewed by hand for a release"),
    Exclusion(("SECURITY.md",), None, "the support policy (which releases get fixes) is decided at the release"),
    Exclusion(("update.sh",), r"^\s*#", "comments of update.sh are migration history (\"until ...\", Beelzebub note)"),
    Exclusion(("docker/honeyaml/Dockerfile",), r"\btpot-[0-9]", "the name of a branch of the honeyaml fork, a fixed ref"),
    Exclusion(("docker/deprecated/**/*",), None, "deprecated services are not built, their images exist only at the last tag"),
    Exclusion(("tpotctl/tests/**/*", "compose/tests/**/*", "docker/tpotinit/tests/**/*"), None,
              "test data, fixed so the tests do not change with a release"),
    Exclusion(("docker/**/*.ndjson",), None, "Kibana saved objects carry their own version fields"),
)

# the scripts and modules that must read the version (fuUI_VERSION, the file `version`)
SCRIPTS = ("install.sh", "update.sh", "restore.sh", "uninstall.sh", "genuser.sh", "deploy.sh",
           "docker/_builder/builder.sh", "docker/_builder/setup_builder.sh", "installer/lib/ui.sh",
           "tpotctl/*.py", "tpotctl/*/*.py")


def read_version(root=ROOT) -> str:
    text = Path(root, "version").read_text(encoding="utf-8").strip()
    if not text or len(text.split()) != 1:
        raise ReleaseError(f"{Path(root, 'version')}: no version in it")
    return text


def _glob(root, patterns) -> set:
    found = set()
    for pattern in patterns:
        found.update(p.relative_to(root).as_posix() for p in Path(root).glob(pattern) if p.is_file())
    return found


def files_of(place, root=ROOT) -> List[str]:
    """The files of a place (or an exclusion), relative paths, sorted."""
    root = Path(root)
    return sorted(_glob(root, place.paths) - _glob(root, getattr(place, "skip", ())))


def _split(data: bytes) -> List[str]:
    # split at \n only, so a join gives back the same bytes (\r stays at the end of a line)
    return data.decode("utf-8").split("\n")


def _lines(root, rel) -> List[str]:
    return _split(Path(root, rel).read_bytes())


class _Read(NamedTuple):
    """A file as set-version read it: its bytes and what it was (to find a replaced one)."""
    data: bytes
    lines: Tuple[str, ...]
    stat: Tuple[int, int, int, int]


def _identity(st) -> Tuple[int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _read(root, rel) -> _Read:
    with open(Path(root, rel), "rb") as handle:
        st = os.fstat(handle.fileno())
        data = handle.read()
    return _Read(data, tuple(_split(data)), _identity(st))


def hits(place, root, rel, lines=None) -> List[Hit]:
    regex = re.compile(place.pattern)
    found = []
    for number, line in enumerate(lines if lines is not None else _lines(root, rel), 1):
        for match in regex.finditer(line.rstrip("\r")):
            found.append(Hit(rel, number, match.group(1), match.start(1), match.end(1)))
    return found


def _scan(root, places, optional=False, read=None):
    """{path: [hits]} of every place and the problems of places without a line.

    `read` ({path: _Read}) keeps every file read once, for all its places and the write."""
    read = {} if read is None else read
    found, problems = {}, []
    for place in places:
        files = files_of(place, root)
        if not files and not optional:
            problems.append(f"{' '.join(place.paths)}: no such file ({place.what})")
        for rel in files:
            if rel not in read:
                read[rel] = _read(root, rel)
            got = hits(place, root, rel, read[rel].lines)
            if not got and not optional:
                problems.append(f"{rel}: not found: {place.what}")
            found.setdefault(rel, []).extend(got)
    return found, problems


def check(root=ROOT, places=PLACES, pending=PENDING_READERS, version=None) -> List[str]:
    """Every place against the file `version`; the list of problems, empty when all agree."""
    root = Path(root)
    version = version or read_version(root)
    problems, read = [], {}
    for group, optional in ((places, False), (pending, True)):
        found, missing = _scan(root, group, optional, read)
        problems += missing
        problems += [f"{hit.path}:{hit.line}: found {hit.value}, expected {version}"
                     for got in found.values() for hit in got if hit.value != version]
    return problems


def set_version(root, new, places=PLACES, pending=PENDING_READERS) -> List[str]:
    """Writes `new` into `version` and every place, in place (mode, owner and line endings stay).

    All or nothing: every file is read once, every target is opened for writing before the
    first write, and a write that fails puts back the files already written and the one
    that failed. Refuses (ReleaseError, nothing written) a number that is not X.Y.Z, a place
    that has no line any more, a target it cannot open and one that was replaced or changed
    after it was read. A failed write raises ReleaseError with `failed`, `restored` and
    `unrestored` (see there). Returns the files it changed."""
    root = Path(root)
    if not isinstance(new, str) or not RELEASE.fullmatch(new):
        raise ReleaseError(f"{new!r} is not a release number X.Y.Z (digits, e.g. 24.04.3)")
    read = {}
    found, problems = _scan(root, places, read=read)
    if problems:
        raise ReleaseError("places without the version, nothing written:\n  " + "\n  ".join(problems))
    for rel, got in _scan(root, pending, optional=True, read=read)[0].items():
        found.setdefault(rel, []).extend(got)

    writes = {}     # {path: (read, new bytes)}, in the order of writing (`version` last)
    for rel, got in sorted(found.items()):
        lines = list(read[rel].lines)
        spans = sorted({(h.line, h.start, h.end) for h in got}, reverse=True)
        for number, start, end in spans:
            line = lines[number - 1]
            lines[number - 1] = line[:start] + new + line[end:]
        data = "\n".join(lines).encode("utf-8")
        if data != read[rel].data:
            writes[rel] = (read[rel], data)
    old = read["version"] if "version" in read else _read(root, "version")
    ending = b"\r\n" if old.data.endswith(b"\r\n") else b"\n"
    writes["version"] = (old, new.encode("utf-8") + ending)
    _write_all(root, writes)
    return sorted(writes)


def _put(handle, data: bytes) -> None:
    """The whole of `data` into an unbuffered handle, from the start (a short write goes on)."""
    handle.seek(0)
    view = memoryview(data)
    while view:
        count = handle.write(view)
        if not count:
            raise OSError(errno.EIO, "the write made no progress")
        view = view[count:]
    handle.truncate()


def _write_all(root, writes) -> None:
    """Opens every target (r+b, unbuffered), then writes them; puts them back on an error."""
    with contextlib.ExitStack() as stack:
        handles = {}
        for rel, (was, _) in writes.items():
            # in place, not by rename: the file keeps its inode, mode and owner
            try:
                handle = stack.enter_context(open(Path(root, rel), "r+b", buffering=0))
                now = _identity(os.fstat(handle.fileno()))
            except OSError as error:
                raise ReleaseError(f"{rel}: cannot open it for writing ({error.strerror or error}),"
                                   " nothing written") from error
            if now != was.stat:
                raise ReleaseError(f"{rel}: replaced or changed after it was read, nothing written")
            handles[rel] = handle
        written = []
        for rel, (_, data) in writes.items():
            written.append(rel)
            try:
                _put(handles[rel], data)
            except OSError as error:
                restored, unrestored = [], []
                for back in written:
                    try:
                        _put(handles[back], writes[back][0].data)
                        restored.append(back)
                    except OSError:
                        unrestored.append(back)
                message = [f"{rel}: write failed ({error.strerror or error})"]
                if restored:
                    message.append("restored as they were: " + ", ".join(restored))
                if unrestored:
                    message.append("not restored, check by hand: " + ", ".join(unrestored))
                raise ReleaseError("\n  ".join(message), failed=rel, restored=restored,
                                   unrestored=unrestored) from error


def installed_t_pot(root, service_file=SERVICE_FILE) -> bool:
    """Whether the checkout `root` is the one of a T-Pot installed on this host: the unit
    `service_file` names its docker-compose.yml, or it has a data folder (TPOT_DATA_PATH,
    ./data by default). Then its `.env` is the configuration T-Pot starts with."""
    root = Path(root)
    if root.joinpath("data").is_dir():
        return True
    try:
        unit = Path(service_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    compose = (root.resolve() / "docker-compose.yml")
    for name in re.findall(r"""[^\s"'=]*docker-compose\.yml(?![^\s"'])""", unit):
        if Path(name).is_absolute() and Path(name).resolve() == compose:
            return True
    return False


def _excluded(root):
    """[(set of paths, compiled pattern or None)] of EXCLUDED."""
    return [(set(files_of(ex, root)), re.compile(ex.pattern) if ex.pattern else None) for ex in EXCLUDED]


def _uncovered(root, rel, lines, version, covered, excluded) -> List[str]:
    """Lines of a file that name the version and are neither a place nor excluded."""
    out = []
    for number, line in enumerate(lines, 1):
        count = line.count(version)
        if not count or covered.get((rel, number), 0) >= count:
            continue
        if any(rel in paths and (regex is None or regex.search(line)) for paths, regex in excluded):
            continue
        out.append(f"{rel}:{number}: {line.strip()}")
    return out


def _covered(root, version, groups):
    covered = {}
    for group in groups:
        for place in group:
            for rel in files_of(place, root):
                for hit in hits(place, root, rel):
                    if hit.value == version:
                        covered[(rel, hit.line)] = covered.get((rel, hit.line), 0) + 1
    return covered


def unlisted(root=ROOT, version=None) -> List[str]:
    """`git grep` of the version minus places, pending readers and exclusions: a new hard
    coded number, `path:line: text`. Needs a git checkout (tracked files only)."""
    root = Path(root)
    version = version or read_version(root)
    done = subprocess.run(["git", "-C", str(root), "grep", "-I", "-l", "-F", "-e", version],
                          capture_output=True, text=True, timeout=60)
    if done.returncode not in (0, 1):
        raise ReleaseError("git grep failed: " + done.stderr.strip())
    covered = _covered(root, version, (PLACES, PENDING_READERS))
    excluded = _excluded(root)
    out = []
    for rel in sorted(filter(None, done.stdout.splitlines())):
        out += _uncovered(root, rel, _lines(root, rel), version, covered, excluded)
    return out


def literals_in_scripts(root=ROOT, version=None) -> List[str]:
    """The version written into a script or module that should read it, `path:line: text`."""
    root = Path(root)
    version = version or read_version(root)
    excluded = _excluded(root)
    out = []
    for rel in sorted(_glob(root, SCRIPTS)):
        out += _uncovered(root, rel, _lines(root, rel), version, {}, excluded)
    return out


def pending_hits(root=ROOT) -> List[str]:
    """The lines of PENDING_READERS that still carry a version, in the form of literals_in_scripts."""
    root = Path(root)
    out = []
    for reader in PENDING_READERS:
        for rel in files_of(reader, root):
            lines = _lines(root, rel)
            out += [f"{rel}:{hit.line}: {lines[hit.line - 1].strip()}" for hit in hits(reader, root, rel, lines)]
    return out


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: {message}\n")


def main(argv=None) -> int:
    parser = _Parser(prog="python3 -m tpotctl.release",
                     description="The version of a release: `version` is the source, the other places follow it.")
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.required = True
    one = sub.add_parser("check", help="every place has the version of the file `version` (exit 1 if not)")
    one.add_argument("--root", default=str(ROOT), help="the checkout (default: this one)")
    two = sub.add_parser("set-version", help="write X.Y.Z into `version` and every place")
    two.add_argument("version", help="the new release number, X.Y.Z")
    two.add_argument("--root", default=str(ROOT), help="the checkout (default: this one)")
    # the unit that tells an installed T-Pot, for the tests (they never read the host's)
    two.add_argument("--service-file", default=str(SERVICE_FILE), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        if args.command == "check":
            version = read_version(root)
            problems = check(root, version=version)
            if problems:
                print(f"version {version}: {len(problems)} place(s) do not agree:")
                for problem in problems:
                    print("  " + problem)
                print("set them with: python3 -m tpotctl.release set-version " + version)
                return 1
            print(f"version {version}: every place agrees")
            return 0
        changed = set_version(root, args.version)
        print(f"version {args.version}: {len(changed)} file(s) written")
        for rel in changed:
            print("  " + rel)
        if ".env" in changed and installed_t_pot(root, args.service_file):
            print("note: .env is the configuration of the T-Pot installed here: its next start pulls"
                  f" the images {args.version} (TPOT_VERSION)", file=sys.stderr)
        return 0
    except (ReleaseError, OSError, UnicodeDecodeError) as error:
        print(f"release: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
