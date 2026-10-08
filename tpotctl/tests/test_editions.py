"""tpot edition: switch the edition without the customizer, in a checkout of its own."""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tpotctl.tests import isolate

isolate()

from tpotctl import editions  # noqa: E402

try:
    import rich  # noqa: F401
except ImportError:
    rich = None
try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Done:
    def __init__(self, code=0, stdout=None):
        self.returncode = code
        self.stdout = stdout


class Calls:
    def __init__(self, fail=()):
        self.commands = []
        self.fail = fail

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        return Done(1 if any(part in command for part in self.fail) else 0)


class EditionsTest(unittest.TestCase):

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="tpot-editions-")
        self.addCleanup(shutil.rmtree, self.repo)
        self.backups = os.path.join(self.repo, "backups")
        os.makedirs(os.path.join(self.repo, "compose"))
        for name in ("standard", "sensor", "mini", "llm", "tarpit", "mobile", "mac_win"):
            shutil.copy(os.path.join(REPO, "compose", f"{name}.yml"), os.path.join(self.repo, "compose"))
        shutil.copy(os.path.join(REPO, "env.example"), os.path.join(self.repo, ".env"))
        self.use("standard")

    def use(self, edition):
        shutil.copy(os.path.join(self.repo, "compose", f"{edition}.yml"), self.compose())

    def compose(self):
        return os.path.join(self.repo, "docker-compose.yml")

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def plan(self, target, env=None, users_ok=True):
        return editions.plan(target, self.repo, env or {"TPOT_TYPE": "HIVE"}, users_ok=users_ok,
                             backup_dir=self.backups, linux=True)

    # what there is ----------------------------------------------------------

    def test_linux_editions(self):
        keys = [choice.key for choice in editions.available(self.repo, linux=True)]
        self.assertEqual(keys, ["standard", "sensor", "llm", "mini", "mobile", "tarpit"])

    def test_off_linux_only_mac_win(self):
        self.assertEqual([c.key for c in editions.available(self.repo, linux=False)], ["mac_win"])

    def test_current(self):
        self.assertEqual(editions.current(self.repo), ("STANDARD", ""))
        with open(self.compose(), "w", encoding="utf-8") as out:
            out.write("# T-Pot: CUSTOM\n# customizer: base=SENSOR add=x\nservices: {}\n")
        self.assertEqual(editions.current(self.repo), ("CUSTOM", "SENSOR"))

    # the plan ---------------------------------------------------------------

    def test_same_edition_is_refused(self):
        with self.assertRaises(editions.EditionError):
            self.plan("standard")

    def test_unknown_edition(self):
        with self.assertRaises(editions.EditionError):
            self.plan("nosuch")

    def test_hive_to_sensor_warns(self):
        plan = self.plan("sensor")
        self.assertTrue(any("tpot sensors add" in warning for warning in plan.warnings))
        self.assertFalse(plan.needs_web_user)

    def test_sensor_to_hive_needs_a_web_user(self):
        self.use("sensor")
        plan = self.plan("standard", env={"TPOT_TYPE": "SENSOR"}, users_ok=False)
        self.assertTrue(plan.needs_web_user)
        self.assertEqual(plan.env_changes, {"TPOT_TYPE": "HIVE"})

    def test_sensor_to_hive_with_a_user(self):
        self.use("sensor")
        plan = self.plan("mini", env={"TPOT_TYPE": "SENSOR"}, users_ok=True)
        self.assertFalse(plan.needs_web_user)

    def test_hive_editions_switch_without_warnings(self):
        self.assertEqual(self.plan("mini").warnings, [])

    def test_custom_file_is_kept(self):
        with open(self.compose(), "w", encoding="utf-8") as out:
            out.write("# T-Pot: CUSTOM\n# customizer: base=STANDARD add=glutton\nservices: {}\n")
        plan = self.plan("standard")
        self.assertTrue(any(plan.keep_copy in warning for warning in plan.warnings))
        editions.switch(plan, self.repo, run=Calls(), linux=True)
        self.assertIn("# T-Pot: CUSTOM", self.read(plan.keep_copy))

    def test_hand_edited_edition_warns(self):
        with open(self.compose(), "a", encoding="utf-8") as out:
            out.write("# my change\n")
        self.assertTrue(any("changed by hand" in warning for warning in self.plan("mini").warnings))

    # the switch -------------------------------------------------------------

    @unittest.skipUnless(yaml, "PyYAML is not installed")
    def test_mac_win_on_a_mac_sets_the_os_type(self):
        from tpotctl import ops, settings
        plan = editions.plan("mac_win", self.repo, ops.env_values(self.repo), backup_dir=self.backups,
                             linux=False, host_ostype="mac")
        self.assertEqual(plan.env_changes.get("TPOT_OSTYPE"), "mac")
        calls = Calls()
        with contextlib.redirect_stdout(io.StringIO()):
            editions.switch(plan, self.repo, run=calls, linux=False)
        self.assertEqual(calls.commands, [])                       # no systemctl, no sudo on a Mac
        self.assertEqual(settings.load(self.repo).values["TPOT_OSTYPE"], "mac")

    def test_a_linux_edition_puts_linux_back(self):
        plan = editions.plan("mini", self.repo, {"TPOT_TYPE": "HIVE", "TPOT_OSTYPE": "mac"},
                             backup_dir=self.backups, linux=True, host_ostype="linux")
        self.assertEqual(plan.env_changes.get("TPOT_OSTYPE"), "linux")

    def test_a_linux_edition_on_docker_desktop_warns_and_keeps_the_os_type(self):
        plan = editions.plan("mini", self.repo, {"TPOT_TYPE": "HIVE", "TPOT_OSTYPE": "win"},
                             backup_dir=self.backups, linux=True, host_ostype="win")
        self.assertNotIn("TPOT_OSTYPE", plan.env_changes)
        self.assertTrue(any("MAC_WIN" in w for w in plan.warnings), plan.warnings)

    def test_linux_stays_linux(self):
        self.assertNotIn("TPOT_OSTYPE", self.plan("mini").env_changes)

    def test_switch_runs_the_steps_in_order(self):
        calls = Calls()
        editions.switch(self.plan("mini"), self.repo, run=calls, linux=True)
        steps = [command for command in calls.commands if command[0] == "sudo"]
        self.assertEqual(steps[0], ["sudo", "systemctl", "stop", "tpot"])
        self.assertIn(["sudo", "docker", "network", "prune", "-f"], calls.commands)
        self.assertEqual(calls.commands[-1], ["sudo", "systemctl", "start", "tpot"])
        self.assertEqual(self.read(self.compose()), self.read(os.path.join(self.repo, "compose", "mini.yml")))

    def test_a_failed_stop_changes_nothing(self):
        before = self.read(self.compose())
        with self.assertRaises(editions.EditionError):
            editions.switch(self.plan("mini"), self.repo, run=Calls(fail=("stop",)), linux=True)
        self.assertEqual(self.read(self.compose()), before)

    @unittest.skipUnless(yaml, "the schema needs PyYAML")
    def test_sensor_to_hive_sets_the_type(self):
        self.use("sensor")
        env = os.path.join(self.repo, ".env")
        with open(env, "a", encoding="utf-8") as out:
            out.write("")
        subprocess.run(["sed", "-i.bak", "s/^TPOT_TYPE=HIVE/TPOT_TYPE=SENSOR/", env], check=True)
        plan = self.plan("standard", env={"TPOT_TYPE": "SENSOR"}, users_ok=True)
        editions.switch(plan, self.repo, run=Calls(), linux=True)
        self.assertIn("TPOT_TYPE=HIVE\n", self.read(env))

    def test_web_user_is_added_before_the_start(self):
        calls = Calls()
        order = []
        original = calls.__call__

        def run(command, **kwargs):
            order.append(" ".join(command[-2:]))
            return original(command, **kwargs)
        editions.switch(self.plan("mini"), self.repo, run=run, linux=True,
                        add_user=lambda: order.append("user") or "used right away")
        self.assertLess(order.index("stop tpot"), order.index("user"))
        self.assertLess(order.index("user"), order.index("start tpot"))

    def test_a_failing_web_user_stops_before_the_start(self):
        from tpotctl import users
        calls = Calls()

        def broken():
            raise users.UsersError("htpasswd is missing")
        with self.assertRaises(editions.EditionError) as caught:
            editions.switch(self.plan("mini"), self.repo, run=calls, linux=True, add_user=broken)
        self.assertNotIn(["sudo", "systemctl", "start", "tpot"], calls.commands)
        self.assertIn("htpasswd is missing", str(caught.exception))
        self.assertIn(self.plan_copy_hint, str(caught.exception))

    plan_copy_hint = "Back to the edition before"

    @unittest.skipUnless(yaml, "the schema needs PyYAML")
    def test_a_settings_error_stops_before_the_start(self):
        from tpotctl import settings
        calls = Calls()
        plan = self.plan("mini")
        plan.env_changes = {"TPOT_TYPE": "HIVE"}
        with mock.patch.object(settings, "load", side_effect=settings.SettingsError("cannot read .env")):
            with self.assertRaises(editions.EditionError):
                editions.switch(plan, self.repo, run=calls, linux=True)
        self.assertNotIn(["sudo", "systemctl", "start", "tpot"], calls.commands)

    def test_sensor_to_mobile_is_no_sensor_any_more(self):
        self.use("sensor")
        plan = self.plan("mobile", env={"TPOT_TYPE": "SENSOR"}, users_ok=False)
        self.assertEqual(plan.env_changes, {"TPOT_TYPE": "HIVE"})
        self.assertFalse(plan.needs_web_user)            # MOBILE has no web UI

    def test_no_compose_file_is_nothing_to_keep(self):
        os.remove(self.compose())
        plan = self.plan("mini")
        self.assertEqual(plan.keep_copy, "")
        self.assertFalse(any("kept as" in w for w in plan.warnings))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            editions.switch(plan, self.repo, run=Calls(), linux=True)
        self.assertNotIn("is kept as", out.getvalue())
        self.assertFalse(os.path.exists(self.backups))
        self.assertEqual(self.read(self.compose()), self.read(os.path.join(self.repo, "compose", "mini.yml")))

    def test_become_file_refreshes_sudo_first(self):
        become = os.path.join(self.repo, "become")
        with open(become, "w", encoding="utf-8") as out:
            out.write("pw\n")
        calls = Calls()
        editions.switch(self.plan("mini"), self.repo, become_file=become, run=calls, linux=True)
        steps = [command for command in calls.commands if command[0] == "sudo"]
        self.assertEqual(steps[0], ["sudo", "-S", "-p", "", "-v"])
        self.assertNotIn("pw", str(calls.commands))

    def test_off_host_only_swaps(self):
        calls = Calls()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            editions.switch(self.plan("mini"), self.repo, run=calls, linux=False)
        self.assertEqual(calls.commands, [])
        self.assertIn("docker compose up -d", out.getvalue())

    def test_marks_with_tpot_marks(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"TPOT_MARKS": "1"}), contextlib.redirect_stdout(out):
            editions.switch(self.plan("mini"), self.repo, run=Calls(), linux=True)
        self.assertIn("@@tpot phase stop", out.getvalue())
        self.assertIn("@@tpot phase start", out.getvalue())

    # ctrl+c: a real SIGINT to this process in a step; the child (systemctl) gets it from the terminal too

    def switch_with_ctrl_c(self, during, code=0, state=None, initial="active", start_leaves=None):
        """switch to mini, a SIGINT while the command with `during` runs, which then exits with code.

        A small systemd answers `systemctl is-active tpot`, T-Pot is `initial` at first: a stop or
        start that exits 0 is done, the interrupted command leaves T-Pot in `state` (None: as its rc
        says), a start that is not interrupted in `start_leaves` (None: active). sudo exits 1 on ^C
        at its password prompt and T-Pot stays as it was, a ^C into systemctl stops its waiting only."""
        import signal
        calls = Calls()
        systemd = {"state": initial}

        def run(command, **kwargs):
            calls(command, **kwargs)
            if command[:2] == ["systemctl", "is-active"]:
                now = systemd["state"]
                return Done(0 if now == "active" else 3, now + "\n")
            result = 0
            if during in command:
                os.kill(os.getpid(), signal.SIGINT)
                result = code
            if result == 0 and "stop" in command:
                systemd["state"] = "inactive"
            if result == 0 and "start" in command:
                systemd["state"] = start_leaves or "active"
            if during in command and state is not None:
                systemd["state"] = state
            return Done(result)
        before = signal.getsignal(signal.SIGINT)
        err, out = io.StringIO(), io.StringIO()
        raised = None
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            try:
                editions.switch(self.plan("mini"), self.repo, run=run, linux=True)
            except (KeyboardInterrupt, editions.EditionError) as interrupt:     # unittest would stop on it
                raised = interrupt
        self.assertEqual(signal.getsignal(signal.SIGINT), before)
        self.last_state = systemd["state"]
        return calls.commands, raised, err.getvalue() + out.getvalue() + str(raised or "")

    def test_ctrl_c_while_stopping_goes_back(self):
        """Before the swap: docker-compose.yml stays, T-Pot is started again, the switch ends as cancelled."""
        before = self.read(self.compose())
        for code, state in ((0, None), (1, "inactive"), (1, "deactivating")):
            # the stop was done / systemctl stopped waiting, the stop goes on in systemd
            with self.subTest(code=code, state=state):
                commands, raised, text = self.switch_with_ctrl_c("stop", code, state)
                self.assertIsInstance(raised, editions.bootstrap.Interrupted, text)
                self.assertEqual(self.read(self.compose()), before)
                self.assertIn(["sudo", "systemctl", "start", "tpot"], commands)
                self.assertNotIn(["sudo", "docker", "network", "prune", "-f"], commands)
                self.assertIn("still the STANDARD edition", text)
                self.assertIn("started again", text)
                self.assertEqual(self.last_state, "active")

    def test_ctrl_c_at_the_password_prompt_of_the_stop_leaves_tpot_running(self):
        """sudo exits 1 on ^C at its prompt, T-Pot never stopped: no start (no second prompt), no
        "T-Pot is stopped" while it runs."""
        commands, raised, text = self.switch_with_ctrl_c("stop", 1, "active")
        self.assertIsInstance(raised, editions.bootstrap.Interrupted, text)
        self.assertNotIn(["sudo", "systemctl", "start", "tpot"], commands)
        self.assertNotIn("is stopped", text)
        self.assertIn("T-Pot still runs", text)

    def test_ctrl_c_goes_back_to_a_tpot_that_did_not_run(self):
        """T-Pot was stopped on purpose (or in a restart loop) before the switch: the way back does
        not start it, neither after a ^C at sudo's prompt nor after one into systemctl stop."""
        before = self.read(self.compose())
        for initial in ("inactive", "failed", "activating"):
            for code, state in ((1, None), (0, None), (1, "inactive")):
                with self.subTest(initial=initial, code=code, state=state):
                    commands, raised, text = self.switch_with_ctrl_c("stop", code, state, initial=initial)
                    self.assertIsInstance(raised, editions.bootstrap.Interrupted, text)
                    self.assertEqual(self.read(self.compose()), before)
                    self.assertNotIn(["sudo", "systemctl", "start", "tpot"], commands)
                    self.assertNotIn("started again", text)
                    self.assertNotIn("Starting T-Pot again", text)
                    self.assertIn("still the STANDARD edition", text)
                    self.assertNotEqual(self.last_state, "active")
                    if self.last_state == initial:      # ^C at sudo's prompt: nothing happened
                        self.assertIn(f"as it was before the switch (systemd says {initial})", text)
                    else:
                        self.assertIn("did not run before the switch and is not started", text)
                        self.assertIn("tpot start", text)

    def test_ctrl_c_while_stopping_and_a_start_still_activating(self):
        """The way back starts T-Pot, systemd still says activating: a hint, no success, no stop."""
        commands, raised, text = self.switch_with_ctrl_c("stop", 0, start_leaves="activating")
        self.assertIsInstance(raised, editions.bootstrap.Interrupted, text)
        self.assertIn(["sudo", "systemctl", "start", "tpot"], commands)
        self.assertIn("starting again", text)
        self.assertIn("tpot status", text)
        self.assertNotIn("started again", text)
        self.assertNotIn("is stopped", text)

    def test_ctrl_c_twice_at_the_password_prompts_says_what_systemd_says(self):
        """^C into the stop (it goes on in systemd), ^C again at the prompt of the start: stopped."""
        import signal
        systemd = {"state": "active"}
        commands = []

        def run(command, **kwargs):
            commands.append(list(command))
            if command[:2] == ["systemctl", "is-active"]:
                return Done(0 if systemd["state"] == "active" else 3, systemd["state"] + "\n")
            os.kill(os.getpid(), signal.SIGINT)
            if "stop" in command:
                systemd["state"] = "inactive"
            return Done(1)
        text = io.StringIO()
        with contextlib.redirect_stderr(text), contextlib.redirect_stdout(text), \
                self.assertRaises(editions.bootstrap.Interrupted):
            editions.switch(self.plan("mini"), self.repo, run=run, linux=True)
        self.assertIn(["sudo", "systemctl", "start", "tpot"], commands)
        self.assertIn("T-Pot is stopped", text.getvalue())
        self.assertIn("tpot start", text.getvalue())
        self.assertNotIn("started again", text.getvalue())

    def test_ctrl_c_after_the_swap_finishes_the_switch(self):
        mini = self.read(os.path.join(self.repo, "compose", "mini.yml"))
        for during, code in (("prune", 1), ("prune", 0), ("start", 0)):
            with self.subTest(during=during, code=code):
                self.use("standard")
                commands, raised, text = self.switch_with_ctrl_c(during, code)
                self.assertIsNone(raised, text)
                self.assertEqual(self.read(self.compose()), mini)
                self.assertEqual(commands[-1], ["sudo", "systemctl", "start", "tpot"])
                self.assertIn("the switch is finished", text)
                self.assertIn("T-Pot runs the Mini edition", text)

    def test_ctrl_c_at_the_password_prompt_of_the_start_is_no_success(self):
        """After the swap sudo asks again (its time stamp ran out), ^C there: T-Pot stays stopped."""
        mini = self.read(os.path.join(self.repo, "compose", "mini.yml"))
        commands, raised, text = self.switch_with_ctrl_c("start", 1, "inactive")
        self.assertIsInstance(raised, editions.EditionError, text)
        self.assertNotIn("T-Pot runs the", text)
        self.assertIn("T-Pot is stopped", text)
        self.assertIn("tpot start", text)
        self.assertEqual(self.read(self.compose()), mini)

    def test_ctrl_c_while_starting_says_to_look(self):
        """systemctl start stops waiting on ctrl+c, the start goes on in systemd: no success yet, a hint."""
        commands, raised, text = self.switch_with_ctrl_c("start", 1, "activating")
        self.assertIsInstance(raised, editions.EditionError, text)
        self.assertNotIn("T-Pot runs the", text)
        self.assertIn("tpot status", text)

    def test_ctrl_c_into_a_start_that_got_through(self):
        """systemctl gave 1 on the ^C, but systemd says active: the switch is done."""
        commands, raised, text = self.switch_with_ctrl_c("start", 1, "active")
        self.assertIsNone(raised, text)
        self.assertIn("T-Pot runs the Mini edition", text)


