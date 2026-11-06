"""Coordinated updates of the sensors registered on a HIVE."""

import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
from typing import Dict, List, Optional, Sequence
from urllib.parse import urlsplit
from datetime import datetime, timezone

from tpotctl import fleet_remote, fleet_transport, sensors


class FleetError(Exception):
    """A fleet update cannot safely continue."""


RUN_ID = re.compile(r"[a-f0-9]{32}")
NAME = re.compile(r"[A-Za-z0-9_.-]{1,512}")
VERSION = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}")
RUN_PHASES = {
    "preparing", "prepared", "precheck_failed", "running", "paused", "aborting", "aborted", "completed",
    "failed",
}
TARGET_PHASES = {
    "preparing", "prepared", "running", "checking", "completed", "failed", "skipped",
    "rollback_running", "rolled_back", "rollback_failed",
}
RUN_KEYS = {
    "schema", "run_id", "phase", "repo", "branch", "version", "parallel", "created_at",
    "warnings", "targets",
}
RUN_OPTIONAL_KEYS = {"started_at", "finished_at", "error", "canary_passed", "abort_requested"}
TARGET_KEYS = {"name", "remote_run_id", "phase", "previous_version", "losses", "warnings"}
TARGET_OPTIONAL_KEYS = {
    "prepared_at", "started_at", "deadline", "exit_code", "archive", "last_error",
    "runtime", "heartbeat_received_at", "completed_observed_at", "attempt", "decision", "approved_losses",
}


def validate_run(state: dict) -> None:
    if (not isinstance(state, dict) or not RUN_KEYS.issubset(state)
            or set(state) - RUN_KEYS - RUN_OPTIONAL_KEYS):
        raise FleetError("The fleet run state has an unexpected schema.")
    if (type(state["schema"]) is not int or state["schema"] != 1
            or not isinstance(state["run_id"], str) or not RUN_ID.fullmatch(state["run_id"])
            or not isinstance(state["phase"], str) or state["phase"] not in RUN_PHASES
            or not isinstance(state["repo"], str) or not state["repo"] or len(state["repo"]) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in state["repo"])
            or not isinstance(state["branch"], str) or not state["branch"] or len(state["branch"]) > 256
            or state["branch"] == "HEAD" or state["branch"].startswith("-")
            or not isinstance(state["version"], str) or not VERSION.fullmatch(state["version"])
            or (type(state["parallel"]) is not int and state["parallel"] != "all")
            or (type(state["parallel"]) is int and not 1 <= state["parallel"] <= 1000)
            or type(state["created_at"]) is not int or state["created_at"] < 0
            or not isinstance(state["warnings"], list) or len(state["warnings"]) > 1000
            or not all(isinstance(item, str) and len(item) <= 4096 for item in state["warnings"])
            or not isinstance(state["targets"], list) or not 1 <= len(state["targets"]) <= 1000):
        raise FleetError("The fleet run state contains invalid values.")
    if "://" in state["repo"]:
        try:
            repo_url = urlsplit(state["repo"])
        except ValueError as err:
            raise FleetError("The fleet repository URL is invalid.") from err
        if repo_url.username or repo_url.password:
            raise FleetError("Fleet repository URLs cannot contain credentials.")
    if any(type(state.get(key)) is not int or state[key] < 0
           for key in ("started_at", "finished_at") if key in state):
        raise FleetError("The fleet run state contains an invalid timestamp.")
    if any(type(state.get(key)) is not bool for key in ("canary_passed", "abort_requested") if key in state):
        raise FleetError("The fleet run state contains an invalid flag.")
    if "error" in state and (not isinstance(state["error"], str) or len(state["error"]) > 4096):
        raise FleetError("The fleet run state contains an invalid error.")
    names = set()
    for target in state["targets"]:
        if (not isinstance(target, dict) or not TARGET_KEYS.issubset(target)
                or set(target) - TARGET_KEYS - TARGET_OPTIONAL_KEYS):
            raise FleetError("A fleet target has an unexpected schema.")
        name, remote_id = target["name"], target["remote_run_id"]
        if (not isinstance(name, str) or not NAME.fullmatch(name) or name in names
                or not isinstance(remote_id, str) or not RUN_ID.fullmatch(remote_id)
                or not isinstance(target["phase"], str) or target["phase"] not in TARGET_PHASES
                or not isinstance(target["previous_version"], str)
                or (target["previous_version"] and not VERSION.fullmatch(target["previous_version"]))
                or not isinstance(target["losses"], list) or len(target["losses"]) > 256
                or not all(isinstance(item, str) and item.startswith("@@tpot loss ") and len(item) <= 4096
                           for item in target["losses"])
                or len(target["losses"]) != len(set(target["losses"]))
                or not isinstance(target["warnings"], list) or len(target["warnings"]) > 256
                or not all(isinstance(item, str) and len(item) <= 4096 for item in target["warnings"])):
            raise FleetError("A fleet target contains invalid values.")
        names.add(name)
        for key in ("prepared_at", "started_at", "deadline", "heartbeat_received_at", "completed_observed_at"):
            if key in target and (type(target[key]) is not int or target[key] < 0):
                raise FleetError("A fleet target contains an invalid timestamp.")
        if "exit_code" in target and type(target["exit_code"]) is not int:
            raise FleetError("A fleet target contains an invalid exit code.")
        if "attempt" in target and (type(target["attempt"]) is not int or target["attempt"] < 1):
            raise FleetError("A fleet target contains an invalid attempt.")
        if "archive" in target and (not isinstance(target["archive"], str) or len(target["archive"]) > 4096):
            raise FleetError("A fleet target contains an invalid archive path.")
        if "last_error" in target and (not isinstance(target["last_error"], str) or len(target["last_error"]) > 4096):
            raise FleetError("A fleet target contains an invalid error.")
        if "decision" in target and target["decision"] not in ("retry", "skip", "rollback", "abort"):
            raise FleetError("A fleet target contains an invalid decision.")
        if "runtime" in target and not isinstance(target["runtime"], dict):
            raise FleetError("A fleet target contains an invalid runtime result.")
        if "approved_losses" in target and (
                not isinstance(target["approved_losses"], list)
                or not all(isinstance(item, str) and item.startswith("@@tpot loss ") and len(item) <= 4096
                           for item in target["approved_losses"])
                or len(target["approved_losses"]) != len(set(target["approved_losses"]))):
            raise FleetError("A fleet target contains invalid data-loss approvals.")


