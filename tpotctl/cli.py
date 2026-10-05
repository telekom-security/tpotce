"""tpot sub-commands. Without a command tpot opens its menu (tpotctl.app)."""

import argparse
import os
import sys
import time
from typing import List, Optional

from tpotctl import bootstrap, ops, say
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
               "options to update.sh, restore.sh and compose/customizer.py, i.e. tpot update -h. status, ps, "
               "images, start, stop, restart, update, restore, check, uninstall and sensors need a T-Pot host "
               "(Linux with systemd); users and sensors a HIVE.")
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
    install = sub.add_parser("install", help="install T-Pot on this host with the assistant (install.sh -s does it "
                                             "without questions)")
    install.add_argument("--classic", action="store_true", help="the questions of install.sh in the terminal")
    sub.add_parser("uninstall", help="remove T-Pot from this host, with a backup first if you like "
                                     "(uninstall.sh -y does it without questions)")
    env = sub.add_parser("env", help="list, check and change the settings in .env (default: list)")
    actions = env.add_subparsers(dest="env_command", metavar="ACTION")
    listing = actions.add_parser("list", help="the settings of this T-Pot, secrets masked")
    listing.add_argument("--all", action="store_true", help="also the ones of services that do not run here")
    listing.add_argument("--show-secrets", action="store_true", help="do not mask passwords and keys")
    get = actions.add_parser("get", help="print the value of one setting")
    get.add_argument("key")
    change = actions.add_parser("set", help="change settings, only written if they are valid afterwards")
    change.add_argument("assignments", nargs="+", metavar="KEY=VALUE")
    change.add_argument("--unlock", action="store_true",
                        help="also change fixed keys of the system section (TPOT_VERSION, TPOT_DATA_PATH, ...), "
                             "after their warning")
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
    sensors = sub.add_parser("sensors", help="sensors of this HIVE (default: list)")
    sensor_actions = sensors.add_subparsers(dest="sensors_command", metavar="ACTION")
    listing = sensor_actions.add_parser("list", help="sensors, their access and when they were last seen")
    listing.add_argument("--days", type=int, default=7, help="look for events of the last DAYS days (default 7)")
    add = sensor_actions.add_parser("add", help="deploy a sensor over SSH (was: deploy.sh)")
    add.add_argument("--host", help="IP or name of the sensor, asked for if left out")
    add.add_argument("--ssh-user", help="user T-Pot was installed with on the sensor")
    add.add_argument("--ssh-port", type=int, default=64295, help="SSH port of the sensor (default 64295, T-Pot's)")
    add.add_argument("--hive-address", help="IP or name the sensor reaches this HIVE on")
    add.add_argument("--no-become-pass", action="store_true", help="sudo on the sensor needs no password")
    add.add_argument("-y", "--yes", action="store_true", help="do not ask, renew the certificate if needed")
    remove = sensor_actions.add_parser("remove", help="revoke the access of a sensor, nothing is done on it")
    remove.add_argument("name")
    remove.add_argument("-y", "--yes", action="store_true", help="do not ask")
    change = sensor_actions.add_parser("set", help="add where a sensor is, i.e. for sensors of earlier releases")
    change.add_argument("name")
    change.add_argument("--host")
    change.add_argument("--ssh-user")
    change.add_argument("--ssh-port", type=int)
    change.add_argument("--hive-address")
    cert = sensor_actions.add_parser("cert", help="the certificate the sensors check this HIVE with")
    cert.add_argument("--add", action="append", default=[], metavar="ADDRESS",
                      help="an IP or name of this HIVE the certificate has to cover, repeatable")
    cert.add_argument("--renew", action="store_true", help="issue it anew for its addresses and the --add ones")
    cert.add_argument("--distribute", nargs="*", metavar="NAME",
                      help="copy it to these registered sensors (all if no NAME) and restart their Logstash")
    cert.add_argument("--no-restart", action="store_true", help="do not restart T-Pot after --renew")
    cert.add_argument("--no-become-pass", action="store_true", help="sudo on the sensors needs no password")
    cert.add_argument("-y", "--yes", action="store_true", help="do not ask")
    edition = sub.add_parser("edition", help="the editions of T-Pot and switching between them (default: list)")
    edition_actions = edition.add_subparsers(dest="edition_command", metavar="ACTION")
    edition_actions.add_parser("list", help="the editions, the one in use marked")
    switch = edition_actions.add_parser("set", help="switch to another edition: stop T-Pot, keep the compose "
                                                    "file in ~/tpot_backups, swap it, start T-Pot")
    switch.add_argument("key", metavar="EDITION", help="standard, sensor, llm, mini, mobile or tarpit")
    switch.add_argument("-y", "--yes", action="store_true", help="do not ask")
    switch.add_argument("--web-user", metavar="NAME", help="the web user a SENSOR needs to become a HIVE")
    switch.add_argument("--password-stdin", action="store_true", help="read its password from stdin")
    switch.add_argument("--password-file", metavar="FILE", default="", help="read its password from FILE")
    switch.add_argument("--become-file", metavar="FILE", default="",
                        help="read the sudo password from FILE (the tpot menu hands one over)")
    llm = sub.add_parser("llm", help="the LLM backends of Beelzebub and Galah: find an Ollama, list the models, "
                                     "test a model (default: their settings)")
    llm_actions = llm.add_subparsers(dest="llm_command", metavar="ACTION")
    detect = llm_actions.add_parser("detect", help="find an Ollama: the configured URLs, this host, its Docker "
                                                   "bridges and gateways, on port 11434")
    detect.add_argument("--scan", action="store_true", help="also ask every address of the /24 of this host, it "
                                                            "looks like a scan to the network")
    detect.add_argument("-y", "--yes", action="store_true", help="scan without asking")
    for name, text in (("models", "the models the backend of a honeypot offers"),
                       ("test", "send a short prompt to the model of a honeypot, from this host")):
        action = llm_actions.add_parser(name, help=text)
        action.add_argument("service", nargs="?", choices=["beelzebub", "galah"], default="galah")
    check = sub.add_parser("check", help="checks of a running T-Pot: probe the honeypots, test the Attack Map "
                                         "pipeline")
    check_actions = check.add_subparsers(dest="check_command", metavar="CHECK")
    probe = check_actions.add_parser("honeypots", help="probe the honeypots: service requests, then nmap over every "
                                                       "published port (runs hptest.sh)")
    probe.add_argument("--host", default="", help="the host to probe, default: the address of this host")
    probe.add_argument("--become-file", metavar="FILE", default="", help="the sudo password in a file")
    pipe = check_actions.add_parser("pipeline", help="inject test events and follow them to the Attack Map "
                                                     "(runs attackmap_pipeline_test.sh); they stay in Kibana")
    pipe.add_argument("--types", help="comma list of cowrie, dionaea, honeytrap, rdphoneypot")
    pipe.add_argument("--ips", help="test source IPs, one per type")
    pipe.add_argument("--dry-run", action="store_true", help="show the events only, inject nothing")
    pipe.add_argument("--become-file", metavar="FILE", default="", help="the sudo password in a file")
    pipe.add_argument("-y", "--yes", action="store_true", help="do not ask")
    attackers = sub.add_parser("attackers", help="the source IPs with the most attacks, with country and "
                                                 "reputation (was mytopips.sh)")
    attackers.add_argument("--hours", type=int, default=24, help="of the last HOURS hours (default 24)")
    attackers.add_argument("--count", type=int, default=10, help="how many (default 10)")
    attackers.add_argument("--plain", action="store_true", help="only the IPs, one per line")
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
    from tpotctl import glyphs
    table.add_column("Name", style="bold", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Ports", overflow="fold")
    for c in containers:
        style = HEALTH_STYLE.get(c.health) or ("red" if c.state != "running" else "")
        status = Text()
        status.append(f"{glyphs.g(ops.cell_state(c)[0])} ", style=style or "green")
        status.append(c.status, style=style)
        table.add_row(c.name, status, c.ports)
    return table


def images_table(images: List[ops.Image]):
    from rich import box
    from rich.table import Table
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MAGENTA}", pad_edge=False)
    for column in ("Repository", "Tag", "Image ID", "Size", "Created", "Repository:tag"):
        table.add_column(column, no_wrap=column != "Repository:tag")
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
        unlocked = [key for key in changes if current.can_unlock(key)] if args.unlock else []
        for key in unlocked:
            say.warn(f"{key} is unlocked: {current.schema[key].unlock}", console.file)
        try:
            remaining = current.change(changes, unlocked=unlocked)
        except tsettings.SettingsError as err:
            error(str(err))
            return 1
        print_problems(remaining, console)
        for key in changes:
            say.ok(f"{key} is set.", console.file)
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
    table.add_column("Setting", style="bold", no_wrap=True)
    table.add_column("Value", overflow="fold")
    table.add_column("What", overflow="fold")
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
        fail("no terminal to ask for the password, use --password-stdin")
    first = getpass.getpass("Password: ")
    if getpass.getpass("Repeat the password: ") != first:
        fail("the passwords do not match")
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
        for column in ("User", "Hash", "State"):
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
        say.ok(f"{args.name} is removed, {note}.", console.file)
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
    say.ok(f"{name} {what} (bcrypt), {note}.", console.file)
    return 0


def confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        fail(f"{question} Add --yes to do it without a terminal.")
    return ask(f"{question} [y/N] ").lower() == "y"


def sensors_table(registry, status, days: int):
    from rich import box
    from rich.table import Table
    from rich.text import Text
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MAGENTA}", pad_edge=False)
    for column in ("Sensor", "Host", "Hostname", "Last seen", "State"):
        table.add_column(column, overflow="fold")
    for sensor in registry.sensors():
        seen = status.sensors.get(sensor.name)
        if not sensor.access:
            state = Text("no access (LS_WEB_USER)", style="red")
        elif seen:
            state = Text("sending", style="green")
        elif status.problem:
            state = Text("unknown", style="yellow")
        else:
            state = Text(f"nothing in {days} days", style="yellow")
        source = "" if sensor.source == "deployed" else " (migrated)"
        table.add_row(sensor.name + source, sensor.host or "-", (seen.hostname if seen else sensor.hostname) or "-",
                      seen.last[:19].replace("T", " ") if seen else "-", state)
    return table


def run_sensors(args) -> int:
    from rich.text import Text
    from tpotctl import sensors as tsensors
    registry = tsensors.Registry()
    console = _console()
    command = args.sensors_command or "list"
    if registry.migrated:
        console.print(Text(f"Taken over from LS_WEB_USER: {', '.join(registry.migrated)}. Add where they are "
                           f"with: tpot sensors set NAME --host ... --ssh-user ...", style=MAGENTA))
    if command == "list":
        status = tsensors.fetch_status(args.days)
        tsensors.link_hostnames(registry, status)
        if not registry.sensors():
            console.print("No sensors yet, deploy one with: tpot sensors add")
        else:
            console.print(sensors_table(registry, status, args.days))
        if status.problem:
            console.print(Text(status.problem, style="yellow"))
        for hostname, seen in sorted(status.unlinked.items()):
            console.print(Text(f"Events from {hostname} ({seen.ip_ext or seen.ip_int}, last {seen.last[:19]}) "
                               f"carry no sensor name: they are older, or the nginx and Logstash images of this "
                               f"HIVE do not pass it on yet (update T-Pot).", style="dim"))
        return 0
    if command == "remove":
        registry.get(args.name)
        if not confirm(f"Revoke the access of {args.name}? It cannot send to this HIVE any more.", args.yes):
            return 1
        note = registry.revoke(args.name)
        say.ok(f"{args.name} is removed, {note}. Nothing was done on the sensor itself.", console.file)
        return 0
    if command == "set":
        registry.update(args.name, host=args.host or "", ssh_user=args.ssh_user or "", ssh_port=args.ssh_port or "",
                        hive_address=args.hive_address or "")
        say.ok(f"{args.name} is updated.", console.file)
        return 0
    if command == "cert":
        return run_sensor_cert(args, registry, console)
    return run_sensor_add(args, registry, console)


