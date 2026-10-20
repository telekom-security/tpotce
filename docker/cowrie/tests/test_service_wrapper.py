"""Final wave: `service NAME verb` of a persona with systemd is its systemctl, as the service wrapper of the release
runs it (refs final-wave: init-system-helpers of debian:12 / ubuntu:24.04, initscripts-service of almalinux:9 with
systemd); a device keeps the init scripts of its firmware."""
import json
import unittest
from pathlib import Path

from tests.test_packages import SessionCase

REFS = json.loads((Path(__file__).parent / "fixtures" / "final_wave.json").read_text(encoding="utf-8"))["entries"]
PERSONAS = (("debian-bookworm", "debian-12"), ("ubuntu-noble", "ubuntu-24-04"), ("rhel-9", "almalinux-9"))


def refs(tag, prefix):
    return [e for e in REFS if e["id"].startswith((f"{tag}-ffw-root-", f"{tag}-ffw-tester-"))
            and e["cmd"].startswith(prefix)]


def own_text(text):
    """The text of a user's job: the persona answers as a system with polkit ("Interactive authentication
    required."); the containers of the refs have none ("Access denied"), the exit status is the same."""
    return text.replace(": Access denied\n", ": Interactive authentication required.\n")


class ServiceTest(SessionCase):
    def test_root_and_a_user_get_the_texts_and_status_of_the_wrapper(self):
        for pid, tag in PERSONAS:
            root = self.session(pid, uid=0)
            user = self.session(pid, uid=1000)
            cases = refs(tag, "service ")
            self.assertGreaterEqual(len(cases), 16, tag)
            for case in cases:
                with self.subTest(persona=pid, user=case["user"], cmd=case["cmd"]):
                    run = root if case["user"] == "root" else user
                    self.assertEqual(run(case["cmd"]), (case["stdout"], own_text(case["stderr"]), case["rc"]))

    def test_systemctl_of_an_unknown_unit_and_of_a_user(self):
        for pid, tag in PERSONAS:
            root = self.session(pid, uid=0)
            user = self.session(pid, uid=1000)
            for case in refs(tag, "systemctl "):
                if case["cmd"].startswith(("systemctl enable", "systemctl is-active")):
                    continue
                with self.subTest(persona=pid, user=case["user"], cmd=case["cmd"]):
                    run = root if case["user"] == "root" else user
                    self.assertEqual(run(case["cmd"]), (case["stdout"], own_text(case["stderr"]), case["rc"]))

    def test_the_status_of_a_stopped_daemon(self):
        """How SIGTERM ended the daemons of the persona (refs final-wave *-ffw-stop-status-*): the Main PID line."""
        import re
        checked = 0
        for pid, tag in PERSONAS:
            for ref in [e for e in REFS if e["id"].startswith(f"{tag}-ffw-stop-status-")]:
                unit = ref["id"].rsplit("-ffw-stop-status-", 1)[1]
                recorded = re.search(r"   Main PID: \d+ (\(code=[^)]*\))", ref["stdout"])
                run = self.session(pid, uid=0)
                if recorded is None or run(f"systemctl is-active {unit}")[0] != "active\n":
                    continue    # (a unit the persona has not, rsyslog of a socket started again)
                with self.subTest(persona=pid, unit=unit):
                    self.assertEqual(run(f"systemctl stop {unit}")[2], 0)
                    status = run(f"systemctl status {unit} --no-pager")[0]
                    self.assertRegex(status, r"   Main PID: \d+ " + re.escape(recorded.group(1)) + "\n")
                    checked += 1
        self.assertGreaterEqual(checked, 5)

    def test_a_service_runs_and_stops(self):
        run = self.session("debian-bookworm", uid=0)
        self.assertEqual(run("service ssh stop"), ("", "", 0))
        self.assertEqual(run("systemctl is-active ssh")[0], "inactive\n")
        self.assertEqual(run("service ssh start"), ("", "", 0))
        self.assertEqual(run("systemctl is-active ssh")[0], "active\n")


if __name__ == "__main__":
    unittest.main()