class RunStore:
    """Private, crash-safe HIVE-side state for resumable fleet runs."""

    def __init__(self, home: Optional[Path] = None):
        self.home = Path.home() if home is None else Path(home)
        self.root = self.home / ".local/share/tpotce/fleet"

    def run_path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise FleetError("Invalid fleet run identifier.")
        return self.root / run_id

    def _directory(self, path: Path, *, private: bool = False, create: bool = False) -> None:
        if create:
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass
        try:
            info = path.lstat()
        except OSError as err:
            raise FleetError(f"The fleet directory {path.name} is missing or unreadable.") from err
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or mode & 0o022 or (private and mode != 0o700)):
            raise FleetError(f"The fleet directory {path.name} is not private and owned.")

    def _ensure_root(self) -> None:
        self._directory(self.home)
        current = self.home
        for part in (".local", "share", "tpotce"):
            current /= part
            self._directory(current, create=True)
        self._directory(self.root, private=True, create=True)

    def _read(self, path: Path, run_id: str) -> Dict:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_size > 1_048_576):
                    raise FleetError("The fleet run state is not a private regular file.")
                data = stream.read(1_048_577)
            if len(data) > 1_048_576:
                raise FleetError("The fleet run state is too large.")
            state = json.loads(data.decode("utf-8"))
        except FleetError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as err:
            raise FleetError("The fleet run state is missing, invalid or unreadable.") from err
        validate_run(state)
        if state["run_id"] != run_id:
            raise FleetError("The fleet run state does not match its directory.")
        return state

    def create(self, state: dict) -> None:
        validate_run(state)
        self._ensure_root()
        path = self.run_path(state["run_id"])
        self._directory(path, private=True, create=True)
        if any(path.iterdir()):
            raise FleetError("This fleet run already exists.")
        self._write(path, state)

    def _write(self, path: Path, state: dict) -> None:
        validate_run(state)
        fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path / "state.json")
            folder = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(folder)
            finally:
                os.close(folder)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def write(self, state: dict) -> None:
        validate_run(state)
        path = self.run_path(state["run_id"])
        self._directory(self.root, private=True)
        self._directory(path, private=True)
        state_file = path / "state.json"
        if state_file.exists() or state_file.is_symlink():
            self._read(state_file, state["run_id"])
        elif any(path.iterdir()):
            raise FleetError("The fleet run directory contains unexpected files.")
        self._write(path, state)

    def read(self, run_id: str) -> Dict:
        path = self.run_path(run_id)
        self._directory(self.root, private=True)
        self._directory(path, private=True)
        return self._read(path / "state.json", run_id)

    def list_runs(self) -> List[Dict]:
        try:
            self._directory(self.root, private=True)
        except FleetError as err:
            if not self.root.exists() and not self.root.is_symlink():
                return []
            raise err
        runs = []
        for path in sorted(self.root.iterdir()):
            if not RUN_ID.fullmatch(path.name):
                raise FleetError("The fleet state directory contains an invalid entry.")
            self._directory(path, private=True)
            runs.append(self._read(path / "state.json", path.name))
        return runs

    def remove(self, run_id: str) -> None:
        path = self.run_path(run_id)
        self.read(run_id)
        if {item.name for item in path.iterdir()} != {"state.json"}:
            raise FleetError("The fleet run directory contains unexpected files.")
        (path / "state.json").unlink()
        try:
            path.rmdir()
        except OSError as err:
            raise FleetError("The fleet run directory could not be removed.") from err


