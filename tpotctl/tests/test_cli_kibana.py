"""tpot elastic export, import and restore, and the line of the Kibana objects in tpot elastic status."""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import time
import unittest
from unittest import mock

try:
    from rich.console import Console
except ImportError:              # the system python3 without the venv
    Console = None

from tpotctl import cli, elastic, kibana_objects as ko
from tpotctl.tests.test_kibana_objects import CONFIG, NOW, FakeKibana, Site, as_in_kibana, dash


class FakeSettings:
    def __init__(self, values):
        self.values, self.changes = values, []

    def change(self, changes, unlocked=()):
        self.changes.append(changes)
        return []


@unittest.skipUnless(Console, "Rich is not installed, run with the venv of tpot")
class KibanaCliTest(unittest.TestCase):
    """The site: dashboard a is in Kibana as an older version T-Pot shipped (update), b is missing (new),
    c is the shipped version (current), d was changed in Kibana (yours)."""

    A, B, C, D = dash("a"), dash("b"), dash("c"), dash("d")
    OLD_A = dash("a", body=0)

    def setUp(self):
        self.site = Site(self, [self.A, self.B, self.C, self.D], old=[self.OLD_A])
        self.kibana = FakeKibana([as_in_kibana(CONFIG), as_in_kibana(self.OLD_A), as_in_kibana(self.C),
                                  as_in_kibana(dash("d", title="Mine"))])
        self.compose = ["elasticsearch", "kibana", "tpotinit"]
        self.answers = []
        self.asked = []

    def run_cli(self, *argv, terminal=True, sensor=False, values=None):
        settings = dict(values or {"TPOT_TYPE": "SENSOR" if sensor else "HIVE", "TPOT_ES_RETENTION": "30",
                                   "TPOT_KIBANA_OBJECTS": "update"})
        self.out, self.err = io.StringIO(), io.StringIO()

        def typed(prompt):
            self.asked.append(prompt)
            answer = self.answers.pop(0) if self.answers else "n"
            return answer() if callable(answer) else answer

        stdin = mock.Mock(isatty=lambda: terminal)
        with mock.patch("tpotctl.ops.require_linux_host"), \
                mock.patch("tpotctl.settings.load", return_value=FakeSettings(settings)), \
                mock.patch("tpotctl.ops.compose_names", return_value=list(self.compose)), \
                mock.patch("tpotctl.ops.kibana_dirs", return_value=(self.site.data, self.site.objects)), \
                mock.patch("tpotctl.ops.kibana_export_dir", return_value=self.site.exports), \
                mock.patch("urllib.request.urlopen", self.kibana), \
                mock.patch("tpotctl.elastic.status", return_value=elastic.Status("30", "30", "managed")), \
                mock.patch("tpotctl.cli.ask", side_effect=typed), \
                mock.patch("tpotctl.cli._console", return_value=Console(file=self.out, width=300)), \
                mock.patch("sys.stdin", stdin), \
                contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            try:
                return cli.run_elastic(cli.build_parser().parse_args(["elastic", *argv]))
            except SystemExit as stop:
                return stop.code

    def text(self):
        return " ".join((self.out.getvalue() + self.err.getvalue()).split())

    def title(self, ident):
        return self.kibana.store[f"dashboard:{ident}"]["attributes"]["title"]

    def body(self, ident):
        return self.kibana.store[f"dashboard:{ident}"]["attributes"]["panelsJSON"]

    def write(self, name, text):
        path = os.path.join(self.site.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    # -- the parser -----------------------------------------------------------------------------

    def test_the_parser_knows_the_commands_and_the_modes(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["elastic", "import"]).mode, "update")
        for mode in ("add", "update", "overwrite"):
            self.assertEqual(parser.parse_args(["elastic", "import", "--mode", mode]).mode, mode)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            parser.parse_args(["elastic", "import", "--mode", "keep"])
        self.assertEqual(stop.exception.code, 2)
        args = parser.parse_args(["elastic", "restore", "x.ndjson", "-y"])
        self.assertEqual((args.file, args.yes), ("x.ndjson", True))
        self.assertIsNone(parser.parse_args(["elastic", "export"]).file)

    # -- export ---------------------------------------------------------------------------------

    def test_export_without_a_file_goes_into_tpot_backups(self):
        self.assertEqual(self.run_cli("export"), 0)
        files = self.site.files()
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0], r"^kibana_objects_\d{14}\.ndjson$")
        path = os.path.join(self.site.exports, files[0])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.site.exports).st_mode), 0o700)
        self.assertIn(f"4 Kibana objects exported to {path}.", self.text())
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(len(ko.parse_ndjson(handle.read(), path)), 4)
        self.assertEqual(self.kibana.imports(), [])

    def test_export_to_a_file_is_new_and_0600(self):
        path = os.path.join(self.site.root, "mine.ndjson")
        old = os.umask(0)
        try:
            self.assertEqual(self.run_cli("export", path), 0)
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertIn(f"4 Kibana objects exported to {path}.", self.text())
        self.assertEqual(self.site.files(), [])
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("mine\n")
        self.assertEqual(self.run_cli("export", path), 1)
        self.assertIn("is there already", self.err.getvalue())
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "mine\n")

    def test_export_of_a_kibana_that_does_not_answer_writes_nothing(self):
        for kibana in (FakeKibana(down=True), FakeKibana(export_status=503), FakeKibana()):
            with self.subTest(kibana=kibana.__dict__.get("export_status")):
                self.kibana = kibana
                self.assertEqual(self.run_cli("export"), 1)
                self.assertEqual(self.site.files(), [])
                self.assertIn("Kibana at", self.err.getvalue())

    # -- import ---------------------------------------------------------------------------------

    def test_import_is_update_without_a_question(self):
        self.assertEqual(self.run_cli("import", terminal=False), 0)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.body("a"), self.A["attributes"]["panelsJSON"])     # updated
        self.assertIn("dashboard:b", self.kibana.store)                           # new
        self.assertEqual(self.title("d"), "Mine")                                 # yours stays
        self.assertEqual(len(self.kibana.imports()), 1)
        self.assertTrue(self.kibana.imports()[0].full_url.endswith("overwrite=true"))
        files = self.site.files()
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith("kibana_objects_"))
        text = self.text()
        self.assertIn("1 new, 1 updated, 1 the same, 1 of yours kept", text)
        self.assertIn(os.path.join(self.site.exports, files[0]), text)
        self.assertIn("These objects of yours stay as they are: Mine (dashboard:d)", text)
        self.assertEqual(sorted(self.site.records()), ["dashboard:a", "dashboard:b"])

    def test_import_dry_run_writes_nothing(self):
        for mode in ("add", "update", "overwrite"):
            with self.subTest(mode=mode):
                self.assertEqual(self.run_cli("import", "--mode", mode, "--dry-run", terminal=False), 0)
                self.assertEqual(self.kibana.imports(), [])
                self.assertEqual(self.site.files(), [])
                self.assertFalse(os.path.exists(os.path.join(ko.state_dir(self.site.data), "record.json")))
                text = self.text()
                self.assertIn("Kibana objects: 1 new · 1 to update · 1 the same · 1 of yours", text)
                self.assertIn("Nothing was changed (--dry-run).", text)
        self.assertEqual(self.asked, [])
        self.run_cli("import", "--dry-run")
        self.assertIn("Mode update imports 2, an export comes first. These objects of yours stay as they are: "
                      "Mine (dashboard:d)", self.text())
        self.run_cli("import", "--mode", "overwrite", "--dry-run")
        self.assertIn("Mode overwrite imports 3, an export comes first. These objects of yours are replaced by the "
                      "T-Pot version, the export keeps them: Mine (dashboard:d)", self.text())
        self.run_cli("import", "--mode", "add", "--dry-run")
        self.assertIn("Mode add imports the 1 new ones, the others stay as they are.", self.text())

    def test_import_add_only_creates_and_exports_nothing(self):
        self.assertEqual(self.run_cli("import", "--mode", "add"), 0)
        self.assertEqual(self.asked, [])
        self.assertTrue(all(r.full_url.endswith("overwrite=false") for r in self.kibana.imports()))
        self.assertIn("dashboard:b", self.kibana.store)
        self.assertEqual(self.body("a"), self.OLD_A["attributes"]["panelsJSON"])
        self.assertEqual(self.site.files(), [])
        self.assertIn("1 new, 3 already there", self.text())

    def test_overwrite_lists_yours_and_asks(self):
        self.answers = ["n"]
        self.assertEqual(self.run_cli("import", "--mode", "overwrite"), 1)
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.asked[0], "Overwrite 1 object of yours with the T-Pot version? [y/N] ")
        self.assertIn("These objects of yours are replaced by the T-Pot version, the export keeps them: "
                      "Mine (dashboard:d)", self.text())
        self.assertIn("Nothing was changed.", self.text())
        self.assertEqual((self.kibana.imports(), self.site.files()), ([], []))
        self.answers = ["y"]
        self.assertEqual(self.run_cli("import", "--mode", "overwrite"), 0)
        self.assertEqual(self.title("d"), "Dashboard d")
        self.assertEqual(len(self.site.files()), 1)
        with open(os.path.join(self.site.exports, self.site.files()[0]), encoding="utf-8") as handle:
            self.assertIn('"title": "Mine"', handle.read())                  # the export keeps yours
        self.assertIn("1 of yours replaced", self.text())

    def test_overwrite_without_a_terminal_needs_yes(self):
        self.assertEqual(self.run_cli("import", "--mode", "overwrite", terminal=False), 2)
        self.assertEqual((self.asked, self.kibana.imports(), self.site.files()), ([], [], []))
        self.assertIn("--yes", self.err.getvalue())
        self.assertEqual(self.title("d"), "Mine")
        self.assertEqual(self.run_cli("import", "--mode", "overwrite", "-y", terminal=False), 0)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.title("d"), "Dashboard d")

    def test_overwrite_without_objects_of_yours_does_not_ask(self):
        del self.kibana.store["dashboard:d"]
        self.assertEqual(self.run_cli("import", "--mode", "overwrite", terminal=False), 0)
        self.assertEqual(self.asked, [])
        self.assertIn("dashboard:d", self.kibana.store)

    def test_overwrite_stops_for_objects_of_yours_that_came_after_the_question(self):
        def change_then_yes():
            self.kibana.store["dashboard:c"]["attributes"]["title"] = "Mine too"
            return "y"
        self.answers = [change_then_yes]
        self.assertEqual(self.run_cli("import", "--mode", "overwrite"), 1)
        self.assertIn("1 more object of yours than when the plan was shown (Mine too)", self.err.getvalue())
        self.assertEqual((self.kibana.imports(), self.site.files()), ([], []))
        self.assertEqual(self.title("d"), "Mine")

    def test_an_import_with_errors_names_them_and_exits_1(self):
        self.kibana.fail = {"dashboard:b"}
        self.assertEqual(self.run_cli("import"), 1)
        text = self.text()
        self.assertIn("1 failed", text)
        self.assertIn('not imported: dashboard:b "Dashboard b" (missing_references)', text)
        self.assertEqual(self.body("a"), self.A["attributes"]["panelsJSON"])     # the others got in

    def test_nothing_to_import(self):
        self.kibana = FakeKibana([as_in_kibana(o) for o in (CONFIG, self.A, self.B, self.C, self.D)])
        self.assertEqual(self.run_cli("import"), 0)
        self.assertIn("Nothing to import (0 new · 0 to update · 4 the same · 0 of yours).", self.text())
        self.assertEqual((self.kibana.imports(), self.site.files()), ([], []))

    def test_a_kibana_that_does_not_answer_means_no_import(self):
        self.kibana = FakeKibana(export_status=503)
        self.assertEqual(self.run_cli("import"), 1)
        self.assertIn("Kibana at http://127.0.0.1:64296 answered 503", self.err.getvalue())
        self.assertEqual((self.kibana.imports(), self.site.files()), ([], []))

    def test_another_import_running_is_one_line_and_exit_1(self):
        os.makedirs(ko.state_dir(self.site.data))
        hold = ("import fcntl, os, sys, time\n"
                "handle = os.open(sys.argv[1], os.O_RDONLY | os.O_CREAT, 0o660)\n"
                "fcntl.flock(handle, fcntl.LOCK_EX)\n"
                "print('held', flush=True)\n"
                "time.sleep(3)\n")
        proc = subprocess.Popen([sys.executable, "-c", hold, ko.lock_path(self.site.data)],
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.wait)
        self.assertEqual(proc.stdout.readline().strip(), "held")
        proc.stdout.close()
        with mock.patch.object(ko, "LOCK_WAIT", 0.3):
            for argv in (("import",), ("import", "--mode", "add"), ("restore", self.write("pick.ndjson",
                                                                                         json.dumps(self.A)), "-y")):
                with self.subTest(argv=argv):
                    self.assertEqual(self.run_cli(*argv), 1)
                    self.assertEqual(self.err.getvalue().count("\n"), 1)
                    self.assertIn("another Kibana import is running (tpotinit or tpot elastic), try again in a "
                                  "minute", self.err.getvalue())
        self.assertEqual((self.kibana.requests, self.site.files()), ([], []))

    # -- restore --------------------------------------------------------------------------------

    def test_restore_checks_the_file_asks_and_imports_the_advanced_settings(self):
        mine = dict(CONFIG, attributes={"defaultIndex": "mine"})
        path = self.write("pick.ndjson", json.dumps(as_in_kibana(mine)) + "\n" + json.dumps(dash("a", title="Old"))
                          + "\n")
        self.answers = ["n"]
        self.assertEqual(self.run_cli("restore", path), 1)
        self.assertEqual(self.asked, ["Restore them? [y/N] "])
        self.assertIn(f"{path}: 2 Kibana objects. A restore imports every object of the file, also the advanced "
                      "settings; objects made after it stay, Kibana never deletes on import.", self.text())
        self.assertEqual(self.kibana.requests, [])
        self.answers = ["y"]
        self.site.record(NOW, "dashboard:a")
        self.assertEqual(self.run_cli("restore", path), 0)
        self.assertEqual(self.kibana.store["config:9.5.4"]["attributes"]["defaultIndex"], "mine")
        self.assertEqual(self.title("a"), "Old")
        self.assertEqual(len(self.site.files()), 1)
        self.assertIn("2 Kibana objects restored (export: ", self.text())
        self.assertNotIn("dashboard:a", self.site.records())

    def test_restore_refuses_a_file_that_is_no_export_before_the_question(self):
        for text, why in (("", "holds no Kibana objects"), ("no json\n", "line 1: not a JSON object"),
                          ('{"type": "tag"}\n', "line 1: an object without type or id")):
            with self.subTest(text=text):
                self.assertEqual(self.run_cli("restore", self.write("bad.ndjson", text), "-y"), 1)
                self.assertIn(why, self.err.getvalue())
        self.assertEqual(self.run_cli("restore", os.path.join(self.site.root, "missing.ndjson"), "-y"), 1)
        self.assertIn("No such file or directory", self.err.getvalue())
        self.assertEqual((self.asked, self.kibana.requests, self.site.files()), ([], [], []))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no named pipes here")
    def test_restore_of_a_pipe_is_refused_at_once(self):
        pipe = os.path.join(self.site.root, "pipe.ndjson")
        os.mkfifo(pipe)
        started = time.monotonic()
        self.assertEqual(self.run_cli("restore", pipe, "-y"), 1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("is no regular file", self.err.getvalue())
        self.assertEqual(self.err.getvalue().count("\n"), 1)
        self.assertEqual(self.kibana.requests, [])

    def test_restore_without_a_terminal_needs_yes(self):
        path = self.write("pick.ndjson", json.dumps(dash("a", title="Old")) + "\n")
        self.assertEqual(self.run_cli("restore", path, terminal=False), 2)
        self.assertEqual((self.asked, self.kibana.requests), ([], []))
        self.assertIn("--yes", self.err.getvalue())
        self.assertEqual(self.run_cli("restore", path, "-y", terminal=False), 0)
        self.assertEqual(self.title("a"), "Old")

    # -- where it runs --------------------------------------------------------------------------

    def test_no_kibana_in_this_edition(self):
        self.compose = ["elasticsearch", "tpotinit"]
        path = self.write("pick.ndjson", json.dumps(self.A) + "\n")
        for argv in (("export",), ("import",), ("import", "--dry-run"), ("restore", path, "-y")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli(*argv), 1)
                self.assertIn("This edition runs no Kibana.", self.err.getvalue())
        self.assertEqual((self.kibana.requests, self.site.files()), ([], []))

    def test_a_sensor_is_refused(self):
        for argv in (("export",), ("import",), ("restore", "x.ndjson")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli(*argv, sensor=True), 1)
                self.assertIn("SENSOR", self.err.getvalue())
        self.assertEqual(self.kibana.requests, [])

    def test_only_on_a_t_pot_host(self):
        with mock.patch("tpotctl.ops.linux_host", return_value=False):
            for argv in (["elastic", "export"], ["elastic", "import"], ["elastic", "restore", "x"]):
                with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(cli.dispatch(argv), 1)
                    self.assertIn("needs a T-Pot host", err.getvalue())

    # -- status ---------------------------------------------------------------------------------

    def test_status_names_the_mode_and_what_an_import_would_bring(self):
        self.assertEqual(self.run_cli("status"), 0)
        self.assertIn("Kibana objects     TPOT_KIBANA_OBJECTS=update · 1 new · 1 to update · 1 the same · "
                      "1 of yours", self.out.getvalue())
        self.assertEqual((self.kibana.imports(), self.site.files()), ([], []))
        self.assertEqual(self.run_cli(), 0)                       # status is the default
        self.assertIn("TPOT_KIBANA_OBJECTS=update ·", self.out.getvalue())

    def test_status_reads_kibana_with_the_short_timeout(self):
        seen, kibana = [], self.kibana

        def opener(request, timeout=None):
            seen.append(timeout)
            return kibana(request, timeout)
        self.kibana = opener
        self.run_cli("status")
        self.assertEqual(seen, [ko.STATUS_TIMEOUT])
        self.assertEqual(ko.STATUS_TIMEOUT, 5)

    def test_status_names_a_first_import_that_gave_up(self):
        ko._write_state(self.site.data, os.path.basename(ko.given_up_path(self.site.data)),
                        {"version": 1, "refused": ["dashboard:b", "dashboard:x"], "at": "2026-10-16T10:00:00Z",
                         "shipped": ko.shipped_sha(self.site.objects)}, ko.TEMP_GIVEN_UP)
        self.run_cli("status")
        self.assertIn("1 of yours · the first import gave up on 2 objects; tpot elastic import retries them",
                      self.out.getvalue())
        # a marker of another shipped file counts as none, and a status leaves it where it is
        ko._write_state(self.site.data, os.path.basename(ko.given_up_path(self.site.data)),
                        {"version": 1, "refused": ["dashboard:b"], "shipped": "other"}, ko.TEMP_GIVEN_UP)
        self.run_cli("status")
        self.assertNotIn("gave up", self.out.getvalue())
        self.assertTrue(os.path.exists(ko.given_up_path(self.site.data)))

    def test_status_of_an_empty_mode_is_keep(self):
        self.run_cli("status", values={"TPOT_TYPE": "HIVE", "TPOT_ES_RETENTION": "30"})
        self.assertIn("TPOT_KIBANA_OBJECTS=keep · 1 new", self.out.getvalue())

    def test_status_says_why_kibana_cannot_be_read(self):
        self.kibana = FakeKibana(down=True)
        self.assertEqual(self.run_cli("status"), 0)
        self.assertIn("TPOT_KIBANA_OBJECTS=update · Kibana at http://127.0.0.1:64296 is not reachable",
                      self.out.getvalue())

    def test_status_without_kibana_in_this_edition(self):
        self.compose = ["elasticsearch"]
        self.run_cli("status")
        self.assertIn("TPOT_KIBANA_OBJECTS=update · no Kibana in this edition", self.out.getvalue())
        self.assertEqual(self.kibana.requests, [])

    def test_status_of_a_pending_first_import(self):
        ko.mark_pending(self.site.data, "fresh")
        self.run_cli("status")
        self.assertIn("TPOT_KIBANA_OBJECTS=update · the first import has not finished yet, tpotinit retries on "
                      "every start", self.out.getvalue())
        self.assertEqual(self.kibana.requests, [])

    def test_retention_without_a_value_shows_no_kibana_line(self):
        self.run_cli("retention")
        self.assertNotIn("Kibana objects", self.out.getvalue())
        self.assertEqual(self.kibana.requests, [])


if __name__ == "__main__":
    unittest.main()
