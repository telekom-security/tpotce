"""Fleet selection and orchestration against synthetic HIVE state."""

import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from tpotctl import fleet, fleet_remote, fleet_transport, sensors


class SelectionTest(unittest.TestCase):

    def setUp(self):
        self.registry = SimpleNamespace(
            env={"TPOT_VERSION": "current"},
            sensors=lambda: [
                sensors.Sensor("sensor-heartbeat", version="cached", version_checked_at=""),
                sensors.Sensor("sensor-cache", version="old", version_checked_at="2026-10-01T10:00:00Z"),
                sensors.Sensor("sensor-current", version="current", version_checked_at="2026-10-01T10:00:00Z"),
                sensors.Sensor("sensor-no-access", version="old", version_checked_at="2026-10-01T10:00:00Z",
                               access=False),
                sensors.Sensor("sensor-unknown"),
            ],
        )
        self.status = sensors.Status(heartbeats={
            "sensor-heartbeat": sensors.Heartbeat(
                received_at=1, version="old", started_at="2026-10-01T10:00:00Z",
                sensor_at="2026-10-01T10:00:00Z", sequence=1),
        })

    def test_default_selects_only_accessible_sensors_verified_behind(self):
        selected = fleet.select_targets(self.registry, self.status)
        self.assertEqual([sensor.name for sensor in selected], ["sensor-cache", "sensor-heartbeat"])

    def test_all_selects_every_accessible_sensor(self):
        selected = fleet.select_targets(self.registry, self.status, all_sensors=True)
        self.assertEqual([sensor.name for sensor in selected],
                         ["sensor-cache", "sensor-current", "sensor-heartbeat", "sensor-unknown"])

    def test_explicit_names_allow_current_versions_but_reject_unknown_or_no_access(self):
        selected = fleet.select_targets(self.registry, self.status, names=["sensor-current"])
        self.assertEqual([sensor.name for sensor in selected], ["sensor-current"])
        for name in ("sensor-missing", "sensor-no-access"):
            with self.subTest(name=name), self.assertRaises(fleet.FleetError):
                fleet.select_targets(self.registry, self.status, names=[name])

    def test_names_and_all_are_mutually_exclusive(self):
        with self.assertRaises(fleet.FleetError):
            fleet.select_targets(self.registry, self.status, names=["sensor-current"], all_sensors=True)


class RunStoreTest(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.run_id = "1234567890abcdef1234567890abcdef"
        self.state = {
            "schema": 1,
            "run_id": self.run_id,
            "phase": "prepared",
            "repo": "https://example.invalid/tpotce.git",
            "branch": "master",
            "version": "target-release",
            "parallel": 1,
            "created_at": 1,
            "warnings": [],
            "targets": [{
                "name": "sensor-example",
                "remote_run_id": "abcdef1234567890abcdef1234567890",
                "phase": "prepared",
                "previous_version": "old-release",
                "losses": [],
                "warnings": [],
            }],
        }

    def test_round_trips_only_private_run_state_and_removes_it(self):
        store = fleet.RunStore(self.home)
        store.create(self.state)
        path = store.run_path(self.run_id)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(path / "state.json").st_mode & 0o777, 0o600)
        self.assertEqual(store.read(self.run_id), self.state)
        self.assertEqual(store.list_runs(), [self.state])
        store.remove(self.run_id)
        self.assertFalse(path.exists())

    def test_run_lock_is_exclusive_between_threads(self):
        store = fleet.RunStore(self.home)
        store.create(self.state)
        acquired = threading.Event()

        def acquire():
            with store.lock(self.run_id):
                acquired.set()

        with store.lock(self.run_id):
            thread = threading.Thread(target=acquire)
            thread.start()
            self.assertFalse(acquired.wait(0.1))
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(acquired.is_set())

    def test_rejects_secret_fields_in_persisted_state(self):
        store = fleet.RunStore(self.home)
        state = dict(self.state, password="must not persist")
        with self.assertRaises(fleet.FleetError):
            store.create(state)
        self.assertFalse(store.root.exists())

    def test_refuses_unexpected_run_files_without_removing_them(self):
        store = fleet.RunStore(self.home)
        store.create(self.state)
        path = store.run_path(self.run_id)
        (path / "extra").write_text("preserve", encoding="utf-8")
        with self.assertRaises(fleet.FleetError):
            store.remove(self.run_id)
        self.assertEqual((path / "extra").read_text(encoding="utf-8"), "preserve")

    def test_rejects_a_symlink_state_file(self):
        store = fleet.RunStore(self.home)
        store.create(self.state)
        path = store.run_path(self.run_id) / "state.json"
        path.unlink()
        path.symlink_to(self.home / "outside")
        with self.assertRaises(fleet.FleetError):
            store.read(self.run_id)

    def test_rejects_unhashable_run_and_target_phases_as_invalid_state(self):
        state = dict(self.state, phase=[])
        with self.assertRaises(fleet.FleetError):
            fleet.validate_run(state)
        state = dict(self.state, targets=[dict(self.state["targets"][0], phase=[])])
        with self.assertRaises(fleet.FleetError):
            fleet.validate_run(state)


