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
import re
import shutil
import subprocess
import sys
import tempfile

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


def install_hint(package: str = "python3-venv") -> str:
    """What to install when this Python cannot create a venv."""
    if sys.platform == "darwin":
        return "Install Python from python.org or Homebrew (brew install python), both can create a venv."
    if os.name == "nt":
        return "Install Python from python.org, it can create a venv."
    return linux_hint(os_release_ids(), package)


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


APT_IDS = {"debian", "ubuntu", "raspbian"}


def linux_hint(ids, package: str = "python3-venv") -> str:
    if APT_IDS & set(ids):
        return f"sudo apt install {package}   (the customizer alone also works with python3-yaml)"
    if {"fedora", "rhel", "centos", "almalinux", "rocky"} & set(ids) or any("suse" in i for i in ids):
        return "python3 -m venv should work out of the box, check the output above"
    return "install the venv module (python3-venv) for this Python"


def venv_package(output: str, executable: str) -> str:
    """The Debian package with the venv module of this Python: the one venv names itself, else
    python3-venv for the Python of the distribution and python3.X-venv for another one."""
    match = re.search(r"\b(python3\.\d+-venv)\b", output)
    if match:
        return match.group(1)
    if executable == "/usr/bin/python3":
        return "python3-venv"
    return f"python3.{sys.version_info.minor}-venv"


def apt_get():
    return shutil.which("apt-get")


def interactive() -> bool:
    """A person at a terminal: the question goes to stderr, the answer comes from stdin. stdout may be
    redirected (tpot attackers > file, setup > /dev/null in genuser.sh) and must not swallow it."""
    return sys.stdin.isatty() and sys.stderr.isatty()


def ask(prompt: str) -> str:
    """input() on stderr: the prompt where the person sees it, EOFError at the end of stdin."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line


def offer_venv_package(package: str) -> bool:
    """Ask to install the venv package with sudo apt-get, give back whether it is installed now.

    Only on Debian, Ubuntu and Raspberry Pi OS at a terminal, not as root; sudo asks for its
    password itself, it never passes through tpot."""
    if not sys.platform.startswith("linux") or not APT_IDS & set(os_release_ids()) or not apt_get():
        return False
    if not interactive() or (hasattr(os, "geteuid") and os.geteuid() == 0):
        return False
    from tpotctl import say
    say.info(f"{sys.executable} cannot create a venv, it needs the package {package}.", sys.stderr)
    try:
        answer = ask("Install it now with sudo apt-get? [Y/n] ").strip().lower()
    except EOFError:
        return False
    if answer not in ("", "y", "yes"):
        return False
    if subprocess.call(["sudo", "apt-get", "install", "-y", package]) != 0:
        raise BootstrapError(f"sudo apt-get install {package} did not work, see its output above. "
                             f"{install_hint(package)}")
    return True


LINK = "/usr/local/bin/tpot"


def ensure_link(launcher: str, link: str = LINK, checkout: str = "") -> None:
    """Started as ./tpot in ~/tpotce without the link the installer makes: offer it, once.

    Linux only, not as root and only for a person at a terminal; a file of its own at that
    place is never touched, a link is (re)pointed with sudo ln, which asks for its password
    itself. The launcher it offered last is noted next to the venv, so tpot does not ask on
    every start."""
    if not sys.platform.startswith("linux") or (hasattr(os, "geteuid") and os.geteuid() == 0):
        return
    checkout = checkout or os.path.join(os.path.expanduser("~"), "tpotce")
    if os.path.realpath(os.path.dirname(launcher)) != os.path.realpath(checkout):
        return
    if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(launcher):
        return
    if not interactive():
        return                      # the playbook and update.sh link it, install.sh says if not
    marker = os.path.join(os.path.dirname(venv_dir()), "link-asked")
    try:
        with open(marker, encoding="utf-8") as handle:
            if handle.read().strip() == launcher:
                return
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(launcher + "\n")
    except OSError:
        return                      # without the note it would ask on every start
    from tpotctl import say
    command = f"sudo ln -sfn {launcher} {link}"
    if os.path.lexists(link) and not os.path.islink(link):
        say.warn(f"{link} is a file of its own, the T-Pot Manager leaves it alone: run {launcher}.", sys.stderr)
        return
    try:
        if os.path.islink(link) and os.path.exists(link):
            # another T-Pot Manager (i.e. of another user) works there: it stays unless this one says yes
            answer = ask(f"{link} runs {os.path.realpath(link)}. Point it to {launcher} instead? [y/N] ")
            answer = answer.strip().lower() or "n"
        else:
            answer = ask(f"Link {link} so that 'tpot' works everywhere? [Y/n] ").strip().lower()
    except EOFError:
        return
    if answer not in ("", "y", "yes"):
        say.info(f"Later with: {command}", sys.stderr)
        return
    if subprocess.call(["sudo", "ln", "-sfn", launcher, link]) == 0:
        say.ok(f"'tpot' works everywhere now ({link}).", sys.stderr)
    else:
        say.warn(f"{link} could not be linked, later with: {command}", sys.stderr)


def _make_venv(directory: str):
    """python -m venv, its output kept for the error."""
    with tempfile.TemporaryFile("w+") as out:
        code = subprocess.call([sys.executable, "-m", "venv", directory], stdout=out, stderr=subprocess.STDOUT)
        out.seek(0)
        output = out.read()
    if code == 0 and os.path.exists(venv_python(directory)):
        return True, output
    shutil.rmtree(directory, ignore_errors=True)
    return False, output


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
            print(f"[INFO] - Setting up the Python packages of the T-Pot Manager in {directory} (needs internet) ...",
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
    made, output = _make_venv(directory)
    if not made:
        package = venv_package(output, sys.executable)
        if offer_venv_package(package):
            made, output = _make_venv(directory)
    if not made:
        said = " ".join(line.strip() for line in output.splitlines() if line.strip())
        raise BootstrapError(f"{sys.executable} cannot create a venv. {install_hint(package)}"
                             + (f"\nvenv said: {said}" if said else ""))
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
    # the guard against loops: set by reexec only, the Engine keeps it from the children of the menu
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