def select_targets(registry, status: sensors.Status, *, names: Sequence[str] = (),
                   all_sensors: bool = False, now: Optional[float] = None) -> List[sensors.Sensor]:
    if names and all_sensors:
        raise FleetError("Choose sensor names or --all, not both.")
    records = {sensor.name: sensor for sensor in registry.sensors()}
    if names:
        if len(names) != len(set(names)):
            raise FleetError("A sensor name was selected more than once.")
        selected = []
        for name in names:
            sensor = records.get(name)
            if sensor is None:
                raise FleetError(f"There is no registered sensor {name}.")
            if not sensor.access:
                raise FleetError(f"{name} has no access to this HIVE.")
            selected.append(sensor)
        return selected
    accessible = sorted((sensor for sensor in records.values() if sensor.access), key=lambda sensor: sensor.name)
    if all_sensors:
        return accessible
    return [
        sensor for sensor in accessible
        if sensors.observe(sensor, status, registry.env.get("TPOT_VERSION") or "", now=now).behind
    ]


class FleetCoordinator:

    def __init__(self, *, registry=None, store: Optional[RunStore] = None, remote_call=None, clock=None):
        if remote_call is None:
            remote_call = fleet_transport.call
        self.registry = registry
        self.store = RunStore() if store is None else store
        self.remote_call = remote_call
        self.clock = time.time if clock is None else clock

    @staticmethod
    def _prepared_fields(reply: dict, name: str, version: str) -> dict:
        if (not isinstance(reply, dict) or reply.get("name") != name
                or reply.get("phase") != "prepared" or reply.get("version") != version
                or not isinstance(reply.get("previous_version"), str)
                or not VERSION.fullmatch(reply["previous_version"])
                or not isinstance(reply.get("commit"), str)
                or not re.fullmatch(r"[a-f0-9]{40,64}", reply["commit"])
                or not isinstance(reply.get("losses"), list) or len(reply["losses"]) > 256
                or not all(isinstance(item, str) and item.startswith("@@tpot loss ")
                           and len(item) <= 4096 for item in reply["losses"])
                or len(reply["losses"]) != len(set(reply["losses"]))
                or not isinstance(reply.get("warnings"), list) or len(reply["warnings"]) > 256
                or not all(isinstance(item, str) and len(item) <= 4096 for item in reply["warnings"])):
            raise FleetError(f"{name} returned an invalid precheck result.")
        return {
            "previous_version": reply["previous_version"],
            "losses": reply["losses"],
            "warnings": reply["warnings"],
        }

    def prepare(self, registry, status: sensors.Status, *, repo: str, branch: str, version: str,
                names: Sequence[str] = (), all_sensors: bool = False, parallel=1, password: str = "") -> Dict:
        self.registry = registry
        if (type(parallel) is not int and parallel != "all") or (
                type(parallel) is int and not 1 <= parallel <= 1000):
            raise FleetError("Parallelism must be a positive number or 'all'.")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise FleetError("The target version is invalid.")
        if version != (registry.env.get("TPOT_VERSION") or ""):
            raise FleetError("The target version must match this HIVE.")
        targets = select_targets(registry, status, names=names, all_sensors=all_sensors, now=self.clock())
        if not targets:
            raise FleetError("No sensors match the update selection.")
        run_id = secrets.token_hex(16)
        observations = {
            sensor.name: sensors.observe(sensor, status, version, now=self.clock())
            for sensor in targets
        }
        state = {
            "schema": 1,
            "run_id": run_id,
            "phase": "preparing",
            "repo": repo,
            "branch": branch,
            "version": version,
            "parallel": parallel,
            "created_at": int(self.clock()),
            "warnings": [],
            "targets": [
                {
                    "name": sensor.name,
                    "remote_run_id": secrets.token_hex(16),
                    "phase": "preparing",
                    "previous_version": "",
                    "losses": [],
                    "warnings": ([] if observations[sensor.name].heartbeat else
                                 [f"Heartbeat status is {observations[sensor.name].state}."]),
                    "attempt": 1,
                }
                for sensor in targets
            ],
        }
        validate_run(state)
        self.store.create(state)
        by_name = {sensor.name: sensor for sensor in targets}
        for target in state["targets"]:
            request = {
                "action": "prepare",
                "run_id": target["remote_run_id"],
                "name": target["name"],
                "repo": repo,
                "branch": branch,
                "version": version,
            }
            try:
                reply = self.remote_call(by_name[target["name"]], request, password=password)
                prepared = self._prepared_fields(reply, target["name"], version)
                target.update(
                    phase="prepared",
                    previous_version=prepared["previous_version"],
                    losses=prepared["losses"],
                    warnings=target["warnings"] + prepared["warnings"],
                    prepared_at=int(self.clock()),
                )
            except (FleetError, fleet_remote.RemoteError, fleet_transport.TransportError,
                    sensors.SensorsError) as err:
                target.update(phase="failed", last_error=str(err)[:4096])
            self.store.write(state)
        state["phase"] = "prepared" if all(target["phase"] == "prepared" for target in state["targets"]) \
            else "precheck_failed"
        self.store.write(state)
        return state

    def resolve_failure(self, run_id: str, name: str, choice: str, *, password: str = "") -> Dict:
        state = self.store.read(run_id)
        target = next((item for item in state["targets"] if item["name"] == name), None)
        if target is None:
            raise FleetError(f"{name} is not part of this fleet run.")
        if choice not in ("retry", "skip", "rollback", "abort"):
            raise FleetError("Choose retry, skip, rollback or abort.")
        active = any(item["phase"] in ("running", "checking", "rollback_running") for item in state["targets"])
        if choice == "abort":
            if state["phase"] not in ("prepared", "precheck_failed", "running", "paused", "aborting"):
                raise FleetError("This fleet run can no longer be aborted.")
            state["abort_requested"] = True
            for item in state["targets"]:
                if item["phase"] == "prepared":
                    item.update(phase="skipped", decision="abort")
            self._settle_phase(state)
            self.store.write(state)
            return state
        if active:
            raise FleetError("Wait for the other running sensor updates before resolving this failure.")
        if target["phase"] not in ("failed", "rollback_failed"):
            raise FleetError("Only a failed sensor update can be resolved.")
        target["decision"] = choice
        if choice == "skip":
            target["phase"] = "skipped"
            state["phase"] = "running"
            self.store.write(state)
            self._start_pending(state, password)
            self._settle_phase(state)
            self.store.write(state)
            return self.store.read(run_id)
        if choice == "rollback" or (target["phase"] == "rollback_failed" and choice == "retry"):
            if not target.get("archive"):
                raise FleetError("This update has no verified backup to roll back.")
            target["phase"] = "rollback_running"
            state["phase"] = "running"
            self.store.write(state)
            try:
                reply = self.remote_call(self._sensor(name), {
                    "action": "rollback",
                    "run_id": target["remote_run_id"],
                    "name": name,
                }, password=password)
                if not isinstance(reply, dict) or reply.get("phase") != "rollback_running":
                    raise FleetError(f"{name} returned an invalid rollback-start result.")
            except fleet_remote.RemoteError as err:
                target.update(phase="rollback_failed", last_error=str(err)[:4096])
                state["phase"] = "paused"
            except (fleet_transport.TransportError, sensors.SensorsError, FleetError) as err:
                target["last_error"] = str(err)[:4096]
            self.store.write(state)
            return self.store.read(run_id)
        if target["phase"] != "failed":
            raise FleetError("A failed rollback can only be retried, skipped or aborted.")
        try:
            discarded = self.remote_call(self._sensor(name), {
                "action": "discard",
                "run_id": target["remote_run_id"],
                "name": name,
            })
            if (not isinstance(discarded, dict) or discarded.get("run_id") != target["remote_run_id"]
                    or discarded.get("discarded") is not True):
                raise FleetError(f"{name} did not confirm removal of the failed attempt.")
        except (fleet_remote.RemoteError, fleet_transport.TransportError, sensors.SensorsError) as err:
            target["last_error"] = str(err)[:4096]
            self.store.write(state)
            return state
        remote_run_id = secrets.token_hex(16)
        target.update(
            remote_run_id=remote_run_id,
            phase="preparing",
            previous_version="",
            losses=[],
            warnings=[],
            attempt=target.get("attempt", 1) + 1,
        )
        for key in ("started_at", "deadline", "exit_code", "archive", "last_error", "runtime",
                    "heartbeat_received_at", "completed_observed_at", "approved_losses"):
            target.pop(key, None)
        state["phase"] = "paused"
        self.store.write(state)
        try:
            reply = self.remote_call(self._sensor(name), {
                "action": "prepare",
                "run_id": remote_run_id,
                "name": name,
                "repo": state["repo"],
                "branch": state["branch"],
                "version": state["version"],
            }, password=password)
            prepared = self._prepared_fields(reply, name, state["version"])
            target.update(
                phase="prepared",
                previous_version=prepared["previous_version"],
                losses=prepared["losses"],
                warnings=prepared["warnings"],
                prepared_at=int(self.clock()),
            )
        except (FleetError, fleet_remote.RemoteError, fleet_transport.TransportError,
                sensors.SensorsError) as err:
            target.update(phase="failed", last_error=str(err)[:4096])
        self.store.write(state)
        return state

    def start_retry(self, run_id: str, name: str, *, accepted_losses: Optional[List[str]] = None,
                    password: str = "") -> Dict:
        state = self.store.read(run_id)
        target = next((item for item in state["targets"] if item["name"] == name), None)
        if target is None or target["phase"] != "prepared" or state["phase"] != "paused":
            raise FleetError("This sensor has no prepared retry to start.")
        approved = [] if accepted_losses is None else accepted_losses
        if not isinstance(approved, list) or approved != target["losses"]:
            raise FleetError(f"Every data-loss item reported for {name} requires exact confirmation.")
        target["approved_losses"] = list(approved)
        state["phase"] = "running"
        self.store.write(state)
        self._launch(state, target, password)
        return self.store.read(run_id)

    def extend_deadline(self, run_id: str, name: str) -> Dict:
        state = self.store.read(run_id)
        target = next((item for item in state["targets"] if item["name"] == name), None)
        if (target is None or target["phase"] not in ("running", "checking")
                or state["phase"] not in ("running", "paused")):
            raise FleetError("Only a running sensor update can have its deadline extended.")
        now = int(self.clock())
        deadline = target.get("deadline")
        if type(deadline) is not int:
            raise FleetError("This sensor update has no valid deadline.")
        target["deadline"] = max(now, deadline) + 900
        self.store.write(state)
        return state

    def discard(self, run_id: str) -> Dict:
        state = self.store.read(run_id)
        if any(target["phase"] in ("preparing", "running", "checking", "rollback_running")
               for target in state["targets"]):
            raise FleetError("A running fleet update cannot be discarded.")
        if state["phase"] not in ("prepared", "precheck_failed", "paused", "aborted", "completed", "failed"):
            raise FleetError("This fleet run is not in a discardable state.")
        for target in state["targets"]:
            name = target["name"]
            try:
                reply = self.remote_call(self._sensor(name), {
                    "action": "discard",
                    "run_id": target["remote_run_id"],
                    "name": name,
                })
            except (fleet_remote.RemoteError, fleet_transport.TransportError, sensors.SensorsError) as err:
                raise FleetError(f"Could not discard the remote run for {name}: {err}") from err
            if (not isinstance(reply, dict) or reply.get("run_id") != target["remote_run_id"]
                    or reply.get("discarded") is not True):
                raise FleetError(f"{name} did not confirm removal of its fleet run.")
        self.store.remove(run_id)
        return {"run_id": run_id, "discarded": True}

    def _sensor(self, name: str) -> sensors.Sensor:
        if self.registry is None:
            raise FleetError("The HIVE sensor registry is not available.")
        try:
            sensor = self.registry.get(name)
        except sensors.SensorsError as err:
            raise FleetError(str(err)) from err
        if not sensor.access:
            raise FleetError(f"{name} no longer has access to this HIVE.")
        if not sensor.host or not sensor.ssh_user:
            raise FleetError(f"{name} needs an SSH host and user; set them with tpot sensors set.")
        return sensor

    def _launch(self, state: dict, target: dict, password: str = "") -> None:
        target["phase"] = "running"
        target["started_at"] = int(self.clock())
        target["deadline"] = target["started_at"] + 900
        target.pop("last_error", None)
        state["phase"] = "running"
        self.store.write(state)
        request = {
            "action": "start",
            "run_id": target["remote_run_id"],
            "name": target["name"],
            "accepted_losses": target.get("approved_losses", []),
        }
        try:
            reply = self.remote_call(self._sensor(target["name"]), request, password=password)
            if not isinstance(reply, dict) or reply.get("name") != target["name"] or reply.get("phase") != "running":
                raise FleetError(f"{target['name']} returned an invalid worker-start result.")
        except fleet_remote.RemoteError as err:
            target.update(phase="failed", last_error=str(err)[:4096])
            state["phase"] = "paused"
        except (fleet_transport.TransportError, sensors.SensorsError, FleetError) as err:
            target["last_error"] = str(err)[:4096]
        self.store.write(state)

    def start(self, run_id: str, *, accepted_losses: Optional[Dict[str, List[str]]] = None,
              password: str = "") -> Dict:
        state = self.store.read(run_id)
        if state["phase"] != "prepared" or any(target["phase"] != "prepared" for target in state["targets"]):
            raise FleetError("Every selected sensor must pass precheck before the fleet run can start.")
        accepted_losses = {} if accepted_losses is None else accepted_losses
        if not isinstance(accepted_losses, dict) or set(accepted_losses) - {
                target["name"] for target in state["targets"]}:
            raise FleetError("The data-loss approvals do not match this fleet run.")
        for target in state["targets"]:
            approved = accepted_losses.get(target["name"], [])
            if (not isinstance(approved, list) or not all(isinstance(item, str) for item in approved)
                    or approved != target["losses"]):
                raise FleetError(f"Every data-loss item reported for {target['name']} requires exact confirmation.")
            target["approved_losses"] = list(approved)
        state["canary_passed"] = False
        state["started_at"] = int(self.clock())
        self.store.write(state)
        self._launch(state, state["targets"][0], password)
        return self.store.read(run_id)

    def _start_pending(self, state: dict, password: str) -> None:
        if state.get("abort_requested") or any(target["phase"] in ("failed", "rollback_failed")
                                               for target in state["targets"]):
            return
        active = sum(target["phase"] in ("running", "checking", "rollback_running")
                     for target in state["targets"])
        if not state.get("canary_passed"):
            if active == 0:
                pending = next((target for target in state["targets"] if target["phase"] == "prepared"), None)
                if pending:
                    self._launch(state, pending, password)
            return
        limit = len(state["targets"]) if state["parallel"] == "all" else state["parallel"]
        while active < limit:
            pending = next((target for target in state["targets"] if target["phase"] == "prepared"), None)
            if pending is None:
                break
            self._launch(state, pending, password)
            active += 1

    def _settle_phase(self, state: dict) -> None:
        active = any(target["phase"] in ("running", "checking", "rollback_running")
                     for target in state["targets"])
        failed = any(target["phase"] in ("failed", "rollback_failed") for target in state["targets"])
        pending = any(target["phase"] == "prepared" for target in state["targets"])
        if state.get("abort_requested"):
            for target in state["targets"]:
                if target["phase"] == "prepared":
                    target["phase"] = "skipped"
            state["phase"] = "aborting" if active else "aborted"
        elif failed:
            state["phase"] = "paused"
        elif active or pending:
            state["phase"] = "running"
        elif all(target["phase"] in ("completed", "skipped", "rolled_back") for target in state["targets"]):
            state["phase"] = "completed" if any(
                target["phase"] == "completed" for target in state["targets"]) else "failed"
        if state["phase"] in ("completed", "aborted", "failed"):
            state["finished_at"] = int(self.clock())
        else:
            state.pop("finished_at", None)

    def _cache_version(self, target: dict, version: str, received_at: float) -> None:
        if self.registry is None or not callable(getattr(self.registry, "record", None)):
            return
        sensor = self._sensor(target["name"])
        sensor.version = version
        sensor.version_checked_at = datetime.fromtimestamp(received_at, timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            self.registry.record(sensor)
        except sensors.SensorsError as err:
            warning = f"The version cache could not be saved for {target['name']}: {err}"
            target["warnings"].append(warning[:4096])

    def resume(self, run_id: str, heartbeat_status: sensors.Status, *, password: str = "",
               now: Optional[int] = None) -> Dict:
        state = self.store.read(run_id)
        if state["phase"] not in ("running", "paused", "aborting"):
            raise FleetError("This fleet run has no work that can be resumed.")
        return self.advance(run_id, heartbeat_status, password=password, now=now)

    def advance(self, run_id: str, heartbeat_status: sensors.Status, *, password: str = "",
                now: Optional[int] = None) -> Dict:
        state = self.store.read(run_id)
        if state["phase"] not in ("running", "paused", "aborting"):
            return state
        current = int(self.clock() if now is None else now)
        for target in state["targets"]:
            if target["phase"] not in ("running", "checking", "rollback_running"):
                continue
            try:
                sensor = self._sensor(target["name"])
                request = {"action": "status", "run_id": target["remote_run_id"], "name": target["name"]}
                reply = self.remote_call(sensor, request)
                if not isinstance(reply, dict) or reply.get("name") != target["name"]:
                    raise FleetError(f"{target['name']} returned an invalid worker status.")
                phase = reply.get("phase")
                if phase in ("running", "rollback_running"):
                    target["phase"] = phase
                    continue
                if phase == "rolled_back":
                    target["phase"] = "rolled_back"
                    target.pop("last_error", None)
                    state["phase"] = "paused"
                    continue
                if phase in ("failed", "rollback_failed"):
                    error = reply.get("error", "The sensor worker failed.")
                    if not isinstance(error, str):
                        error = "The sensor worker failed."
                    archive = reply.get("archive", "")
                    if isinstance(archive, str):
                        target["archive"] = archive
                    target.update(phase=phase, last_error=error[:4096])
                    state["phase"] = "paused"
                    continue
                if phase != "completed":
                    raise FleetError(f"{target['name']} returned an unexpected worker phase.")
                archive = reply.get("archive", "")
                if type(reply.get("exit_code")) is not int or reply["exit_code"] != 0:
                    target.update(
                        phase="failed",
                        last_error="The sensor worker did not report a zero exit code.",
                    )
                    if isinstance(archive, str):
                        target["archive"] = archive
                    state["phase"] = "paused"
                    continue
                if not isinstance(archive, str) or not archive:
                    target.update(phase="failed", last_error="The successful worker did not record its backup.")
                    state["phase"] = "paused"
                    continue
                target["phase"] = "checking"
                target.setdefault("completed_observed_at", current)
                target["archive"] = archive
                runtime = self.remote_call(sensor, {
                    "action": "runtime", "run_id": target["remote_run_id"], "name": target["name"],
                })
                if (not isinstance(runtime, dict) or runtime.get("name") != target["name"]
                        or type(runtime.get("ready")) is not bool
                        or not isinstance(runtime.get("problems"), list)
                        or not all(isinstance(item, str) for item in runtime["problems"])):
                    raise FleetError(f"{target['name']} returned an invalid runtime health result.")
                target["runtime"] = runtime
                heartbeat = heartbeat_status.heartbeats.get(target["name"])
                if runtime["ready"] and heartbeat and heartbeat.version == state["version"] \
                        and heartbeat.received_at > target["completed_observed_at"]:
                    target.update(phase="completed", heartbeat_received_at=int(heartbeat.received_at))
                    target.pop("last_error", None)
                    state["canary_passed"] = True
                    self._cache_version(target, state["version"], heartbeat.received_at)
                elif current >= target["deadline"]:
                    details = runtime["problems"] or ["No fresh heartbeat with the target version was received."]
                    target.update(phase="failed", last_error="; ".join(details)[:4096])
                    state["phase"] = "paused"
                else:
                    target["last_error"] = "; ".join(runtime["problems"])[:4096] or \
                        "Waiting for a fresh heartbeat with the target version."
            except fleet_remote.RemoteError as err:
                target["last_error"] = str(err)[:4096]
            except (fleet_transport.TransportError, sensors.SensorsError, FleetError) as err:
                target["last_error"] = str(err)[:4096]
        self.store.write(state)
        self._start_pending(state, password)
        self._settle_phase(state)
        self.store.write(state)
        return state
