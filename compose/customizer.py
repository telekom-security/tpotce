#!/usr/bin/env python3
"""T-Pot Service Builder: build a docker-compose.yml from an edition plus your changes.

Interactive:   python3 customizer.py
Headless:      python3 customizer.py --base standard --add glutton --remove honeytrap
Rebuild:       python3 customizer.py --rebuild ~/tpotce/docker-compose.yml
"""

import argparse
import os
import sys


COMPOSE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, COMPOSE_DIR)
sys.path.insert(0, os.path.dirname(COMPOSE_DIR))

# PyYAML of the distribution is enough for everything but the full screen dialog,
# which needs Textual from the venv of tpot (tpotctl/bootstrap.py).
from tpotctl import bootstrap, say  # noqa: E402

HEADLESS = ("--base", "--add", "--remove", "--port", "--rebuild", "--setup", "-h", "--help", "--text")


def wants_ui(argv):
    if any(arg.split("=", 1)[0] in HEADLESS for arg in argv):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def prepare(argv):
    """The Python to run with (None: this one) and the arguments, --text without Textual."""
    if wants_ui(argv):
        try:
            return bootstrap.ensure("ui"), argv
        except bootstrap.BootstrapError as err:
            say.warn(f"No full screen dialog: {err}. Using the text dialog.", sys.stderr)
            argv = argv + ["--text"]
    try:
        return bootstrap.ensure("yaml"), argv
    except bootstrap.BootstrapError as err:
        say.error(f"The customizer needs PyYAML: {err}")
        sys.exit(2)


if __name__ == "__main__":
    _python, _argv = prepare(sys.argv[1:])
    if _python:
        sys.exit(bootstrap.reexec(_python, os.path.abspath(__file__), _argv))
    sys.argv[1:] = _argv

import customizer_core as core  # noqa: E402

def report(result):
    for finding in result.findings:
        (say.error if finding.level == "error" else say.warn)(finding.text, sys.stderr)


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
                        help="only make sure PyYAML is there (setting up the venv of tpot if needed), then exit")
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
    say.ok(f"{path} is written.")
    if os.path.abspath(path) == os.path.join(core.REPO_DIR, "docker-compose.yml"):
        return
    say.info("To use it:")
    say.hint("sudo systemctl stop tpot",
             f"cd {core.REPO_DIR} && docker compose -f {name} up",
             f"CTRL-C once everything works, then: docker compose -f {name} down -v",
             f"mv {name} docker-compose.yml && sudo systemctl start tpot")


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.setup:
        where = f"the venv {sys.prefix}" if os.environ.get(bootstrap.GUARD) else sys.executable
        say.ok(f"PyYAML is available to the customizer ({where}).")
        return 0
    try:
        catalog = core.Catalog()
        if args.rebuild:
            with open(args.rebuild, encoding="utf-8") as handle:
                selection = core.parse_header(handle.read())
            if selection is None:
                say.error(f"{args.rebuild} was not built by this customizer.")
                return 2
            result = core.resolve(catalog, selection, args.max_networks, strict=False)
            report(result)
            if result.errors:
                return 1
            write(catalog, result, args.output or args.rebuild)
            say.ok(f"{args.output or args.rebuild} is rebuilt from the {selection.base} edition.")
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
            if not args.text and wants_ui([]) and bootstrap.importable(bootstrap.NEEDS["ui"]):
                from tpotctl.app import run_customizer
                chosen = run_customizer(catalog, selection, args.max_networks)
            else:
                import customizer_tui as tui
                say.info("T-Pot customizer")
                chosen = tui.run_text(catalog, selection, args.max_networks)
            if chosen is None:
                say.info("Nothing written.")
                return 0
            result = core.resolve(catalog, chosen, args.max_networks, strict=True)
            report(result)
            if result.errors:
                return 1
        write(catalog, result, output)
        next_steps(output)
        return 0
    except core.CustomizerError as err:
        say.error(f"{err}")
        return 2
    except OSError as err:
        say.error(f"{err}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
