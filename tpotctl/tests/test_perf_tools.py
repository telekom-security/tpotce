"""The load and measurement tools for Logstash and Fluent Bit (docker/_tests/perf)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tpotctl.checks import fixtures

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "docker" / "_tests" / "perf"))

import load  # noqa: E402
import measure  # noqa: E402


def completed(args, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


class LoadTest(unittest.TestCase):
    def test_load_rate(self):
        present = ("cowrie", "dionaea", "honeypots")
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            for name in present:
                (data / fixtures.log_file(name)).parent.mkdir(parents=True)
            old = data / fixtures.log_file("cowrie")
            old.write_text("an earlier line\n")
            now = [1000.0]

            def sleep(seconds):
                self.assertGreaterEqual(seconds, 0)
                now[0] += seconds

            rendered = []
            real = fixtures.render

            def spy(name, ts, src_ip, src_port, dst_port, token):
                line = real(name, ts, src_ip, src_port, dst_port, token)
                rendered.append((name, token, line))
                return line

            with mock.patch.object(fixtures, "render", spy):
                count = load.run(data, 50, 2, clock=lambda: now[0], sleep=sleep)

            self.assertEqual(count, 100)
            self.assertEqual(len(rendered), 100)
            # paced: 100 lines at 50/s take 2 s less the gap after the last one
            self.assertAlmostEqual(now[0] - 1000.0, 2.0 - 1 / 50)
            self.assertEqual(sorted({name for name, _, _ in rendered}), sorted(present))
            self.assertEqual(len({token for _, token, _ in rendered}), 100)
            for name in present:
                path = data / fixtures.log_file(name)
                lines = path.read_text().splitlines()
                if name == "cowrie":
                    self.assertEqual(lines.pop(0), "an earlier line")
                else:
                    self.assertEqual(path.stat().st_mode & 0o777, 0o660)
                self.assertEqual(lines, [line for n, _, line in rendered if n == name])
            # a honeypot without its log folder is left out, nothing is created for it
            self.assertEqual(sorted(entry.name for entry in data.iterdir()), sorted(present))


class SampleTest(unittest.TestCase):
    def test_sample_parses_stats(self):
        stats = [{"Container": "logstash", "Name": "logstash", "MemUsage": "512MiB / 2GiB", "CPUPerc": "3.5%"},
                 {"Container": "fluent-bit", "Name": "fluent-bit", "MemUsage": "40.5MiB / 2GiB", "CPUPerc": "0.25%"}]
        top = {"logstash": "PID\n101\n202\n", "fluent-bit": "PID\n303\n"}
        status = {"/proc/101/status": "Name:\tjava\nVmRSS:\t  786432 kB\n",
                  "/proc/202/status": "Name:\tbash\nVmRSS:\t    1024 kB\n",
                  "/proc/303/status": "Name:\tfluent-bit\nVmRSS:\t   30720 kB\n"}
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["docker", "stats"]:
                return completed(args, "".join(json.dumps(entry) + "\n" for entry in stats))
            if args[:2] == ["docker", "top"]:
                return completed(args, top[args[2]])
            if args[:3] == ["docker", "exec", "logstash"]:
                return completed(args, json.dumps({"jvm": {"mem": {"heap_used_in_bytes": 300 * 1024 * 1024}}}))
            self.fail(f"unexpected command {args}")

        rows = measure.sample(["logstash", "fluent-bit"], run=run, read=lambda path: status[path])

        self.assertEqual(rows, [
            {"container": "logstash", "process_rss_mb": 768.0, "container_memory_mb": 512.0, "cpu_pct": 3.5,
             "heap_used_mb": 300.0},
            {"container": "fluent-bit", "process_rss_mb": 30.0, "container_memory_mb": 40.5, "cpu_pct": 0.25,
             "heap_used_mb": None},
        ])
        self.assertEqual(sum(1 for args in calls if args[:2] == ["docker", "stats"]), 1)

    def test_sample_keeps_the_stats_of_the_others_when_a_container_is_missing(self):
        # docker stats of several names fails as a whole (rc 1, no output) when one does not exist
        def run(args, **kwargs):
            if args[:2] == ["docker", "stats"]:
                if "nosuch" in args:
                    return completed(args, "", "Error response from daemon: No such container: nosuch", 1)
                return completed(args, json.dumps({"Name": args[-1], "MemUsage": "1.5GiB / 2GiB",
                                                   "CPUPerc": "4.88%"}) + "\n")
            return completed(args, "", "Error: No such container", 1)

        rows = measure.sample(["nosuch", "fluent-bit"], run=run, read=lambda path: "")

        self.assertEqual([(row["container"], row["container_memory_mb"], row["cpu_pct"]) for row in rows],
                         [("nosuch", None, None), ("fluent-bit", 1536.0, 4.88)])


class StartupTest(unittest.TestCase):
    def test_startup_marks(self):
        started = {"tpotinit": "2026-11-17T10:00:00Z", "logstash": "2026-11-17T10:03:00.123456789Z"}
        logs = {
            "elasticsearch": (
                '2026-11-17T10:02:00.000000000Z {"log.level": "INFO", "message":"starting ..."}\n'
                '2026-11-17T10:02:30.000000000Z {"log.level": "INFO", "message":"started {es}"}\n'
                '2026-11-17T10:09:00.000000000Z {"log.level": "INFO", "message":"started again"}\n', ""),
            "logstash": ("2026-11-17T10:03:01.000000000Z Starting Logstash\n",
                         "2026-11-17T10:04:05.000000000Z [INFO ] Successfully started Logstash API endpoint"
                         " {:port=>9600}\n"),
        }
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["docker", "inspect"]:
                if args[-1] not in started:
                    return completed(args, "", "Error: No such object: " + args[-1], 1)
                return completed(args, started[args[-1]] + "\n")
            if args[:2] == ["docker", "logs"]:
                if args[-1] not in logs:
                    return completed(args, "", "Error: No such container: " + args[-1], 1)
                return completed(args, *logs[args[-1]])
            self.fail(f"unexpected command {args}")

        marks = measure.startup(run=run)

        self.assertEqual(set(marks), {"elasticsearch", "logstash_api", "logstash_pipelines", "logstash_start"})
        self.assertEqual(marks["elasticsearch"], 150.0)
        self.assertEqual(marks["logstash_api"], 245.0)
        self.assertIsNone(marks["logstash_pipelines"])
        self.assertAlmostEqual(marks["logstash_start"], 180.123456)
        self.assertTrue(all(args[args.index("--since") + 1] == started["tpotinit"]
                            for args in calls if args[:2] == ["docker", "logs"]))

        # a container that does not run here (no Elasticsearch on a SENSOR): its marks are None
        del logs["elasticsearch"]
        self.assertIsNone(measure.startup(run=run)["elasticsearch"])


if __name__ == "__main__":
    unittest.main()