class PreparationTest(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.sensors = [
            sensors.Sensor("sensor-a", host="sensor-a.example.org", ssh_user="tpot"),
            sensors.Sensor("sensor-b", host="sensor-b.example.org", ssh_user="tpot"),
        ]
        self.registry = SimpleNamespace(
            env={"TPOT_VERSION": "target-release"},
            sensors=lambda: self.sensors,
            get=lambda name: next(item for item in self.sensors if item.name == name),
        )
        self.status = sensors.Status()
        self.calls = []
        self.losses = {}
        self.runtime_ready = True
        self.worker_phases = {}

    def remote(self, sensor, request, *, password=""):
        self.calls.append((sensor.name, dict(request), password))
        if request["action"] == "prepare":
            return {
                "name": sensor.name,
                "phase": "prepared",
                "version": request["version"],
                "previous_version": "old-release",
                "commit": "a" * 40,
                "losses": self.losses.get(sensor.name, []),
                "warnings": [],
            }
        if request["action"] == "start":
            return {"name": sensor.name, "phase": "running"}
        if request["action"] == "status":
            phase = self.worker_phases.get(sensor.name, "completed")
            return {"name": sensor.name, "phase": phase,
                    "exit_code": 0 if phase in ("completed", "rolled_back") else 1,
                    "archive": "/home/tpot/tpot_backups/run_tpot_backup.tar",
                    "error": "synthetic worker failure"}
        if request["action"] == "runtime":
            return {"name": sensor.name, "ready": self.runtime_ready, "problems": []}
        if request["action"] == "discard":
            return {"run_id": request["run_id"], "discarded": True}
        if request["action"] == "rollback":
            return {"name": sensor.name, "phase": "rollback_running", "discarded": True}
        self.fail(f"unexpected remote action: {request['action']}")

    def coordinator(self, remote=None):
        return fleet.FleetCoordinator(
            registry=self.registry, store=fleet.RunStore(self.home),
            remote_call=remote or self.remote, clock=lambda: 1234)

    def test_prepares_all_targets_before_any_start_and_never_saves_password(self):
        result = self.coordinator().prepare(
            self.registry, self.status, names=["sensor-a", "sensor-b"],
            repo="https://example.invalid/tpotce.git", branch="master",
            version="target-release", password="fleet-test")
        self.assertEqual(result["phase"], "prepared")
        self.assertEqual([name for name, _, _ in self.calls], ["sensor-a", "sensor-b"])
        self.assertTrue(all(request["action"] == "prepare" for _, request, _ in self.calls))
        self.assertEqual([password for _, _, password in self.calls], ["fleet-test", "fleet-test"])
        self.assertEqual({target["phase"] for target in result["targets"]}, {"prepared"})
        self.assertTrue(all(target["warnings"] for target in result["targets"]))
        saved = fleet.RunStore(self.home).run_path(result["run_id"]) / "state.json"
        self.assertNotIn("fleet-test", saved.read_text(encoding="utf-8"))

    def test_precheck_collects_errors_from_every_target_and_blocks_start(self):
        def partial(sensor, request, *, password=""):
            self.calls.append((sensor.name, dict(request), password))
            if sensor.name == "sensor-a":
                raise fleet_transport.TransportError("SSH unreachable")
            return {
                "name": sensor.name,
                "phase": "prepared",
                "version": request["version"],
                "previous_version": "old-release",
                "commit": "a" * 40,
                "losses": [],
                "warnings": [],
            }

        result = self.coordinator(partial).prepare(
            self.registry, self.status, all_sensors=True, repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        self.assertEqual(result["phase"], "precheck_failed")
        self.assertEqual([name for name, _, _ in self.calls], ["sensor-a", "sensor-b"])
        self.assertEqual([target["phase"] for target in result["targets"]], ["failed", "prepared"])
        self.assertIn("SSH unreachable", result["targets"][0]["last_error"])

    def test_interrupted_precheck_can_be_discarded(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a"], repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        state["phase"] = "preparing"
        state["targets"][0]["phase"] = "preparing"
        coordinator.store.write(state)

        result = coordinator.discard(state["run_id"])

        self.assertEqual(result, {"run_id": state["run_id"], "discarded": True})
        self.assertFalse(coordinator.store.run_path(state["run_id"]).exists())
        self.assertEqual([request["action"] for _, request, _ in self.calls if request["action"] == "discard"],
                         ["discard"])

    def test_precheck_rejects_duplicate_data_loss_items(self):
        loss = "@@tpot loss images: old image (12 MB)"
        self.losses["sensor-a"] = [loss, loss]
        result = self.coordinator().prepare(
            self.registry, self.status, names=["sensor-a"], repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        self.assertEqual(result["phase"], "precheck_failed")
        self.assertEqual(result["targets"][0]["phase"], "failed")

    def test_start_requires_exact_loss_approval_and_launches_only_the_canary(self):
        loss = "@@tpot loss images: old image (12 MB)"
        self.losses["sensor-a"] = [loss]
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, all_sensors=True, repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release", parallel=2)
        with self.assertRaisesRegex(fleet.FleetError, "data-loss"):
            coordinator.start(state["run_id"])
        self.assertFalse(any(request["action"] == "start" for _, request, _ in self.calls))
        result = coordinator.start(state["run_id"], accepted_losses={"sensor-a": [loss]})
        starts = [(name, request) for name, request, _ in self.calls if request["action"] == "start"]
        self.assertEqual([name for name, _ in starts], ["sensor-a"])
        self.assertEqual(result["targets"][0]["phase"], "running")
        self.assertNotIn("password", (fleet.RunStore(self.home).run_path(state["run_id"])
                                      / "state.json").read_text(encoding="utf-8"))

    def test_lost_start_response_with_sensor_still_prepared_is_actionable_failure(self):
        def lost_start(sensor, request, *, password=""):
            if request["action"] == "start":
                raise fleet_transport.TransportError("SSH disconnected after start")
            if request["action"] == "status":
                return {"name": sensor.name, "phase": "prepared"}
            return self.remote(sensor, request, password=password)

        coordinator = self.coordinator(lost_start)
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a"], repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        coordinator.start(state["run_id"])

        result = coordinator.advance(state["run_id"], self.status, now=1240)

        self.assertEqual(result["phase"], "paused")
        self.assertEqual(result["targets"][0]["phase"], "failed")
        self.assertIn("prepared", result["targets"][0]["last_error"])

    def test_concurrent_starts_launch_only_once(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a"], repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        first_write = threading.Event()
        second_write = threading.Event()
        second_started = threading.Event()
        release_write = threading.Event()
        secondary_writes = 0
        write_count_lock = threading.Lock()
        original_write = coordinator.store.write

        def delayed_write(value):
            nonlocal secondary_writes
            if value["phase"] == "running" and value["targets"][0]["phase"] == "running":
                if threading.current_thread() is first:
                    if not first_write.is_set():
                        first_write.set()
                        if not release_write.wait(5):
                            raise AssertionError("test did not release the first start")
                else:
                    with write_count_lock:
                        secondary_writes += 1
                    second_write.set()
            original_write(value)

        coordinator.store.write = delayed_write
        successes, errors = [], []

        def start():
            try:
                successes.append(coordinator.start(state["run_id"]))
            except fleet.FleetError as err:
                errors.append(err)

        first = threading.Thread(target=start)
        second = threading.Thread(target=lambda: (second_started.set(), start()))
        first.start()
        try:
            self.assertTrue(first_write.wait(2))
            second.start()
            self.assertTrue(second_started.wait(2))
            self.assertFalse(second_write.wait(0.2))
        finally:
            release_write.set()
            first.join(timeout=5)
            if second.ident is not None:
                second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(secondary_writes, 0)
        self.assertEqual([request["action"] for _, request, _ in self.calls if request["action"] == "start"],
                         ["start"])

    def test_canary_must_pass_runtime_and_fresh_heartbeat_before_parallel_start(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, all_sensors=True, repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release", parallel=2)
        coordinator.start(state["run_id"])
        stale = sensors.Status(heartbeats={
            "sensor-a": sensors.Heartbeat(
                received_at=1234, version="target-release", started_at="1970-01-01T00:20:34Z",
                sensor_at="1970-01-01T00:20:34Z", sequence=1),
        })
        result = coordinator.advance(state["run_id"], stale, now=1240)
        self.assertEqual(result["targets"][0]["phase"], "checking")
        self.assertEqual([request["action"] for _, request, _ in self.calls].count("start"), 1)

        fresh = sensors.Status(heartbeats={
            "sensor-a": sensors.Heartbeat(
                received_at=1235, version="target-release", started_at="1970-01-01T00:20:34Z",
                sensor_at="1970-01-01T00:20:35Z", sequence=2),
        })
        result = coordinator.advance(state["run_id"], fresh, now=1241)
        self.assertEqual(result["targets"][0]["phase"], "checking")
        self.assertEqual(result["targets"][0]["completed_observed_at"], 1240)
        self.assertEqual([request["action"] for _, request, _ in self.calls].count("start"), 1)

        fresh = sensors.Status(heartbeats={
            "sensor-a": sensors.Heartbeat(
                received_at=1242, version="target-release", started_at="1970-01-01T00:20:34Z",
                sensor_at="1970-01-01T00:20:42Z", sequence=3),
        })
        result = coordinator.advance(state["run_id"], fresh, now=1243)
        self.assertEqual(result["targets"][0]["phase"], "completed")
        self.assertEqual(result["targets"][1]["phase"], "running")
        self.assertEqual([name for name, request, _ in self.calls
                          if request["action"] == "start"], ["sensor-a", "sensor-b"])

    def test_completed_worker_requires_a_zero_exit_code(self):
        def nonzero_success(sensor, request, *, password=""):
            reply = self.remote(sensor, request, password=password)
            if request["action"] == "status":
                reply["exit_code"] = 1
            return reply

        coordinator = self.coordinator(nonzero_success)
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a"], repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        coordinator.start(state["run_id"])
        result = coordinator.advance(state["run_id"], self.status, now=1240)
        self.assertEqual(result["targets"][0]["phase"], "failed")
        self.assertIn("exit code", result["targets"][0]["last_error"].lower())

    def test_resume_polls_persisted_running_work_and_continues_the_canary(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, all_sensors=True, repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release", parallel=2)
        coordinator.start(state["run_id"])
        fresh = sensors.Status(heartbeats={
            "sensor-a": sensors.Heartbeat(
                received_at=1235, version="target-release", started_at="1970-01-01T00:20:34Z",
                sensor_at="1970-01-01T00:20:35Z", sequence=2),
        })

        result = coordinator.resume(state["run_id"], fresh, now=1241)

        self.assertEqual(result["targets"][0]["phase"], "checking")
        self.assertEqual(result["targets"][1]["phase"], "prepared")

        fresh = sensors.Status(heartbeats={
            "sensor-a": sensors.Heartbeat(
                received_at=1242, version="target-release", started_at="1970-01-01T00:20:34Z",
                sensor_at="1970-01-01T00:20:42Z", sequence=3),
        })
        result = coordinator.resume(state["run_id"], fresh, now=1243)
        self.assertEqual(result["targets"][0]["phase"], "completed")
        self.assertEqual(result["targets"][1]["phase"], "running")

    def test_skip_failed_canary_starts_the_next_sensor_as_a_new_canary(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, all_sensors=True, repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release", parallel=2)
        coordinator.start(state["run_id"])
        self.worker_phases["sensor-a"] = "failed"
        result = coordinator.advance(state["run_id"], self.status, now=1240)
        self.assertEqual(result["phase"], "paused")
        result = coordinator.resolve_failure(state["run_id"], "sensor-a", "skip")
        self.assertEqual(result["targets"][0]["phase"], "skipped")
        self.assertEqual(result["targets"][1]["phase"], "running")
        self.assertFalse(result["canary_passed"])
        self.assertEqual([name for name, request, _ in self.calls
                          if request["action"] == "start"], ["sensor-a", "sensor-b"])

    def test_skipping_the_last_failed_sensor_finishes_the_run(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a"], repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        coordinator.start(state["run_id"])
        self.worker_phases["sensor-a"] = "failed"
        coordinator.advance(state["run_id"], self.status, now=1240)
        result = coordinator.resolve_failure(state["run_id"], "sensor-a", "skip")
        self.assertEqual(result["targets"][0]["phase"], "skipped")
        self.assertEqual(result["phase"], "failed")

    def test_retry_reprepares_with_a_new_remote_run_id_before_starting(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a"], repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        coordinator.start(state["run_id"])
        old_remote_id = state["targets"][0]["remote_run_id"]
        self.worker_phases["sensor-a"] = "failed"
        coordinator.advance(state["run_id"], self.status, now=1240)

        result = coordinator.resolve_failure(state["run_id"], "sensor-a", "retry")
        self.assertEqual(result["targets"][0]["phase"], "prepared")
        self.assertNotEqual(result["targets"][0]["remote_run_id"], old_remote_id)
        self.assertEqual(result["targets"][0]["attempt"], 2)

        result = coordinator.start_retry(state["run_id"], "sensor-a")
        self.assertEqual(result["targets"][0]["phase"], "running")
        self.assertEqual([request["action"] for _, request, _ in self.calls].count("prepare"), 2)
        self.assertEqual([request["action"] for _, request, _ in self.calls].count("start"), 2)

    def test_explicit_rollback_restores_failed_update_before_advancing_canary(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, all_sensors=True, repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release", parallel=2)
        coordinator.start(state["run_id"])
        self.worker_phases["sensor-a"] = "failed"
        result = coordinator.advance(state["run_id"], self.status, now=1240)
        self.assertEqual(result["targets"][0]["phase"], "failed")
        self.assertEqual(result["targets"][0]["archive"], "/home/tpot/tpot_backups/run_tpot_backup.tar")

        result = coordinator.resolve_failure(state["run_id"], "sensor-a", "rollback")
        self.assertEqual(result["targets"][0]["phase"], "rollback_running")
        self.assertEqual([request["action"] for _, request, _ in self.calls
                          if request["action"] == "rollback"], ["rollback"])

        self.worker_phases["sensor-a"] = "rolled_back"
        result = coordinator.advance(state["run_id"], self.status, now=1241)
        self.assertEqual(result["targets"][0]["phase"], "rolled_back")
        self.assertEqual(result["targets"][1]["phase"], "running")
        self.assertFalse(result["canary_passed"])

    def test_deadline_can_be_extended_in_one_explicit_interval(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a"], repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        coordinator.start(state["run_id"])
        coordinator.advance(state["run_id"], self.status, now=1234 + 901)

        result = coordinator.extend_deadline(state["run_id"], "sensor-a")
        self.assertEqual(result["targets"][0]["deadline"], 1234 + 901 + 900 + 900)

    def test_long_update_gets_a_full_heartbeat_window_after_completion(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a"], repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        coordinator.start(state["run_id"])
        completed_at = 1234 + 3600

        result = coordinator.advance(state["run_id"], self.status, now=completed_at)

        target = result["targets"][0]
        self.assertEqual(target["phase"], "checking")
        self.assertEqual(target["checking_started_at"], completed_at)
        self.assertEqual(target["deadline"], completed_at + 900)

    def test_abort_waits_for_active_canary_and_does_not_launch_other_targets(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, all_sensors=True, repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release", parallel=2)
        coordinator.start(state["run_id"])
        result = coordinator.resolve_failure(state["run_id"], "sensor-a", "abort")
        self.assertEqual(result["phase"], "aborting")
        self.assertEqual(result["targets"][1]["phase"], "skipped")

        fresh = sensors.Status(heartbeats={
            "sensor-a": sensors.Heartbeat(
                received_at=1235, version="target-release", started_at="1970-01-01T00:20:34Z",
                sensor_at="1970-01-01T00:20:35Z", sequence=2),
        })
        result = coordinator.advance(state["run_id"], fresh, now=1241)
        self.assertEqual(result["phase"], "aborting")
        fresh = sensors.Status(heartbeats={
            "sensor-a": sensors.Heartbeat(
                received_at=1242, version="target-release", started_at="1970-01-01T00:20:34Z",
                sensor_at="1970-01-01T00:20:42Z", sequence=3),
        })
        result = coordinator.advance(state["run_id"], fresh, now=1243)
        self.assertEqual(result["phase"], "aborted")
        self.assertEqual([name for name, request, _ in self.calls
                          if request["action"] == "start"], ["sensor-a"])

    def test_rollback_failure_remains_distinct_and_can_retry_rollback(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a"], repo="https://example.invalid/tpotce.git",
            branch="master", version="target-release")
        coordinator.start(state["run_id"])
        self.worker_phases["sensor-a"] = "failed"
        coordinator.advance(state["run_id"], self.status, now=1240)
        coordinator.resolve_failure(state["run_id"], "sensor-a", "rollback")

        self.worker_phases["sensor-a"] = "rollback_failed"
        result = coordinator.advance(state["run_id"], self.status, now=1241)
        self.assertEqual(result["targets"][0]["phase"], "rollback_failed")

        result = coordinator.resolve_failure(state["run_id"], "sensor-a", "rollback")
        self.assertEqual(result["targets"][0]["phase"], "rollback_running")
        self.assertEqual([request["action"] for _, request, _ in self.calls
                          if request["action"] == "rollback"], ["rollback", "rollback"])

    def test_discard_removes_remote_attempts_before_removing_hive_state(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a", "sensor-b"],
            repo="https://example.invalid/tpotce.git", branch="master", version="target-release")
        result = coordinator.discard(state["run_id"])
        self.assertEqual(result, {"run_id": state["run_id"], "discarded": True})
        self.assertFalse(coordinator.store.run_path(state["run_id"]).exists())
        self.assertEqual([request["action"] for _, request, _ in self.calls
                          if request["action"] == "discard"], ["discard", "discard"])

    def test_discard_refuses_a_running_fleet_run(self):
        coordinator = self.coordinator()
        state = coordinator.prepare(
            self.registry, self.status, names=["sensor-a"],
            repo="https://example.invalid/tpotce.git", branch="master", version="target-release")
        coordinator.start(state["run_id"])
        with self.assertRaisesRegex(fleet.FleetError, "running"):
            coordinator.discard(state["run_id"])
        self.assertTrue(coordinator.store.run_path(state["run_id"]).exists())