def renew_certificate(registry, addresses, console, yes: bool, restart: bool) -> bool:
    import subprocess
    from tpotctl import sensors as tsensors
    current = tsensors.cert_sans(registry.cert)
    sans = list(dict.fromkeys(current + [tsensors.san_of(a) for a in addresses]))
    console.print(f"The new certificate covers: {', '.join(sans)}")
    if not confirm("Issue the certificate of this HIVE anew? Every sensor needs the new one afterwards "
                   "(tpot sensors cert --distribute).", yes):
        return False
    console.print("Creating a 8192 bit key, this can take a minute ...")
    stamp = tsensors.renew_cert(registry, sans)
    say.ok(f"New certificate, the old one is kept as nginx.crt.bak-{stamp}.", console.file)
    if restart and linux_host_ok() and confirm("Restart T-Pot now, so that nginx uses it?", yes):
        subprocess.call(ops.service_command("restart"))
    return True


def linux_host_ok() -> bool:
    return ops.linux_host()


def run_sensor_cert(args, registry, console) -> int:
    import subprocess
    from rich.text import Text
    from tpotctl import sensors as tsensors
    sans = tsensors.cert_sans(registry.cert)
    console.print(f"The certificate of this HIVE covers: {', '.join(sans) or 'nothing'}")
    missing = [a for a in registry.hive_addresses() + args.add if not tsensors.covers(a, sans)]
    if missing:
        console.print(Text(f"Not covered: {', '.join(missing)} - sensors checking with full verification "
                           f"cannot connect there.", style="yellow"))
    if args.renew:
        if not renew_certificate(registry, registry.hive_addresses() + args.add, console, args.yes,
                                 not args.no_restart):
            return 1
    if args.distribute is not None:
        chosen = [registry.get(n) for n in args.distribute] if args.distribute else registry.sensors()
        reachable = [s for s in chosen if s.host and s.ssh_user and s.access]
        for sensor in chosen:
            if sensor not in reachable:
                console.print(Text(f"{sensor.name} is skipped, add where it is: tpot sensors set {sensor.name} "
                                   f"--host ... --ssh-user ...", style="yellow"))
        if not reachable:
            return 1
        with tempfile_inventory(tsensors.inventory_text(reachable)) as inventory:
            code = subprocess.call(tsensors.distribute_command(inventory, not args.no_become_pass), cwd=REPO_DIR)
        if code != 0:
            error("the certificate could not be copied to all sensors, see above")
            return 1
        names = ", ".join(s.name for s in reachable)
        say.ok(f"{names} {'has' if len(reachable) == 1 else 'have'} the new certificate.", console.file)
    return 0


