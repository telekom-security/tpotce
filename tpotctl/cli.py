"""tpot sub-commands. Without a command tpot opens its menu (tpotctl.app)."""

import argparse
import os
import sys
import time
from typing import List, Optional

from tpotctl import bootstrap, ops
from tpotctl.bootstrap import REPO_DIR

MAGENTA = "#E20074"
HEALTH_STYLE = {"healthy": "green", "unhealthy": "red", "starting": "yellow"}
# handed over unchanged, `tpot update -h` shows the help of update.sh
PASSTHROUGH = {"update": "update.sh", "restore": "restore.sh"}
CUSTOMIZER = os.path.join(REPO_DIR, "compose", "customizer.py")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tpot", description="Configure and run T-Pot.",
        epilog="Without a command tpot opens its menu. update, restore and customize hand all their "
               "options to update.sh, restore.sh and compose/customizer.py, i.e. tpot update -h.")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("status", help="version, edition, service state and containers")
    ps = sub.add_parser("ps", help="containers with status and ports (was: dps)")
    ps.add_argument("-w", "--watch", nargs="?", type=float, const=2.0, metavar="SECONDS",
                    help="refresh every 2 or SECONDS seconds (was: dpsw)")
    sub.add_parser("images", help="docker images (was: dim)")
    for action in ("start", "stop", "restart"):
        sub.add_parser(action, help=f"{action} T-Pot (sudo systemctl {action} tpot)")
    sub.add_parser("update", help="update T-Pot, runs update.sh with your options")
    sub.add_parser("restore", help="restore a backup, runs restore.sh with your options")
    sub.add_parser("customize", help="choose edition, services and ports, runs compose/customizer.py")
    sub.add_parser("setup", help="set up or refresh the Python packages of tpot")
    return parser


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

def _console(stream=None):
    from rich.console import Console
    return Console(file=stream, highlight=False)


def ps_table(containers: List[ops.Container]):
    from rich import box
    from rich.table import Table
    from rich.text import Text
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MAGENTA}", pad_edge=False)
    table.add_column("NAME", style="bold", no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("PORTS", overflow="fold")
    for c in containers:
        style = HEALTH_STYLE.get(c.health) or ("red" if c.state != "running" else "")
        table.add_row(c.name, Text(c.status, style=style), c.ports)
    return table


def images_table(images: List[ops.Image]):
    from rich import box
    from rich.table import Table
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MAGENTA}", pad_edge=False)
    for column in ("REPOSITORY", "TAG", "IMAGE ID", "SIZE", "CREATED", "REPOSITORY:TAG"):
        table.add_column(column, no_wrap=column != "REPOSITORY:TAG")
    for i in images:
        table.add_row(i.repository, i.tag, i.id, i.size, i.created, i.ref)
    return table


def summary(containers: List[ops.Container]) -> str:
    running = sum(c.state == "running" for c in containers)
    unhealthy = sum(c.health == "unhealthy" for c in containers)
    starting = sum(c.health == "starting" for c in containers)
    restarting = sum(c.state == "restarting" for c in containers)
    parts = [f"{running}/{len(containers)} running"]
    if restarting:
        parts.append(f"{restarting} restarting")
    if unhealthy:
        parts.append(f"{unhealthy} unhealthy")
    if starting:
        parts.append(f"{starting} starting")
    return ", ".join(parts)


def print_ps(watch: Optional[float]) -> int:
    if not sys.stdout.isatty():
        for c in ops.containers():
            print(f"{c.name}\t{c.status}\t{c.ports}")
        return 0
    console = _console()
    if watch is None:
        console.print(ps_table(ops.containers()))
        return 0
    from rich.console import Group
    from rich.live import Live
    from rich.text import Text

    def frame():
        current = ops.containers()
        stamp = Text(f"{time.strftime('%H:%M:%S')}  {summary(current)}  (Ctrl-C ends)", style=MAGENTA)
        return Group(stamp, ps_table(current))
    try:
        with Live(frame(), console=console, auto_refresh=False, screen=False) as live:
            while True:
                time.sleep(max(0.5, watch))
                live.update(frame(), refresh=True)
    except KeyboardInterrupt:
        return 0


def print_images() -> int:
    images = ops.images()
    if not sys.stdout.isatty():
        for i in images:
            print(f"{i.repository}\t{i.tag}\t{i.id}\t{i.size}\t{i.created}")
        return 0
    _console().print(images_table(images))
    return 0


def print_status() -> int:
    state = ops.status()
    console = _console()
    rows = [("Version", f"{state.version} ({state.branch} {state.commit})"), ("Edition", state.edition),
            ("Type", state.tpot_type), ("Service", state.service), ("Checkout", state.repo_dir)]
    for label, value in rows:
        console.print(f"[bold {MAGENTA}]{label:<10}[/] {value}")
    try:
        current = ops.containers()
    except ops.OpsError as err:
        console.print(f"[red]Containers: {err}[/]")
        return 1
    console.print(f"[bold {MAGENTA}]{'Containers':<10}[/] {summary(current) if current else 'none'}")
    if current:
        console.print(ps_table(current))
    return 0


def error(text: str) -> None:
    print(f"[ERROR] - {text}", file=sys.stderr)


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def run_script(name: str, args: List[str]) -> int:
    """update.sh and restore.sh run git relative to the working directory."""
    command = ops.script_command(name, args)
    os.chdir(REPO_DIR)
    os.execv(command[0], command)
    return 0    # not reached


def run_customizer(args: List[str]) -> int:
    os.execv(sys.executable, [sys.executable, CUSTOMIZER] + list(args))
    return 0    # not reached


def run_service(action: str) -> int:
    import subprocess
    ops.require_linux_host(action)
    return subprocess.call(ops.service_command(action))


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        error("do not run tpot as root, it calls sudo itself where needed")
        return 2
    try:
        if argv and argv[0] in PASSTHROUGH:
            ops.require_linux_host(argv[0])
            return run_script(PASSTHROUGH[argv[0]], argv[1:])
        if argv and argv[0] == "customize":
            return run_customizer(argv[1:])
        args = build_parser().parse_args(argv)
        if args.command is None:
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                build_parser().print_help()
                return 2
            from tpotctl.app import run_app
            return run_app()
        if args.command == "setup":
            python = bootstrap.setup_venv()
            print(f"[OK] - The Python packages of tpot are ready ({os.path.dirname(os.path.dirname(python))}).")
            return 0
        if args.command in ("start", "stop", "restart"):
            return run_service(args.command)
        ops.require_linux_host(args.command)
        if args.command == "status":
            return print_status()
        if args.command == "ps":
            return print_ps(args.watch)
        if args.command == "images":
            return print_images()
    except (ops.OpsError, bootstrap.BootstrapError) as err:
        error(str(err))
        return 1
    return 2