class EditionCliTest(unittest.TestCase):

    def test_set_refuses_without_yes_off_a_terminal(self):
        from tpotctl import cli
        err = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("")), contextlib.redirect_stderr(err), \
                mock.patch.object(editions, "plan", side_effect=AssertionError("must not plan")):
            self.assertEqual(cli.main(["edition", "set", "mini"]), 2)
        self.assertIn("--yes", err.getvalue())

    def test_sensor_to_hive_adds_the_web_user_from_a_file(self):
        from tpotctl import cli, users

        class Store:
            added = []

            def users(self):
                return []

            def add(self, name, password):
                Store.added.append((name, password))
                return "used right away"

        folder = tempfile.mkdtemp(prefix="tpot-edition-cli-")
        self.addCleanup(shutil.rmtree, folder)
        secret = os.path.join(folder, "pw")
        with open(secret, "w", encoding="utf-8") as out:
            out.write("s3cret-Password\n")
        target = editions.Choice("standard", "Hive", "Everything.", "HIVE", 16, 256, "/x/standard.yml")
        plan = editions.SwitchPlan(target, "SENSOR", "/x/keep.yml", needs_web_user=True,
                                   env_changes={"TPOT_TYPE": "HIVE"})
        def switch(plan, **kwargs):
            kwargs["add_user"]()
        with mock.patch.object(editions, "plan", return_value=plan), \
                mock.patch.object(editions, "switch", side_effect=switch) as switch, \
                mock.patch.object(users, "load", return_value=Store()), \
                contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["edition", "set", "standard", "-y", "--web-user", "anna", "--password-file", secret])
        self.assertEqual(code, 0)
        switch.assert_called_once()
        self.assertEqual(Store.added, [("anna", "s3cret-Password")])

    @unittest.skipUnless(rich, "Rich is not installed, run with the venv of tpot")
    def test_list(self):
        from tpotctl import cli
        out = io.StringIO()
        with mock.patch.object(editions, "available", return_value=[
                editions.Choice("standard", "Hive", "Everything.", "HIVE", 16, 256, "/x/standard.yml"),
                editions.Choice("mini", "Mini", "Less.", "HIVE", 16, 256, "/x/mini.yml")]), \
                mock.patch.object(editions, "current", return_value=("STANDARD", "")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["edition"]), 0)
        self.assertIn("standard", out.getvalue())
        self.assertIn("mini", out.getvalue())


if __name__ == "__main__":
    unittest.main()
