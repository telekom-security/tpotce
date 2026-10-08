"""Switch the T-Pot edition without the customizer: stop, swap the compose file, start.

The editions are the files in compose/, their texts those of the installer
(installer.EDITIONS). The compose file in use is kept in ~/tpot_backups first, a
customized one too, so nothing is lost. A SENSOR that becomes a HIVE needs a web user
and TPOT_TYPE=HIVE; a HIVE that becomes a SENSOR keeps data/elk, its Elastic Stack
just does not run any more.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from tpotctl import bootstrap, installer, ops, say
from tpotctl.bootstrap import REPO_DIR

BACKUP_DIR = os.path.join(os.path.expanduser("~"), "tpot_backups")
_BASE = re.compile(r"^# customizer:.*\bbase=(\S+)")


class EditionError(Exception):
    """The edition cannot be switched, the text says why."""


@dataclass
class Choice:
    key: str            # standard, sensor, ... (the file compose/<key>.yml)
    title: str
    description: str
    role: str           # HIVE, SENSOR or MOBILE
    ram: int            # GB the README asks for
    disk: int
    path: str


@dataclass
class SwitchPlan:
    target: Choice
    current: str                    # STANDARD, CUSTOM, ... as in the first line of the compose file
    keep_copy: str                  # where the compose file in use is kept
    warnings: List[str] = field(default_factory=list)
    needs_web_user: bool = False
    env_changes: Dict[str, str] = field(default_factory=dict)


def role_of(key: str) -> str:
    return {"sensor": "SENSOR", "mobile": "MOBILE"}.get(key.lower(), "HIVE")


def available(repo_dir: str = REPO_DIR, linux: Optional[bool] = None) -> List[Choice]:
    linux = ops.linux_host() if linux is None else linux
    found = []
    if linux:
        for edition in installer.EDITIONS:
            path = os.path.join(repo_dir, "compose", f"{edition.key}.yml")
            if os.path.isfile(path):
                found.append(Choice(edition.key, edition.title, edition.description, role_of(edition.key),
                                    edition.ram_gb, edition.disk_gb, path))
    else:
        path = os.path.join(repo_dir, "compose", "mac_win.yml")
        if os.path.isfile(path):
            found.append(Choice("mac_win", "macOS / Windows", "T-Pot on Docker Desktop for macOS and Windows.",
                                "HIVE", 16, 256, path))
    return found


def current(repo_dir: str = REPO_DIR) -> Tuple[str, str]:
    """(edition, base): STANDARD and "", or CUSTOM and the edition the customizer started from."""
    try:
        with open(ops.compose_path(repo_dir), encoding="utf-8") as handle:
            head = [handle.readline() for _ in range(2)]
    except OSError:
        return "none", ""
    match = ops._EDITION_RE.match(head[0])
    if not match:
        return "unknown", ""
    base = _BASE.match(head[1])
    return match.group(1), base.group(1) if base else ""


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def plan(target: str, repo_dir: str = REPO_DIR, env: Optional[Dict[str, str]] = None, users_ok: bool = True,
         backup_dir: str = BACKUP_DIR, linux: Optional[bool] = None,
         host_ostype: Optional[str] = None) -> SwitchPlan:
    env = ops.env_values(repo_dir) if env is None else env
    choices = {choice.key: choice for choice in available(repo_dir, linux)}
    choice = choices.get(target.lower())
    if choice is None:
        raise EditionError(f"there is no edition {target} here, choose one of {', '.join(choices) or 'none'}")
    name, base = current(repo_dir)
    in_use = ops.compose_path(repo_dir)
    edition_file = os.path.join(repo_dir, "compose", f"{name.lower()}.yml")
    known = name.lower() in choices or os.path.isfile(edition_file)
    unchanged = known and _read(in_use) == _read(edition_file)
    if name.lower() == choice.key and unchanged:
        raise EditionError(f"the {choice.title} edition runs here already")
    stamp = time.strftime("%Y%m%d%H%M%S")
    # without a docker-compose.yml there is nothing to keep
    keep = "" if name == "none" else os.path.join(backup_dir, f"docker-compose_{name.lower()}_{stamp}.yml")
    result = SwitchPlan(choice, name, keep)
    if name == "none":
        pass
    elif name == "CUSTOM" or not known:
        result.warnings.append(f"Your customized docker-compose.yml ({name}{' from ' + base if base else ''}) "
                               f"is replaced, it is kept as {result.keep_copy}.")
    elif not unchanged:
        result.warnings.append(f"Your docker-compose.yml was changed by hand, it is kept as {result.keep_copy}.")
    was = role_of(base or name) if (base or known) else env.get("TPOT_TYPE", "HIVE")
    if env.get("TPOT_TYPE") == "SENSOR":
        was = "SENSOR"
    if choice.role == "SENSOR" and was != "SENSOR":
        result.warnings.append("Kibana, Elasticsearch and the Attack Map stop, their data in data/elk stays. "
                               "The sensor sends its events after 'tpot sensors add' on its HIVE.")
    if choice.role != "SENSOR" and was == "SENSOR":
        # a HIVE (and MOBILE, which installs with TPOT_TYPE=HIVE too) is no SENSOR any more
        if env.get("TPOT_TYPE") == "SENSOR":
            result.env_changes["TPOT_TYPE"] = "HIVE"
        result.needs_web_user = choice.role == "HIVE" and not users_ok
    # TPOT_OSTYPE goes with the edition: MAC_WIN runs on Docker Desktop, the others on Linux
    host = ops.host_ostype() if host_ostype is None else host_ostype
    os_now = env.get("TPOT_OSTYPE", "linux")
    if choice.key == "mac_win":
        if host != "linux" and os_now != host:
            result.env_changes["TPOT_OSTYPE"] = host
    elif host != "linux":
        # i.e. WSL2 with systemd: tpotinit sees the kernel of Docker Desktop, linux would stop it
        result.warnings.append(f"This host runs {ops.OSTYPE_TEXT[host]}: tpotinit only starts the MAC_WIN "
                               f"edition there.")
    elif os_now != "linux":
        result.env_changes["TPOT_OSTYPE"] = "linux"
    return result


def _mark(key: str, title: str) -> None:
    if os.environ.get("TPOT_MARKS") == "1":
        print(f"@@tpot phase {key} {title}", flush=True)


def switch(plan: SwitchPlan, repo_dir: str = REPO_DIR, become_file: str = "", run: Callable = subprocess.run,
           linux: Optional[bool] = None, add_user: Optional[Callable[[], str]] = None) -> None:
    """Stop T-Pot, keep the compose file, swap it, set what .env needs (and the web user a
    HIVE needs, add_user), start T-Pot.

    Ctrl+C only counts meanwhile, so the switch never stops halfway (the child of the step,
    i.e. systemctl, gets it from the terminal as well and decides): before the swap it goes
    back, docker-compose.yml stays, T-Pot is started again and bootstrap.Interrupted follows;
    after the swap it finishes the switch and says so."""
    interrupts: List[int] = []
    with bootstrap.sigint_to(lambda signum, _frame: interrupts.append(signum)):
        _switch(plan, repo_dir, become_file, run, ops.linux_host() if linux is None else linux, add_user,
                interrupts)


def _state(run: Callable) -> str:
    """What systemd says of tpot.service (active, activating, inactive, failed, ...): after a Ctrl+C
    sudo's rc tells nothing, sudo gives 1 for a ^C at its password prompt as well."""
    try:
        proc = run(["systemctl", "is-active", ops.SERVICE], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                   universal_newlines=True)
    except OSError:
        return "unknown"
    return (getattr(proc, "stdout", "") or "").strip() or "unknown"