class tempfile_inventory:
    def __init__(self, text: str):
        self.text = text

    def __enter__(self) -> str:
        import tempfile
        handle, self.path = tempfile.mkstemp(prefix="tpot-sensors-", suffix=".ini")
        with os.fdopen(handle, "w") as out:
            out.write(self.text)
        return self.path

    def __exit__(self, *_exc) -> None:
        os.unlink(self.path)


def run_sensor_add(args, registry, console) -> int:
    import subprocess
    from rich.text import Text
    from tpotctl import sensors as tsensors
    interactive = sys.stdin.isatty()
    host = tsensors.check_address(args.host or (ask("IP or name of the sensor: ") if interactive else ""))
    user = tsensors.check_user(args.ssh_user or (ask("User T-Pot was installed with on the sensor: ")
                                                 if interactive else ""))
    proposal = tsensors.default_hive_address(host)
    hive = args.hive_address or (ask(f"IP or name the sensor reaches this HIVE on [{proposal}]: ")
                                 if interactive else "") or proposal
    hive = tsensors.check_address(hive)

    port = tsensors.check_port(args.ssh_port)
    # 1. SSH with a key, on the port T-Pot moves sshd to
    state = tsensors.check_ssh(host, user, port=port)
    if state == "key":
        if not tsensors.has_ssh_key():
            if not confirm("There is no SSH key on this HIVE. Create one?", args.yes):
                return 1
            subprocess.call(tsensors.keygen_command())
        if not interactive:
            error(f"no key login on {user}@{host}, run: {' '.join(tsensors.copy_id_command(host, user, port))}")
            return 1
        console.print(f"Copying the SSH key to {user}@{host}, enter the password of {user} there.")
        subprocess.call(tsensors.copy_id_command(host, user, port))
        state = tsensors.check_ssh(host, user, port=port)
    if state != "ok":
        error(f"cannot log in to {user}@{host} on port {port} with a key "
              f"(is T-Pot installed there, is the address right?)")
        return 1
    say.ok(f"SSH to {user}@{host} works.", console.file)

    # 2. the sensor checks this HIVE against its certificate
    if not tsensors.covers(hive, tsensors.cert_sans(registry.cert)):
        console.print(Text(f"The certificate of this HIVE does not cover {hive}, the sensor would not trust it.",
                           style="yellow"))
        if not renew_certificate(registry, registry.hive_addresses() + [hive], console, args.yes, True):
            return 1
        others = [s.name for s in registry.sensors()]
        if others:
            console.print(Text(f"Remember: {', '.join(others)} need the new certificate as well: "
                               f"tpot sensors cert --distribute", style="yellow"))

    # 3. access first, so the sensor can send as soon as it is up; taken back if the deployment fails
    name = tsensors.new_name(set(registry.records))
    password = tsensors.new_password()
    registry.grant(name, password)
    console.print(f"Deploying {name} to {host}, this reboots the sensor.")
    code = subprocess.call(tsensors.deploy_command(host, user, not args.no_become_pass, port=port),
                           env=tsensors.deploy_env(tsensors.hive_user(name, password), hive), cwd=REPO_DIR)
    if code != 0:
        registry.revoke(name)
        error(f"the deployment failed (see data/deploy_sensor.log), the access for {name} is taken back")
        return 1
    import time as _time
    registry.record(tsensors.Sensor(name=name, host=host, ssh_user=user, ssh_port=port, hive_address=hive,
                                    added=_time.strftime("%Y-%m-%d %H:%M"),
                                    version=ops.env_values().get("TPOT_VERSION", ""), source="deployed"))
    say.ok(f"{name} is deployed to {host} and sends to {hive}.", console.file)
    console.print(Text(f"Its password, shown only now: {password}", style=f"bold {MAGENTA}"))
    console.print("The sensor has it already, keep it only if you want to set the sensor up again by hand.")
    return 0


