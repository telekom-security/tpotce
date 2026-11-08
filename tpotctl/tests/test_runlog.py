"""tpotctl.runlog: the @@tpot marks of the T-Pot scripts, and fuMARK of ui.sh."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from tpotctl import runlog
from tpotctl.tests import isolate

isolate()

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI = os.path.join(REPO, "installer", "lib", "ui.sh")


class ParseMarkTest(unittest.TestCase):

    def test_mark(self):
        self.assertEqual(runlog.parse_mark("@@tpot phase backup Writing the backup\n"),
                         ("phase", ["backup", "Writing", "the", "backup"]))

    def test_plain_line(self):
        self.assertIsNone(runlog.parse_mark("hello @@tpot phase x\n"))
        self.assertIsNone(runlog.parse_mark("@@tpot\n"))


class RunTest(unittest.TestCase):

    def test_phases_with_and_without_title(self):
        run = runlog.Run()
        for line in ["@@tpot phase backup Writing the backup\n", "tar ...\n", "@@tpot phase pull\n",
                     "@@tpot phase backup again\n"]:
            run.feed(line)
        self.assertEqual(run.phases, [("backup", "Writing the backup"), ("pull", "pull")])
        self.assertEqual(run.phase, "backup")

    def test_warnings(self):
        run = runlog.Run()
        run.feed("@@tpot warn pull Not all images could be pulled\n")
        self.assertEqual(run.warnings, ["Not all images could be pulled"])

    def test_changed_checkout_and_failed_phases(self):
        run = runlog.Run()
        for line in ("@@tpot phase git Rolling the checkout back", "@@tpot changed checkout",
                     "@@tpot phase config Restoring the configuration", "@@tpot fail config"):
            run.feed(line)
        self.assertTrue(run.checkout_changed)
        self.assertEqual(run.failed, {"config"})
        self.assertEqual([key for key, _title in run.phases], ["git", "config"])

    def test_changed_back(self):
        run = runlog.Run()
        for line in ("@@tpot changed checkout", "@@tpot changed back"):
            run.feed(line)
        self.assertFalse(run.checkout_changed)
        run.feed("@@tpot changed checkout")
        self.assertTrue(run.checkout_changed)

    def test_plain_lines_are_kept_marks_are_not(self):
        run = runlog.Run()
        run.feed("hello\n")
        run.feed("@@tpot phase x\n")
        self.assertEqual(list(run.lines), ["hello"])

    def test_lines_are_bounded(self):
        run = runlog.Run(keep=3)
        for number in range(10):
            run.feed(f"{number}\n")
        self.assertEqual(list(run.lines), ["7", "8", "9"])

    def check_start(self):
        return {"version": 1, "run_id": "20261017080000a1b2c3", "kind": "start", "names": ["cowrie"]}

    def check_row(self):
        return {"version": 1, "run_id": "20261017080000a1b2c3", "kind": "row",
                "row": {"name": "cowrie", "stages": {
                    name: {"state": "ok", "detail": f"{name} confirmed"} for name in
                    ("port", "answer", "log", "elasticsearch", "geoip", "attack_map", "ewsposter")}}}

    def test_live_rows_are_bound_to_the_announced_run(self):
        run = runlog.Run()
        run.feed("@@tpot check " + json.dumps(self.check_start()))
        self.assertIn("cowrie", getattr(run, "check_rows", {}))
        self.assertEqual(run.check_rows["cowrie"]["stages"]["port"]["state"], "not_reached")
        run.feed("@@tpot check " + json.dumps(self.check_row()))
        self.assertEqual(run.check_rows["cowrie"]["stages"]["port"],
                         {"state": "ok", "detail": "port confirmed"})
        self.assertEqual(len(run.check_rows["cowrie"]["stages"]), 7)
        self.assertEqual(list(run.lines), [])

    def test_bad_live_records_are_reported_without_replacing_valid_rows(self):
        bad = []
        for key, value in (("version", True), ("version", 2), ("run_id", "20261017080000ffffff")):
            row = self.check_row()
            row[key] = value
            bad.append(json.dumps(row))
        for stages in ({}, {"port": {"state": "green", "detail": "wrong"}}):
            row = self.check_row()
            row["row"]["stages"] = stages
            bad.append(json.dumps(row))
        row = self.check_row()
        row["row"]["name"] = "foreign"
        bad.append(json.dumps(row))
        row = self.check_row()
        del row["row"]["stages"]["port"]["detail"]
        bad.append(json.dumps(row))
        row = self.check_row()
        row["row"]["stages"]["port"]["detail"] = "x" * 1025
        bad += [json.dumps(row), "{", "[]", " " * 65537, json.dumps(self.check_start())]
        for data in bad:
            with self.subTest(data=data[:100]):
                run = runlog.Run()
                run.feed("@@tpot check " + json.dumps(self.check_start()))
                run.feed("@@tpot check " + json.dumps(self.check_row()))
                before = copy.deepcopy(getattr(run, "check_rows", {}))
                run.feed("@@tpot check " + data)
                self.assertTrue(run.warnings, "malformed progress must not fail silently")
                self.assertEqual(run.check_rows, before)

    def test_truncated_check_header_is_not_silently_discarded(self):
        run = runlog.Run()
        run.feed("@@tpot check")
        self.assertTrue(run.warnings)


class ImagePullTest(unittest.TestCase):
    def command(self, *args, text):
        return subprocess.run([sys.executable, "-m", "tpotctl.runlog", *args], input=text,
                              capture_output=True, text=True, timeout=10)

    def test_plan_exposes_only_unique_images_and_service_names(self):
        config = {"services": {
            "first": {"image": "example/a:tag", "environment": {"PASSWORD": "not-for-output"}},
            "shared": {"image": "example/a:tag"}, "other": {"image": "example/b:tag"}}}
        result = self.command("image-plan", text=json.dumps(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("@@tpot image-plan "), result.stdout)
        self.assertEqual(json.loads(result.stdout[len("@@tpot image-plan "):]),
                         {"first": "example/a:tag", "shared": "example/a:tag", "other": "example/b:tag"})
        self.assertNotIn("not-for-output", result.stdout + result.stderr)

    def test_build_only_service_is_left_out_of_the_plan(self):
        config = {"services": {"first": {"image": "example/a:tag"},
                               "local": {"build": {"context": "."}}}}
        result = self.command("image-plan", text=json.dumps(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout[len("@@tpot image-plan "):]), {"first": "example/a:tag"})
        for image in ("", "bad image", "bad\nimage", 7):
            config["services"]["local"]["image"] = image
            with self.subTest(image=image):
                self.assertNotEqual(self.command("image-plan", text=json.dumps(config)).returncode, 0)
        lines = ['@@tpot image-plan {"first":"example/a:tag"}',
                 json.dumps({"id": "local", "text": "Skipped - No image to be pulled", "status": "Done"}),
                 json.dumps({"id": "first", "text": "Pulled", "status": "Done"})]
        with tempfile.TemporaryDirectory() as folder:
            result = self.command("pull-output", os.path.join(folder, "pull.log"), "--marks",
                                  text="\n".join(lines) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        updates = [json.loads(line[len("@@tpot pull "):]) for line in result.stdout.splitlines()
                   if line.startswith("@@tpot pull ")]
        self.assertEqual((updates[-1]["done"], updates[-1]["total"]), (1, 1))

    def test_shared_pulls_layers_retries_and_failures_are_counted_honestly(self):
        def event(name, text, status="Working", details=""):
            return json.dumps({"id": name, "text": text, "status": status, "details": details})
        lines = [
            '@@tpot image-plan {"first":"example/a:tag","shared":"example/a:tag","other":"example/b:tag"}',
            event("shared", "Skipped", "Done", "Image is already being pulled by first"),
            event("first", "Pulling"),
            event("abcdef012345", "Pull complete", "Done"),
            event("first", "Pulled", "Done"),
            event("Image example/a:tag", "Pulled", "Done"),
            event("other", "Error", "Error", "registry unavailable"),
            event("other", "Retrying"),
            event("Image example/b:tag", "Skipped", "Done", "Image is already present locally"),
        ]
        raw = "\n".join(lines) + "\n"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "pull.log")
            result = self.command("pull-output", path, "--marks", text=raw)
            self.assertEqual(result.returncode, 0, result.stderr)
            updates = [json.loads(line[len("@@tpot pull "):]) for line in result.stdout.splitlines()
                       if line.startswith("@@tpot pull ")]
            self.assertTrue(updates, result.stdout)
            self.assertEqual([x["done"] for x in updates], [0, 0, 0, 1, 1, 1, 2])
            self.assertEqual([x["total"] for x in updates], [2] * len(updates))
            self.assertIn("registry unavailable", result.stdout)
            self.assertIn("Retrying", result.stdout)
            self.assertTrue(os.path.isfile(path))
            with open(path) as handle:
                self.assertEqual(handle.read(), raw)

    def test_plain_output_is_quiet_and_names_the_complete_log(self):
        raw = ('@@tpot image-plan {"first":"example/a:tag"}\n'
               ' first Pulling\n abcdef012345 Downloading [======> ] 12MB/40MB\n'
               ' first Pulled\n first Pulled\n')
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "pull.log")
            result = self.command("pull-output", path, text=raw)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("1 of 1 images ready", result.stdout)
            self.assertIn(path, result.stdout)
            self.assertNotIn("12MB", result.stdout)
            self.assertEqual(result.stdout.count("1 of 1 images ready"), 1)

    def test_unknown_total_and_invalid_progress_are_not_success(self):
        raw = ('{"id":"Image example/a:tag","status":"Done","text":"Pulled"}\n'
               '{"id":\nregistry denied access\n')
        with tempfile.TemporaryDirectory() as folder:
            result = self.command("pull-output", os.path.join(folder, "pull.log"), text=raw)
            self.assertIn("total unknown", result.stdout)
            self.assertIn("Invalid image progress", result.stdout)
            self.assertIn("registry denied access", result.stdout)

    def test_layer_phases_are_visible_once_without_bytes_or_false_completion(self):
        progress = runlog.ImagePull()
        progress.feed('@@tpot image-plan {"first":"example/a:tag"}')
        updates = []
        for state in ("Downloading", "Extracting", "Pull complete"):
            for layer in ("abcdef012345", "123456abcdef"):
                for current in (12, 24):
                    update = progress.feed(json.dumps({
                        "id": layer, "parent_id": "Image example/a:tag", "text": state,
                        "status": "Done" if state == "Pull complete" else "Working",
                        "current": current, "total": 40, "details": f"{current}MB",
                    }))
                    if update:
                        updates.append(update)
        self.assertEqual([u["activity"] for u in updates],
                         ["example/a:tag: Downloading", "example/a:tag: Extracting"])
        self.assertEqual([u["done"] for u in updates], [0, 0])
        self.assertEqual(progress.feed('first Pulled')["done"], 1)

    def test_global_and_layer_errors_are_not_hidden_as_progress_noise(self):
        progress = runlog.ImagePull()
        progress.feed('@@tpot image-plan {"first":"example/a:tag"}')
        progress.feed("first Pulled")
        for identity, parent, detail in (("Compose", "", "registry denied access"),
                                         ("abcdef012345", "Image example/a:tag", "layer checksum failed")):
            with self.subTest(identity=identity):
                update = progress.feed(json.dumps({"id": identity, "parent_id": parent,
                                                   "status": "Error", "text": "Error", "details": detail}))
                self.assertIsNotNone(update)
                self.assertIn(detail, update["activity"])
        self.assertEqual(progress.snapshot("")["done"], 0)
        with self.assertRaises(ValueError):
            progress.feed("{}")

    def test_long_activity_names_the_full_log_instead_of_silent_truncation(self):
        progress = runlog.ImagePull()
        update = progress.feed("registry error " + "x" * 1500)
        self.assertLessEqual(len(update["activity"]), 1024)
        self.assertIn("full detail in pull log", update["activity"])

    @unittest.skipIf(os.name == "nt", "a terminal is Unix-only")
    def test_classic_terminal_reuses_one_status_line(self):
        import fcntl
        import pty
        import select
        import struct
        import termios
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            raw = '@@tpot image-plan {"first":"example/a:tag"}\nfirst Pulling\nfirst Pulled\n'
            with tempfile.TemporaryDirectory() as folder:
                path = os.path.join(folder, "pull.log")
                with subprocess.Popen([sys.executable, "-m", "tpotctl.runlog", "pull-output", path],
                                      stdin=subprocess.PIPE, stdout=slave, stderr=subprocess.PIPE,
                                      env=dict(os.environ, COLUMNS="80")) as child:
                    _out, error = child.communicate(raw.encode(), timeout=10)
                    self.assertEqual(child.returncode, 0, error)
                output = b""
                while select.select([master], [], [], 0.1)[0]:
                    chunk = os.read(master, 8192)
                    if not chunk:
                        break
                    output += chunk
                self.assertEqual(output.count(b"\n"), 2, output)
                self.assertIn(b"\r1 of 1 images ready", output)
                self.assertLessEqual(max(map(len, output.splitlines()[1:])), 79)
                with open(path) as handle:
                    self.assertEqual(handle.read(), raw)
        finally:
            if slave != -1:
                os.close(slave)
            os.close(master)

    @unittest.skipIf(os.name == "nt", "a terminal is Unix-only")
    def test_ctrl_c_ends_the_status_line_without_a_traceback(self):
        """Ctrl+C during the pull: the reader ends with 130 and a newline after its status line, so the
        summary of install.sh starts on a line of its own."""
        import fcntl
        import pty
        import select
        import signal
        import struct
        import termios
        import time
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            with tempfile.TemporaryDirectory() as folder:
                path = os.path.join(folder, "pull.log")
                with subprocess.Popen([sys.executable, "-m", "tpotctl.runlog", "pull-output", path],
                                      stdin=subprocess.PIPE, stdout=slave, stderr=subprocess.PIPE,
                                      env=dict(os.environ, COLUMNS="80"),
                                      # not ignored, even when the tests run as `... &` (SIG_IGN inherited)
                                      preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL)) as child:
                    child.stdin.write(b'@@tpot image-plan {"first":"example/a:tag"}\nfirst Pulling\n')
                    child.stdin.flush()
                    output, deadline = b"", time.monotonic() + 10
                    while b"Pulling" not in output and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.1)[0]:
                            output += os.read(master, 8192)
                    child.send_signal(signal.SIGINT)
                    self.assertEqual(child.wait(timeout=10), 130, output)
                    error = child.stderr.read()
                    child.stdin.close()
                while select.select([master], [], [], 0.2)[0]:
                    chunk = os.read(master, 8192)
                    if not chunk:
                        break
                    output += chunk
                self.assertNotIn(b"Traceback", error)
                self.assertTrue(output.endswith(b"\n"), output)
        finally:
            if slave != -1:
                os.close(slave)
            os.close(master)

    def test_unwritable_log_stops_the_renderer_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.command("pull-output", folder, text="first Pulled\n")
            self.assertEqual(result.returncode, 1)
            self.assertIn("Image progress failed", result.stderr)
            self.assertNotIn("images ready", result.stdout)


class FuMarkTest(unittest.TestCase):

    def mark(self, env):
        return subprocess.run(["bash", "-c", f'source "{UI}"; fuMARK phase x Y z'], capture_output=True,
                              universal_newlines=True, env=dict(os.environ, TPOT_GUM="off", **env)).stdout

    def test_marks_with_tpot_marks(self):
        self.assertEqual(self.mark({"TPOT_MARKS": "1"}), "@@tpot phase x Y z\n")

    def test_silent_otherwise(self):
        os.environ.pop("TPOT_MARKS", None)
        self.assertEqual(self.mark({}), "")

    def test_install_sh_marks_with_its_option(self):
        out = subprocess.run(["bash", "-c", f'source "{UI}"; myMARKS=y; fuMARK phase x'], capture_output=True,
                             universal_newlines=True, env=dict(os.environ, TPOT_GUM="off")).stdout
        self.assertEqual(out, "@@tpot phase x\n")


if __name__ == "__main__":
    unittest.main()


class EngineTest(unittest.TestCase):

    def test_lines_and_exit_code_of_a_child(self):
        from tpotctl.engine import Engine
        lines = []
        code = Engine(["bash", "-c", 'echo one; echo two >&2; read -r x || echo "no stdin"; exit 3'],
                      env=dict(os.environ, X="1"), cwd=REPO).run(lines.append)
        self.assertEqual(code, 3)
        self.assertEqual([line.rstrip("\n") for line in lines], ["one", "two", "no stdin"])

    def test_bytes_that_are_not_utf8(self):
        """nmap prints the banners of honeypots as they come."""
        from tpotctl.engine import Engine
        lines = []
        code = Engine(["bash", "-c", r"printf 'banner \xff\xfe end\nnext\n'"]).run(lines.append)
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2)
        self.assertIn("end", lines[0])

    def test_children_do_not_inherit_the_venv_guard(self):
        """tpot (or update.sh calling it) run from the menu starts anew: TPOT_VENV of the menu stays out."""
        from tpotctl import bootstrap
        from tpotctl.engine import Engine
        lines = []
        env = dict(os.environ, **{bootstrap.GUARD: "1"})
        Engine(["python3", "-c", f"import os; print(os.environ.get('{bootstrap.GUARD}', 'none'))"],
               env=env).run(lines.append)
        self.assertEqual(lines[0].strip(), "none")
        os.environ[bootstrap.GUARD] = "1"
        self.addCleanup(os.environ.pop, bootstrap.GUARD, None)
        lines.clear()
        Engine(["python3", "-c", f"import os; print(os.environ.get('{bootstrap.GUARD}', 'none'))"]).run(lines.append)
        self.assertEqual(lines[0].strip(), "none")

    def test_child_runs_in_a_session_of_its_own(self):
        """sudo of a child must not reach the terminal of the menu (no /dev/tty)."""
        from tpotctl.engine import Engine
        lines = []
        Engine(["python3", "-c", "import os; print(os.getsid(0))"]).run(lines.append)
        self.assertNotEqual(int(lines[0]), os.getsid(0))

    def test_the_pipe_of_the_child_is_closed(self):
        """Every run of the task screen closes the pipe it read (no ResourceWarning, no open fd)."""
        import gc
        import warnings
        from tpotctl.engine import Engine
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            Engine(["bash", "-c", "echo one; exit 3"]).run(lambda _line: None)
            gc.collect()
        self.assertEqual([str(w.message) for w in caught if issubclass(w.category, ResourceWarning)], [])

    def test_missing_command(self):
        from tpotctl.engine import Engine
        lines = []
        self.assertEqual(Engine(["/nonexistent/tool"]).run(lines.append), 127)
        self.assertTrue(lines and "/nonexistent/tool" in lines[0])
