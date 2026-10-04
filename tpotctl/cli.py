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
    env = sub.add_parser("env", help="list, check and change the settings in .env (default: list)")
    actions = env.add_subparsers(dest="env_command", metavar="ACTION")
    listing = actions.add_parser("list", help="the settings of this T-Pot, secrets masked")
    listing.add_argument("--all", action="store_true", help="also the ones of services that do not run here")
    listing.add_argument("--show-secrets", action="store_true", help="do not mask passwords and keys")
    get = actions.add_parser("get", help="print the value of one setting")
    get.add_argument("key")
    change = actions.add_parser("set", help="change settings, only written if they are valid afterwards")
    change.add_argument("assignments", nargs="+", metavar="KEY=VALUE")
    actions.add_parser("check", help="check .env as tpotinit does on start, exit code 1 on errors")
    users = sub.add_parser("users", help="users of the T-Pot web UI (default: list)")
    user_actions = users.add_subparsers(dest="users_command", metavar="ACTION")
    user_actions.add_parser("list", help="the web users, entries T-Pot would not start with are marked")
    for name, text in (("add", "add a web user (was: genuser.sh)"), ("passwd", "change the password of a web user")):
        action = user_actions.add_parser(name, help=text)
        action.add_argument("name", nargs="?", help="asked for if left out" if name == "add" else None)
        action.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
        action.add_argument("--allow-weak", action="store_true", help="accept a password cracklib calls weak")
    remove = user_actions.add_parser("remove", help="remove a web user, not the last working one")
    remove.add_argument("name")
    remove.add_argument("-y", "--yes", action="store_true", help="do not ask")
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


def print_problems(problems, console) -> None:
    from rich.text import Text
    for problem in problems:
        style = "red" if problem.level == "error" else "yellow"
        console.print(Text(f"[{problem.level.upper()}] - {problem.key}: {problem.text}", style=style))


def run_env(args) -> int:
    from rich.markup import escape
    from tpotctl import settings as tsettings
    current = tsettings.load()
    console = _console()
    command = args.env_command or "list"
    if command == "get":
        value = current.values.get(args.key)
        if value is None:
            error(f"{args.key} is not set in {current.path}")
            return 1
        print(value)
        return 0
    if command == "check":
        problems = current.problems()
        print_problems(problems, console)
        errors = [p for p in problems if p.level == "error"]
        if errors:
            console.print(f"[red]{len(errors)} error(s), T-Pot will not start like this.[/]")
            return 1
        console.print("[green]All settings seem to be valid.[/]")
        return 0
    if command == "set":
        changes = {}
        for item in args.assignments:
            key, sep, value = item.partition("=")
            if not sep or not key:
                error(f"'{item}' is not KEY=VALUE")
                return 2
            changes[key.strip()] = value
        try:
            remaining = current.change(changes)
        except tsettings.SettingsError as err:
            error(str(err))
            return 1
        print_problems(remaining, console)
        for key in changes:
            console.print(f"[green][OK] - {key} is set.[/]")
        console.print(f"[{MAGENTA}]Restart T-Pot to apply it: tpot restart[/]")
        return 0
    # list
    values = current.values
    rules = current.relevant(include_all=getattr(args, "all", False))
    reveal = getattr(args, "show_secrets", False)
    problems = {p.key: p for p in current.problems()}
    if not sys.stdout.isatty():
        for rule in rules:
            print(f"{rule.key}={tsettings.shown(rule, values.get(rule.key, ''), reveal)}")
        return 0
    from rich import box
    from rich.table import Table
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MAGENTA}", pad_edge=False)
    table.add_column("SETTING", style="bold", no_wrap=True)
    table.add_column("VALUE", overflow="fold")
    table.add_column("WHAT", overflow="fold")
    section = None
    for rule in rules:
        if rule.section != section:
            section = rule.section
            title = dict(envschema_sections()).get(section, section)
            table.add_row(f"[{MAGENTA}]{title}[/]", "", "")
        value = escape(tsettings.shown(rule, values.get(rule.key, ""), reveal))
        problem = problems.get(rule.key)
        if problem:
            value += f"  [{'red' if problem.level == 'error' else 'yellow'}]! {escape(problem.text)}[/]"
        what = rule.title + ("" if rule.editable else f" [dim]({current.why_fixed(rule.key)})[/]")
        table.add_row(rule.key, value, what)
    console.print(table)
    return 0


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def read_password(args) -> str:
    import getpass
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    if not sys.stdin.isatty():
        raise SystemExit("[ERROR] - no terminal to ask for the password, use --password-stdin")
    first = getpass.getpass("Password: ")
    if getpass.getpass("Repeat the password: ") != first:
        raise SystemExit("[ERROR] - the passwords do not match")
    return first


def run_users(args) -> int:
    from rich.text import Text
    from tpotctl import users as tusers
    store = tusers.load()
    console = _console()
    command = args.users_command or "list"
    if command == "list":
        entries = store.users()
        if not sys.stdout.isatty():
            for user in entries:
                print(f"{user.name}\t{user.scheme or '-'}\t{user.problem or 'ok'}")
            return 0 if all(u.ok for u in entries) and entries else 1
        from rich import box
        from rich.table import Table
        table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MAGENTA}", pad_edge=False)
        for column in ("USER", "HASH", "STATE"):
            table.add_column(column)
        for user in entries:
            state = Text("ok", style="green") if user.ok else Text(
                f"! {user.problem}, T-Pot will not start - tpot users "
                f"{'passwd' if tusers.NAME_RE.match(user.name) else 'remove'} '{user.name}'", style="bold red")
            table.add_row(user.name, user.scheme or "-", state)
        console.print(table if entries else Text("No web users, add one with: tpot users add", style="red"))
        return 0 if entries and all(u.ok for u in entries) else 1
    if command == "remove":
        if not args.yes:
            if not sys.stdin.isatty():
                error("add --yes to remove a user without a terminal")
                return 2
            if ask(f"Remove the web user {args.name}? [y/N] ").lower() != "y":
                return 1
        note = store.remove(args.name)
        console.print(Text(f"[OK] - {args.name} is removed, {note}.", style="green"))
        return 0
    name = args.name or (ask("Name of the new web user: ") if sys.stdin.isatty() else "")
    tusers.check_name(name)
    password = read_password(args)
    weak = tusers.weakness(password)
    if weak and not args.allow_weak:
        if args.password_stdin or not sys.stdin.isatty():
            error(f"the password is weak ({weak}), add --allow-weak to keep it anyway")
            return 1
        if ask(f"The password is weak ({weak}). Keep it anyway? [y/N] ").lower() != "y":
            return 1
    note = store.add(name, password) if command == "add" else store.passwd(name, password)
    what = "is added" if command == "add" else "has a new password"
    console.print(Text(f"[OK] - {name} {what} (bcrypt), {note}.", style="green"))
    return 0


def envschema_sections():
    from tpotctl import envschema
    return envschema.SECTIONS


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
        if args.command == "env":
            from tpotctl import settings as tsettings
            try:
                return run_env(args)
            except tsettings.SettingsError as err:
                error(str(err))
                return 1
        if args.command == "users":
            from tpotctl import users as tusers
            try:
                return run_users(args)
            except tusers.UsersError as err:
                error(str(err))
                return 1
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
