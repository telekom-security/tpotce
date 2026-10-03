#!/usr/bin/env python3
"""T-Pot Service Builder: build a docker-compose.yml from an edition plus your changes.

Interactive:   python3 customizer.py
Headless:      python3 customizer.py --base standard --add glutton --remove honeytrap
Rebuild:       python3 customizer.py --rebuild ~/tpotce/docker-compose.yml
"""

import argparse
import os
import shutil
import subprocess
import sys

version = \
"""
 ____  [T-Pot]         _            ____        _ _     _
/ ___|  ___ _ ____   _(_) ___ ___  | __ ) _   _(_) | __| | ___ _ __
\\___ \\ / _ \\ '__\\ \\ / / |/ __/ _ \\ |  _ \\| | | | | |/ _` |/ _ \\ '__|
 ___) |  __/ |   \\ V /| | (_|  __/ | |_) | |_| | | | (_| |  __/ |
|____/ \\___|_|    \\_/ |_|\\___\\___| |____/ \\__,_|_|_|\\__,_|\\___|_| v2.0
"""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# PyYAML comes from the distribution on a T-Pot host (installer, update.sh). Where it
# is missing, i.e. on macOS or Windows, the customizer runs from a venv of its own,
# set up on first use outside ~/tpotce so it is neither backed up nor reset.
VENV_MARKER = "TPOT_CUSTOMIZER_VENV"
VENV_REQUIREMENT = "PyYAML>=6,<7"


def venv_dir():
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(cache, "tpotce", "customizer-venv")


def venv_python(directory):
    if os.name == "nt":
        return os.path.join(directory, "Scripts", "python.exe")
    return os.path.join(directory, "bin", "python3")


