"""The Python environment of tpot and the customizer, standard library only.

tpot needs Textual, which no distribution packages everywhere, so it runs from a
venv of its own in ~/.local/share/tpotce/venv: outside ~/tpotce, so update.sh
neither backs it up nor resets it. The packages are pinned with hashes in
requirements.txt and installed from wheels only, pip does not check the hashes of
what it would fetch to build a source package.

The customizer's headless part (update.sh --rebuild) only needs PyYAML and takes
the one of the distribution when there is one.
"""

import contextlib
import hashlib
import os
import shutil
import subprocess
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
MARKER = "tpot-requirements.sha256"
# set for a process started from the venv, so a broken venv cannot loop
GUARD = "TPOT_VENV"
NEEDS = {"yaml": ("yaml",), "ui": ("yaml", "textual", "rich")}


class BootstrapError(Exception):
    """The environment cannot be set up."""


def venv_dir() -> str:
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "tpotce", "venv")


def old_venv_dir() -> str:
    """Where the customizer kept a PyYAML-only venv before tpot existed."""
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(cache, "tpotce", "customizer-venv")


def venv_python(directory: str) -> str:
    if os.name == "nt":
        return os.path.join(directory, "Scripts", "python.exe")
    return os.path.join(directory, "bin", "python3")


def requirements_hash() -> str:
    with open(REQUIREMENTS, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def marker_ok(directory: str) -> bool:
    try:
        with open(os.path.join(directory, MARKER), encoding="utf-8") as handle:
            return handle.read().strip() == requirements_hash()
    except OSError:
        return False


def imports_ok(python: str, modules) -> bool:
    code = "; ".join(f"import {m}" for m in modules)
    return subprocess.call([python, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def importable(modules) -> bool:
    for module in modules:
        try:
            __import__(module)
        except ImportError:
            return False
    return True


def in_venv(directory: str) -> bool:
    return os.path.realpath(sys.prefix) == os.path.realpath(directory)


def venv_current(directory: str) -> bool:
    python = venv_python(directory)
    return os.path.exists(python) and marker_ok(directory) and imports_ok(python, NEEDS["ui"])


def install_hint() -> str:
    """What to install when this Python cannot create a venv."""
    if sys.platform == "darwin":
        return "Install Python from python.org or Homebrew (brew install python), both can create a venv."
    if os.name == "nt":
        return "Install Python from python.org, it can create a venv."
    return linux_hint(os_release_ids())


def os_release_ids(path: str = "/etc/os-release"):
    ids = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.strip().partition("=")
                if key in ("ID", "ID_LIKE"):
                    ids += value.strip('"').lower().split()
    except OSError:
        pass
    return ids


def linux_hint(ids) -> str:
    if {"debian", "ubuntu", "raspbian"} & set(ids):
        return "sudo apt install python3-venv   (the customizer alone also works with python3-yaml)"
    if {"fedora", "rhel", "centos", "almalinux", "rocky"} & set(ids) or any("suse" in i for i in ids):
        return "python3 -m venv should work out of the box, check the output above"
    return "install the venv module (python3-venv) for this Python"


def setup_venv(force: bool = False, quiet: bool = False) -> str:
    """Create, refresh or (force) rebuild the venv, give back its Python.

    The venv is built next to the one in use (venv.new) and swapped in when it works, so
    a tpot that runs meanwhile keeps its packages, and a failed build (no internet)
    leaves the old venv as it was. The scripts in venv/bin keep the path of venv.new in
    their shebang; tpot never runs them, only venv/bin/python and `python -m pip`.
    """
    directory = venv_dir()
    python = venv_python(directory)
    shutil.rmtree(old_venv_dir(), ignore_errors=True)
    if not force and venv_current(directory):
        return python
    # one build at a time: a tpot started meanwhile (another shell) waits instead of
    # cleaning up a venv.new that is still being built
    with _build_lock(directory):
        if not force and venv_current(directory):
            return python
        for leftover in (directory + ".new", directory + ".old"):       # of a run that broke off
            shutil.rmtree(leftover, ignore_errors=True)
        if not quiet:
            print(f"[INFO] - Setting up the Python packages of tpot in {directory} (needs internet) ...",
                  file=sys.stderr)
        _build(directory + ".new")
        try:
            _swap(directory + ".new", directory)
        except OSError as err:
            raise BootstrapError(f"the new venv could not take the place of {directory}: {err}")
    return python


@contextlib.contextmanager
def _build_lock(directory: str):
    os.makedirs(os.path.dirname(directory), exist_ok=True)
    try:
        import fcntl
    except ImportError:          # Windows: the customizer only, no menu that rebuilds meanwhile
        yield
        return
    with open(directory + ".lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _build(directory: str) -> None:
    os.makedirs(os.path.dirname(directory), exist_ok=True)
    python = venv_python(directory)
    if subprocess.call([sys.executable, "-m", "venv", directory],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0 or not os.path.exists(python):
        shutil.rmtree(directory, ignore_errors=True)
        raise BootstrapError(f"{sys.executable} cannot create a venv. {install_hint()}")
    pip = [python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-cache-dir",
           "--timeout", "15", "--retries", "1",
           "--require-hashes", "--only-binary", ":all:", "--no-deps", "-r", REQUIREMENTS]
    if subprocess.call(pip) != 0 or not imports_ok(python, NEEDS["ui"]):
        shutil.rmtree(directory, ignore_errors=True)
        raise BootstrapError(
            "the packages could not be installed: no internet, pypi.org not reachable, or no wheel "
            f"for Python {sys.version_info.major}.{sys.version_info.minor} on this platform")
    with open(os.path.join(directory, MARKER), "w", encoding="utf-8") as handle:
        handle.write(requirements_hash() + "\n")


def _swap(new: str, directory: str) -> None:
    """new takes the place of directory; between the two renames it is gone for a moment only."""
    if os.path.exists(directory):
        os.replace(directory, directory + ".old")
    os.replace(new, directory)
    shutil.rmtree(directory + ".old", ignore_errors=True)


def ensure(need: str):
    """None if this interpreter has what `need` asks for, else the venv Python to run with.

    yaml: PyYAML of the distribution is fine. ui: always the venv, and only the
    current one, so that an update of requirements.txt is picked up.
    """
    directory = venv_dir()
    if os.environ.get(GUARD):
        if importable(NEEDS[need]):
            return None
        raise BootstrapError(f"the venv {sys.prefix} is missing packages, run: tpot setup")
    if need == "yaml" and importable(NEEDS["yaml"]):
        return None
    if in_venv(directory) and marker_ok(directory) and importable(NEEDS[need]):
        return None
    return setup_venv()


def reexec(python: str, script: str, argv) -> int:
    """Run the script again with the venv's Python and give back its exit code."""
    env = dict(os.environ)
    env[GUARD] = "1"
    proc = subprocess.Popen([python, script] + list(argv), env=env)
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:    # the child gets it as well and decides
            continue
