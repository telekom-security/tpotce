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

from tpotctl import installer, ops, say
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
         backup_dir: str = BACKUP_DIR, linux: Optional[bool] = None) -> SwitchPlan:
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
    result = SwitchPlan(choice, name, os.path.join(backup_dir, f"docker-compose_{name.lower()}_{stamp}.yml"))
    if name == "CUSTOM" or not known:
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
    if choice.role == "HIVE" and was == "SENSOR":
        if env.get("TPOT_TYPE") == "SENSOR":
            result.env_changes["TPOT_TYPE"] = "HIVE"
        result.needs_web_user = not users_ok
    return result


def _mark(key: str, title: str) -> None:
    if os.environ.get("TPOT_MARKS") == "1":
        print(f"@@tpot phase {key} {title}", flush=True)


def switch(plan: SwitchPlan, repo_dir: str = REPO_DIR, become_file: str = "", run: Callable = subprocess.run,
           linux: Optional[bool] = None) -> None:
    """Stop T-Pot, keep the compose file, swap it, set what .env needs, start T-Pot."""
    linux = ops.linux_host() if linux is None else linux

    def sudo(*args: str, what: str) -> None:
        if become_file:
            with open(become_file, encoding="utf-8") as password:
                run(["sudo", "-S", "-p", "", "-v"], stdin=password, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
        if run(["sudo"] + list(args)).returncode != 0:
            raise EditionError(f"{what} failed: sudo {' '.join(args)}")

    in_use = ops.compose_path(repo_dir)
    if linux:
        _mark("stop", "Stopping T-Pot")
        say.info("Stopping T-Pot ...")
        sudo("systemctl", "stop", "tpot", what="Stopping T-Pot")
    _mark("keep", "Keeping the compose file in use")
    os.makedirs(os.path.dirname(plan.keep_copy), exist_ok=True)
    if os.path.isfile(in_use):
        shutil.copy2(in_use, plan.keep_copy)
        say.ok(f"docker-compose.yml is kept as {plan.keep_copy}.")
    _mark("swap", f"Switching to the {plan.target.title} edition")
    shutil.copyfile(plan.target.path, in_use)
    say.ok(f"docker-compose.yml is the {plan.target.title} edition now.")
    if plan.env_changes:
        from tpotctl import settings
        _mark("env", "Changing .env")
        settings.load(repo_dir).change(plan.env_changes)
        say.ok(", ".join(f"{key}={value}" for key, value in plan.env_changes.items()) + " in .env.")
    if not linux:
        say.info("Start it with: docker compose up -d")
        return
    _mark("prune", "Removing the networks of the old edition")
    sudo("docker", "network", "prune", "-f", what="Removing the old networks")
    _mark("start", "Starting T-Pot")
    say.info("Starting T-Pot, it pulls the images it does not have yet ...")
    try:
        sudo("systemctl", "start", "tpot", what="Starting T-Pot")
    except EditionError as err:
        raise EditionError(f"{err}. Back to the edition before: cp {plan.keep_copy} {in_use}")
    _mark("done", "Done")
    say.ok(f"T-Pot runs the {plan.target.title} edition.")


def print_plan(plan: SwitchPlan, stream=None) -> None:
    out = stream or sys.stdout
    say.info(f"{plan.current} -> {plan.target.title} ({plan.target.key})", out)
    for warning in plan.warnings:
        say.warn(warning, out)