def run_edition(args) -> int:
    from tpotctl import editions, glyphs
    if args.edition_command in (None, "list"):
        from rich import box
        from rich.table import Table
        name, base = editions.current()
        table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MAGENTA}", pad_edge=False)
        for column in ("", "Edition", "Key", "Role", "RAM", "Disk", "Description"):
            table.add_column(column, no_wrap=column != "Description")
        for choice in editions.available():
            here = choice.key == name.lower()
            table.add_row(glyphs.g("on") if here else "", choice.title, choice.key, choice.role,
                          f"{choice.ram} GB", f"{choice.disk} GB", choice.description,
                          style=f"bold {MAGENTA}" if here else "")
        console = _console()
        console.print(table)
        console.print(f"In use: {name}{' from ' + base if base else ''}. Switch with: tpot edition set EDITION")
        return 0
    if not args.yes and not sys.stdin.isatty():
        error("add --yes to switch the edition without a terminal")
        return 2
    from tpotctl import users as tusers
    try:
        users_ok = any(user.ok for user in tusers.load().users())
    except (tusers.UsersError, OSError):
        users_ok = False
    try:
        plan = editions.plan(args.key, users_ok=users_ok)
        editions.print_plan(plan)
        if plan.needs_web_user and not args.web_user:
            error("a HIVE needs a web user, add one with --web-user NAME (and --password-stdin)")
            return 1
        password = ""
        if plan.needs_web_user:
            tusers.check_name(args.web_user)
            if args.password_file:
                with open(args.password_file, encoding="utf-8") as handle:
                    password = handle.readline().rstrip("\n")
            else:
                password = read_password(args)
        if not confirm(f"Switch to the {plan.target.title} edition? T-Pot stops meanwhile.", args.yes):
            return 1
        add_user = (lambda: tusers.load().add(args.web_user, password)) if plan.needs_web_user else None
        editions.switch(plan, become_file=args.become_file, add_user=add_user)
    except (editions.EditionError, tusers.UsersError, OSError) as err:
        error(str(err))
        return 1
    return 0