_RUNS = ("active", "reloading")


def _switch(plan: SwitchPlan, repo_dir: str, become_file: str, run: Callable, linux: bool,
            add_user: Optional[Callable[[], str]], interrupts: List[int]) -> None:
    def sudo(*args: str, what: str) -> None:
        if become_file:
            with open(become_file, encoding="utf-8") as password:
                run(["sudo", "-S", "-p", "", "-v"], stdin=password, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
        if run(["sudo"] + list(args)).returncode != 0:
            raise EditionError(f"{what} failed: sudo {' '.join(args)}")

    in_use = ops.compose_path(repo_dir)
    # 1. up to the swap a ctrl+c goes back
    try:
        if linux:
            _mark("stop", "Stopping T-Pot")
            say.info("Stopping T-Pot ...")
            sudo("systemctl", "stop", "tpot", what="Stopping T-Pot")
        if plan.keep_copy and os.path.isfile(in_use) and not interrupts:
            _mark("keep", "Keeping the compose file in use")
            os.makedirs(os.path.dirname(plan.keep_copy), exist_ok=True)
            shutil.copy2(in_use, plan.keep_copy)
            say.ok(f"docker-compose.yml is kept as {plan.keep_copy}.")
    except EditionError:
        if not interrupts:          # a stop that failed by itself: nothing is changed
            raise
    if interrupts:
        _go_back(plan, linux, sudo, run)
    _mark("swap", f"Switching to the {plan.target.title} edition")
    shutil.copyfile(plan.target.path, in_use)
    say.ok(f"docker-compose.yml is the {plan.target.title} edition now.")
    back = f"Back to the edition before: cp {plan.keep_copy} {in_use}" if plan.keep_copy else \
        "Nothing to go back to, there was no docker-compose.yml before."
    # 2. from here on a ctrl+c finishes the switch
    # .env and the web user before the start: tpotinit refuses a HIVE without a web user
    try:
        if plan.env_changes:
            from tpotctl import settings
            _mark("env", "Changing .env")
            current = settings.load(repo_dir)
            # the switch is the confirmed step, it stands for the unlock of a fixed key (TPOT_OSTYPE)
            current.change(plan.env_changes, unlocked=[key for key in plan.env_changes if current.can_unlock(key)])
            say.ok(", ".join(f"{key}={value}" for key, value in plan.env_changes.items()) + " in .env.")
        if add_user is not None:
            _mark("user", "Adding the web user")
            say.ok(f"The web user is added, {add_user()}.")
    except Exception as err:      # SettingsError, UsersError, OSError: T-Pot is not started like that
        raise EditionError(f"{err}. T-Pot is stopped. {back}")
    if not linux:
        say.info("Start it with: docker compose up -d")
        return
    _mark("prune", "Removing the networks of the old edition")
    try:
        sudo("docker", "network", "prune", "-f", what="Removing the old networks")
    except EditionError:
        if not interrupts:
            raise
    _mark("start", "Starting T-Pot")
    say.info("Starting T-Pot, it pulls the images it does not have yet ...")
    try:
        sudo("systemctl", "start", "tpot", what="Starting T-Pot")
    except EditionError as err:
        if not interrupts:
            raise EditionError(f"{err}. {back}")
        # ^C at sudo's password prompt (nothing started) or into systemctl (the start goes on in
        # systemd): only systemd knows
        state = _state(run)
        if state not in _RUNS:
            say.warn(f"Ctrl+C came after docker-compose.yml was swapped, it is the {plan.target.title} edition now.")
            if state == "activating":
                raise EditionError(f"T-Pot is still starting the {plan.target.title} edition (Ctrl+C stopped "
                                   f"systemctl waiting): check with tpot status. {back}")
            raise EditionError(f"T-Pot is stopped (systemd says {state}), start it with: tpot start. {back}")
    _mark("done", "Done")
    if interrupts:
        say.warn(f"Ctrl+C came after docker-compose.yml was swapped, so the switch is finished. {back}")
    say.ok(f"T-Pot runs the {plan.target.title} edition.")


def _go_back(plan: SwitchPlan, linux: bool, sudo: Callable, run: Callable) -> None:
    """Ctrl+C before the swap: docker-compose.yml is unchanged, T-Pot runs again (as after a switch).

    What it says comes from systemd: a ^C at sudo's password prompt of the stop left T-Pot running
    (no start, no second prompt), one into systemctl stop let the stop go on."""
    started = ""
    if linux:
        if _state(run) in _RUNS:
            started = ", T-Pot still runs"
        else:
            say.info("Starting T-Pot again ...")
            failed = ""
            try:
                sudo("systemctl", "start", "tpot", what="Starting T-Pot again")
            except EditionError as err:
                failed = f" ({err})"
            state = _state(run)
            if state in _RUNS:
                started = ", T-Pot is started again"
            elif state == "activating":
                started = ", T-Pot is starting again: check with tpot status"
            else:
                started = f", T-Pot is stopped{failed}, start it with: tpot start"
    sys.stderr.write("\n")
    say.warn(f"Stopped before the switch: docker-compose.yml is still the {plan.current} edition{started}.",
             sys.stderr)
    raise bootstrap.Interrupted


def print_plan(plan: SwitchPlan, stream=None) -> None:
    out = stream or sys.stdout
    say.info(f"{plan.current} -> {plan.target.title} ({plan.target.key})", out)
    for warning in plan.warnings:
        say.warn(warning, out)
    for key, value in plan.env_changes.items():
        say.info(f"{key}={value} goes into .env.", out)