def has_yaml(python):
    return subprocess.call([python, "-c", "import yaml"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def install_hint():
    if sys.platform == "darwin":
        return "Install Python from python.org or Homebrew (brew install python), both can create a venv."
    if os.name == "nt":
        return "Install Python from python.org, it can create a venv."
    ids = []
    try:
        with open("/etc/os-release", encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.strip().partition("=")
                if key in ("ID", "ID_LIKE"):
                    ids += value.strip('"').lower().split()
    except OSError:
        pass
    if {"debian", "ubuntu", "raspbian"} & set(ids):
        return "sudo apt install python3-yaml   (or python3-venv, then the customizer sets up its venv)"
    if {"fedora", "rhel", "centos", "almalinux", "rocky"} & set(ids):
        return "sudo dnf install python3-pyyaml"
    if any("suse" in i for i in ids):
        return "sudo zypper install python3-PyYAML"
    return "install PyYAML for this Python with the package manager of your system"


def give_up(reason):
    print(f"[ERROR] - The customizer needs PyYAML: {reason}\n  {install_hint()}", file=sys.stderr)
    sys.exit(2)


def ensure_yaml():
    """None if PyYAML is importable here, else the Python of a venv that has it."""
    try:
        import yaml  # noqa: F401
        return None
    except ImportError:
        pass
    if os.environ.get(VENV_MARKER):
        give_up(f"it is missing in {sys.prefix} as well")
    directory = venv_dir()
    python = venv_python(directory)
    if os.path.exists(python) and has_yaml(python):
        return python
    print(f"[INFO] - PyYAML is missing, setting up {directory} once (needs internet) ...", file=sys.stderr)
    # a venv that lost its Python, i.e. after an upgrade, is built anew
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(os.path.dirname(directory), exist_ok=True)
    if subprocess.call([sys.executable, "-m", "venv", directory],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0 or not os.path.exists(python):
        shutil.rmtree(directory, ignore_errors=True)
        give_up(f"{sys.executable} cannot create a venv")
    if subprocess.call([python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        VENV_REQUIREMENT]) != 0 or not has_yaml(python):
        shutil.rmtree(directory, ignore_errors=True)
        give_up(f"it could not be installed into {directory}")
    return python


def run_in_venv(python):
    """Start this script again with the venv's Python and hand back its exit code."""
    env = dict(os.environ)
    env[VENV_MARKER] = "1"
    proc = subprocess.Popen([python, os.path.abspath(__file__)] + sys.argv[1:], env=env)
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:    # the child gets it as well and decides
            continue


if __name__ == "__main__":
    _python = ensure_yaml()
    if _python:
        sys.exit(run_in_venv(_python))

import customizer_core as core  # noqa: E402

COLORS = {"red": "\033[91m", "green": "\033[92m", "blue": "\033[94m", "magenta": "\033[95m",
          "yellow": "\033[93m", "end": "\033[0m"}


def print_color(text, color, stream=sys.stdout):
    if stream.isatty():
        text = f"{COLORS[color]}{text}{COLORS['end']}"
    print(text, file=stream)


def report(result):
    for finding in result.findings:
        color = "red" if finding.level == "error" else "yellow"
        print_color(f"[{finding.level.upper()}] - {finding.text}", color, sys.stderr)


def names(value):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Build a T-Pot docker-compose.yml from an edition plus your changes.",
        epilog="Without --base/--add/--remove/--port/--rebuild the customizer starts interactively.")
    parser.add_argument("--base", help="edition to start from, e.g. standard or sensor "
                                       "(default: the edition of ~/tpotce/docker-compose.yml)")
    parser.add_argument("--add", default="", help="comma separated services to add from the catalog")
    parser.add_argument("--remove", default="", help="comma separated services to remove from the edition")
    parser.add_argument("--port", default="", metavar="OVERRIDES",
                        help="host port changes SERVICE:HOSTPORT[/udp]=NEWPORT, =- removes the mapping, "
                             "comma separated, e.g. wordpot:80=8080")
    parser.add_argument("--rebuild", metavar="FILE",
                        help="build a customizer file again from this release's catalog and editions")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="where to write (default: ~/tpotce/docker-compose-custom.yml, "
                             "with --rebuild the file itself)")
    parser.add_argument("--max-networks", type=int, default=core.DEFAULT_MAX_NETWORKS,
                        help=f"bridge networks Docker can create (default {core.DEFAULT_MAX_NETWORKS})")
    parser.add_argument("--text", action="store_true", help="plain text dialog instead of the full screen one")
    parser.add_argument("--setup", action="store_true",
                        help="only make sure PyYAML is there (setting up the venv if needed), then exit")
    return parser.parse_args(argv)


def starting_selection(catalog, base):
    """The edition asked for, or the configuration ~/tpotce/docker-compose.yml has now."""
    if base:
        edition = base.upper()
        if edition not in catalog.editions:
            raise core.CustomizerError(
                f"edition '{base}' does not exist, choose one of {', '.join(catalog.edition_names())}")
        return core.Selection(edition)
    edition, selection = core.current_edition()
    if selection and selection.base in catalog.editions:
        return selection
    if edition in catalog.editions:
        return core.Selection(edition)
    return core.Selection("STANDARD")


def write(catalog, result, path):
    text = core.render(catalog, result)
    core.write_output(text, path)


def next_steps(path):
    name = os.path.basename(path)
    print_color(f"[OK] - {path} is written.", "green")
    if os.path.abspath(path) == os.path.join(core.REPO_DIR, "docker-compose.yml"):
        return
    print_color("To use it:", "blue")
    print_color("  sudo systemctl stop tpot", "blue")
    print_color(f"  cd {core.REPO_DIR} && docker compose -f {name} up", "blue")
    print_color(f"  CTRL-C once everything works, then: docker compose -f {name} down -v", "blue")
    print_color(f"  mv {name} docker-compose.yml && sudo systemctl start tpot", "blue")


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.setup:
        where = f"the venv {sys.prefix}" if os.environ.get(VENV_MARKER) else sys.executable
        print_color(f"[OK] - PyYAML is available to the customizer ({where}).", "green")
        return 0
    try:
        catalog = core.Catalog()
        if args.rebuild:
            with open(args.rebuild, encoding="utf-8") as handle:
                selection = core.parse_header(handle.read())
            if selection is None:
                print_color(f"[ERROR] - {args.rebuild} was not built by this customizer.", "red", sys.stderr)
                return 2
            result = core.resolve(catalog, selection, args.max_networks, strict=False)
            report(result)
            if result.errors:
                return 1
            write(catalog, result, args.output or args.rebuild)
            print_color(f"[OK] - {args.output or args.rebuild} is rebuilt from the {selection.base} edition.",
                        "green")
            return 0

        output = args.output or os.path.join(core.REPO_DIR, "docker-compose-custom.yml")
        headless = args.base or args.add or args.remove or args.port
        selection = starting_selection(catalog, args.base)
        if headless:
            selection.add += [n for n in names(args.add) if n not in selection.add]
            selection.remove += [n for n in names(args.remove) if n not in selection.remove]
            selection.ports.update(core.parse_overrides(args.port))
            result = core.resolve(catalog, selection, args.max_networks, strict=True)
            report(result)
            if result.errors:
                return 1
        else:
            import customizer_tui as tui
            curses_ok = sys.stdin.isatty() and sys.stdout.isatty() and not args.text
            if curses_ok:
                try:
                    import curses  # noqa: F401
                except ImportError:
                    curses_ok = False
            if not curses_ok:
                print_color(version, "magenta")
            run = tui.run_curses if curses_ok else tui.run_text
            chosen = run(catalog, selection, args.max_networks)
            if chosen is None:
                print_color("Nothing written.", "blue")
                return 0
            result = core.resolve(catalog, chosen, args.max_networks, strict=True)
            report(result)
            if result.errors:
                return 1
        write(catalog, result, output)
        next_steps(output)
        return 0
    except core.CustomizerError as err:
        print_color(f"[ERROR] - {err}", "red", sys.stderr)
        return 2
    except OSError as err:
        print_color(f"[ERROR] - {err}", "red", sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