def run_llm(args) -> int:
    from tpotctl import llm, settings as tsettings
    current = tsettings.load()
    values, schema = current.values, current.schema
    if args.llm_command is None:
        for service, (title, _model) in llm.SERVICES.items():
            found = llm.service_settings(service, values, schema)
            where = "in your edition" if service in current.services else "not in your edition"
            say.info(f"{title} ({where}): {found['provider']}, model {found['model'] or '(none)'}, "
                     f"{found['url'] or 'the default endpoint of the provider'}")
        say.hint("tpot llm detect | models SERVICE | test SERVICE, or the LLM page of the tpot menu")
        return 0
    if args.llm_command == "detect":
        if args.scan and not args.yes and not sys.stdin.isatty():
            error("add --yes to scan without a terminal")
            return 2
        found = llm.discover(values)
        if args.scan:
            address = llm.scan_network()
            targets = llm.scan_targets(address) if address else []
            if not targets:
                error("this host has no IPv4 network to scan")
                return 1
            if not confirm(f"Connect to all {len(targets)} addresses around {address} on port 11434? On a "
                           f"honeypot that can look like an attack to the IDS of your network.", args.yes):
                return 1
            known = {hit.url for hit in found}
            found += [hit for hit in llm.scan(targets) if hit.url not in known]
        if not found:
            say.warn("No Ollama answers on port 11434 in the usual places.")
            return 1
        for hit in found:
            line = f"{hit.url}  Ollama {hit.version}, {hit.models} model{'s' if hit.models != 1 else ''}"
            (say.ok if hit.reachable_from_docker else say.warn)(line)
            if hit.note:
                say.hint(hit.note)
        say.hint("Set it with: tpot env set GALAH_LLM_SERVER_URL=<url> BEELZEBUB_LLM_HOST=<url>/api/chat")
        return 0
    found = llm.service_settings(args.service, values, schema)
    if args.llm_command == "models":
        try:
            for name in llm.list_models(found["provider"], found["url"], found["api_key"]):
                print(name)
        except llm.LLMError as err:
            error(str(err))
            return 1
        return 0
    result = llm.test(found["provider"], found["url"], found["model"], found["api_key"])
    if not result.ok:
        error(result.problem)
        return 1
    say.ok(f"{found['model']} answered in {result.seconds:.1f} s: {result.answer}")
    say.hint("Asked from this host; the honeypot asks from its container.")
    return 0


