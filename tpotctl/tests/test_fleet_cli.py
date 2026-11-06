"""CLI contract for controlled sensor fleet updates."""

import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tpotctl import cli


class FleetParserTest(unittest.TestCase):

    def test_sensors_update_parses_selection_and_run_options(self):
        args = cli.build_parser().parse_args([
            "sensors", "update", "sensor-a", "--parallel", "all", "--dry-run",
            "--become-file", "/tmp/become", "--accept-data-loss-file", "/tmp/losses",
        ])
        self.assertEqual(args.sensors_command, "update")
        self.assertEqual(args.names, ["sensor-a"])
        self.assertEqual(args.parallel, "all")
        self.assertTrue(args.dry_run)
        self.assertEqual(args.become_file, "/tmp/become")
        self.assertEqual(args.accept_data_loss_file, "/tmp/losses")

    def test_sensors_update_defaults_to_safe_sequential_selection(self):
        args = cli.build_parser().parse_args(["sensors", "update"])
        self.assertEqual(args.names, [])
        self.assertFalse(args.all_sensors)
        self.assertEqual(args.parallel, 1)
        self.assertFalse(args.yes)
        self.assertIsNone(args.resume)
        self.assertIsNone(args.discard)

    def test_sensors_update_requires_positive_parallelism(self):
        parser = cli.build_parser()
        for value in ("0", "-1", "many", "1_0", "1001"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                parser.parse_args(["sensors", "update", "--parallel", value])

    def test_sensors_update_parses_numeric_parallelism(self):
        args = cli.build_parser().parse_args(["sensors", "update", "--parallel", "4"])
        self.assertEqual(args.parallel, 4)

    def test_sensors_update_rejects_conflicting_control_and_password_options(self):
        parser = cli.build_parser()
        for options in (
            ["--resume", "--discard"],
            ["--become-file", "/tmp/become", "--no-become-pass"],
            ["--accept-data-loss", "--accept-data-loss-file", "/tmp/losses"],
        ):
            with self.subTest(options=options), self.assertRaises(SystemExit):
                parser.parse_args(["sensors", "update", *options])


class FleetCommandTest(unittest.TestCase):

    @staticmethod
    def args(**overrides):
        values = {
            "names": [],
            "all_sensors": False,
            "parallel": 1,
            "dry_run": False,
            "yes": False,
            "become_file": "",
            "no_become_pass": False,
            "accept_data_loss": False,
            "accept_data_loss_file": "",
            "resume": None,
            "discard": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @staticmethod
    def registry():
        return SimpleNamespace(
            env={"TPOT_VERSION": "24.04.1", "TPOT_REPO_URL": "https://example.test/tpotce",
                 "TPOT_BRANCH": "main"},
            repo_dir="/tmp/tpotce",
        )

    def test_noninteractive_update_requires_yes_before_creating_a_run(self):
        coordinator = Mock()
        with patch.object(cli.sys, "stdin", io.StringIO("")), \
                patch.object(cli.ops, "require_linux_host"), \
                patch.object(cli.ops, "require_config_current"), \
                patch("tpotctl.fleet.FleetCoordinator", return_value=coordinator) as create:
            with self.assertRaises(SystemExit) as caught:
                cli.run_fleet_update(self.args(), self.registry(), Mock())

        self.assertEqual(caught.exception.code, 2)
        create.assert_not_called()
        coordinator.prepare.assert_not_called()

    def test_dry_run_prepares_then_discards_without_starting_updates(self):
        run = {
            "run_id": "a" * 32,
            "phase": "prepared",
            "targets": [{
                "name": "sensor-a", "phase": "prepared", "previous_version": "24.04.0",
                "losses": [], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = Mock()
        coordinator.prepare.return_value = run
        coordinator.discard.return_value = {"discarded": True}
        coordinator.store.list_runs.return_value = []
        with patch.object(cli.ops, "require_linux_host"), \
                patch.object(cli.ops, "require_config_current"), \
                patch("tpotctl.fleet.FleetCoordinator", return_value=coordinator), \
                patch("tpotctl.sensors.fetch_status", return_value=object()), \
                patch.dict("os.environ", {}, clear=False):
            result = cli.run_fleet_update(
                self.args(dry_run=True, yes=True, no_become_pass=True),
                self.registry(), Mock(),
            )

        self.assertEqual(result, 0)
        coordinator.prepare.assert_called_once()
        coordinator.discard.assert_called_once_with("a" * 32)
        coordinator.start.assert_not_called()

    def test_yes_does_not_accept_data_loss_without_explicit_approval(self):
        run = {
            "run_id": "b" * 32,
            "phase": "prepared",
            "targets": [{
                "name": "sensor-a", "phase": "prepared", "previous_version": "24.04.0",
                "losses": ["@@tpot loss Elasticsearch data"], "warnings": [],
            }],
            "warnings": [],
        }
        coordinator = Mock()
        coordinator.prepare.return_value = run
        coordinator.store.list_runs.return_value = []
        with patch.object(cli.ops, "require_linux_host"), \
                patch.object(cli.ops, "require_config_current"), \
                patch("tpotctl.fleet.FleetCoordinator", return_value=coordinator), \
                patch("tpotctl.sensors.fetch_status", return_value=object()), \
                patch.dict("os.environ", {}, clear=False):
            with self.assertRaises(SystemExit) as caught:
                cli.run_fleet_update(
                    self.args(yes=True, no_become_pass=True),
                    self.registry(), Mock(),
                )

        self.assertEqual(caught.exception.code, 2)
        coordinator.start.assert_not_called()

    def test_explicit_loss_acceptance_starts_and_monitors_the_run(self):
        run = {
            "run_id": "c" * 32,
            "phase": "prepared",
            "targets": [{
                "name": "sensor-a", "phase": "prepared", "previous_version": "24.04.0",
                "losses": ["@@tpot loss Elasticsearch data"], "warnings": [],
            }],
            "warnings": [],
        }
        completed = {"run_id": "c" * 32, "phase": "completed", "targets": [], "warnings": []}
        coordinator = Mock()
        coordinator.prepare.return_value = run
        coordinator.store.list_runs.return_value = []
        coordinator.store.read.return_value = completed
        with patch.object(cli.ops, "require_linux_host"), \
                patch.object(cli.ops, "require_config_current"), \
                patch("tpotctl.fleet.FleetCoordinator", return_value=coordinator), \
                patch("tpotctl.sensors.fetch_status", return_value=object()), \
                patch.dict("os.environ", {}, clear=False):
            result = cli.run_fleet_update(
                self.args(yes=True, no_become_pass=True, accept_data_loss=True),
                self.registry(), Mock(),
            )

        self.assertEqual(result, 0)
        coordinator.start.assert_called_once_with(
            "c" * 32,
            accepted_losses={"sensor-a": ["@@tpot loss Elasticsearch data"]},
            password="",
        )

    def test_resume_of_prepared_run_requires_approval_then_starts(self):
        prepared = {
            "run_id": "d" * 32,
            "phase": "prepared",
            "targets": [{
                "name": "sensor-a", "phase": "prepared", "previous_version": "24.04.0",
                "losses": ["@@tpot loss Elasticsearch data"], "warnings": [],
            }],
            "warnings": [],
        }
        completed = {"run_id": "d" * 32, "phase": "completed", "targets": [], "warnings": []}
        coordinator = Mock()
        coordinator.store.list_runs.return_value = [prepared]
        coordinator.store.read.side_effect = [prepared, completed]
        with patch.object(cli.ops, "require_linux_host"), \
                patch.object(cli.ops, "require_config_current"), \
                patch("tpotctl.fleet.FleetCoordinator", return_value=coordinator):
            result = cli.run_fleet_update(
                self.args(resume=True, yes=True, no_become_pass=True, accept_data_loss=True),
                self.registry(), Mock(),
            )

        self.assertEqual(result, 0)
        coordinator.start.assert_called_once_with(
            "d" * 32,
            accepted_losses={"sensor-a": ["@@tpot loss Elasticsearch data"]},
            password="",
        )

    def test_discard_requires_a_saved_stopped_run_and_removes_it(self):
        stopped = {
            "run_id": "e" * 32,
            "phase": "paused",
            "targets": [{
                "name": "sensor-a", "phase": "failed", "previous_version": "24.04.0",
                "losses": [], "warnings": [],
            }],
        }
        coordinator = Mock()
        coordinator.store.list_runs.return_value = [stopped]
        with patch.object(cli.ops, "require_linux_host"), \
                patch.object(cli.ops, "require_config_current"), \
                patch("tpotctl.fleet.FleetCoordinator", return_value=coordinator):
            result = cli.run_fleet_update(
                self.args(discard=True, yes=True, no_become_pass=True),
                self.registry(), Mock(),
            )

        self.assertEqual(result, 0)
        coordinator.discard.assert_called_once_with("e" * 32)
        coordinator.prepare.assert_not_called()

    def test_discard_selection_includes_interrupted_prechecks(self):
        preparing = {
            "run_id": "f" * 32,
            "phase": "preparing",
            "targets": [{
                "name": "sensor-a", "phase": "preparing", "previous_version": "",
                "losses": [], "warnings": [],
            }],
        }
        coordinator = Mock()
        coordinator.store.list_runs.return_value = [preparing]

        selected = cli._fleet_run(coordinator, resume=False, discard=True, console=Mock())

        self.assertEqual(selected, preparing)

    def test_discard_does_not_require_a_sensor_sudo_password(self):
        stopped = {
            "run_id": "e" * 32,
            "phase": "completed",
            "targets": [{
                "name": "sensor-a", "phase": "completed", "previous_version": "24.04.0",
                "losses": [], "warnings": [],
            }],
        }
        coordinator = Mock()
        coordinator.store.list_runs.return_value = [stopped]
        coordinator.discard.return_value = {"run_id": "e" * 32, "discarded": True}
        with patch.object(cli.ops, "require_linux_host"), \
                patch.object(cli.ops, "require_config_current"), \
                patch("tpotctl.fleet.FleetCoordinator", return_value=coordinator), \
                patch.object(cli, "confirm", return_value=True):
            result = cli.run_fleet_update(
                self.args(discard=True, yes=True), self.registry(), Mock())

        self.assertEqual(result, 0)
        coordinator.discard.assert_called_once_with("e" * 32)

    def test_dispatch_reports_fleet_errors_without_traceback(self):
        from tpotctl import fleet
        with patch.object(cli, "run_sensors", side_effect=fleet.FleetError("fleet unavailable")), \
                patch.object(cli, "error") as report:
            result = cli.dispatch(["sensors", "update"])

        self.assertEqual(result, 1)
        report.assert_called_once_with("fleet unavailable")

    def test_fleet_host_and_configuration_gates_return_environment_exit_code(self):
        with patch.object(cli.os, "geteuid", return_value=1000), \
                patch.object(cli.ops, "require_linux_host", side_effect=cli.ops.OpsError("not a T-Pot host")), \
                patch.object(cli, "error") as report:
            result = cli.dispatch(["sensors", "update"])

        self.assertEqual(result, 3)
        report.assert_called_once_with("not a T-Pot host")

        with patch.object(cli.os, "geteuid", return_value=1000), \
                patch.object(cli.ops, "require_linux_host"), \
                patch.object(cli.ops, "require_config_current", side_effect=cli.ops.OpsError("configuration drift")), \
                patch.object(cli, "error") as report:
            result = cli.dispatch(["sensors", "update"])

        self.assertEqual(result, 3)
        report.assert_called_once_with("configuration drift")