CHECKS = os.path.join(REPO_DIR, "docker", "tpotinit", "dist", "bin")
HPTEST = os.path.join(CHECKS, "hptest.sh")
PIPELINE = os.path.join(CHECKS, "attackmap_pipeline_test.sh")


def check_command(args) -> List[str]:
    """The script of a check with its options, the menu runs the same."""
    if args.check_command == "honeypots":
        command = [HPTEST]
        if args.become_file:
            command += ["-B", args.become_file]
        return command + ([args.host] if args.host else [])
    command = [PIPELINE]
    for option in ("types", "ips"):
        if getattr(args, option):
            command += [f"--{option}", getattr(args, option)]
    if args.dry_run:
        command.append("--dry-run")
    if args.become_file:
        command += ["--become-file", args.become_file]
    return command


def run_check(args) -> int:
    if args.check_command is None:
        build_parser().parse_args(["check", "-h"])
        return 2
    ops.require_linux_host(f"check {args.check_command}")
    if args.check_command == "pipeline" and not args.dry_run:
        if not args.yes and not sys.stdin.isatty():
            error("add --yes to inject the test events without a terminal")
            return 2
        if not confirm("Inject test events into the honeypot logs? They become ordinary events in Kibana and "
                       "stay there.", args.yes):
            return 1
    command = check_command(args)
    os.chdir(os.path.expanduser("~"))
    os.execv(command[0], command)
    return 0    # not reached


def run_attackers(args) -> int:
    from tpotctl import events
    found = events.top_sources(hours=args.hours, size=args.count)
    if found.problem:
        error(found.problem)
        return 1
    if args.plain:
        for source in found.sources:
            print(source.ip)
        return 0
    from rich import box
    from rich.table import Table
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MAGENTA}", pad_edge=False,
                  title=f"Attackers of the last {args.hours} hours", title_justify="left")
    for column in ("Source IP", "Attacks", "Country", "Reputation"):
        table.add_column(column, justify="right" if column == "Attacks" else "left")
    for source in found.sources:
        table.add_row(source.ip, f"{source.count:,}".replace(",", " "), source.country, source.reputation)
    _console().print(table)
    return 0


def envschema_sections():
    from tpotctl import envschema
    return envschema.SECTIONS


def error(text: str) -> None:
    say.error(text)


def fail(text: str) -> None:
    """The end of a command that cannot go on, with exit code 1."""
    say.error(text)
    raise SystemExit(1)


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


def run_install(classic: bool) -> int:
    from tpotctl import installer
    if not sys.platform.startswith("linux"):
        raise ops.OpsError("tpot install installs T-Pot on Linux, see the README for macOS and Windows")
    if installer.installed():
        raise ops.OpsError("T-Pot is installed on this host already, update it with: tpot update")
    if classic:
        os.execv(installer.INSTALL_SH, [installer.INSTALL_SH, "-n"])
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        error("the assistant needs a terminal, install without questions with: install.sh -s -t ...")
        return 2
    from tpotctl.screens.install import run_install as assistant
    return assistant()


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
            say.ok(f"The Python packages of tpot are ready ({os.path.dirname(os.path.dirname(python))}).")
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
        if args.command == "sensors":
            from tpotctl import sensors as tsensors
            try:
                return run_sensors(args)
            except tsensors.SensorsError as err:
                error(str(err))
                return 1
        if args.command == "edition":
            return run_edition(args)
        if args.command == "check":
            return run_check(args)
        if args.command == "attackers":
            return run_attackers(args)
        if args.command == "llm":
            from tpotctl import settings as tsettings
            try:
                return run_llm(args)
            except tsettings.SettingsError as err:
                error(str(err))
                return 1
        if args.command == "install":
            return run_install(args.classic)
        if args.command == "uninstall":
            ops.require_linux_host("uninstall")
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                error("tpot uninstall asks first and needs a terminal, without questions: uninstall.sh -y")
                return 2
            from tpotctl.screens.uninstall import run_uninstall
            return run_uninstall()
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
