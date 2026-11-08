"""JSON log writers must coexist with the check's external appends."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


class DailyLog:
    def __init__(self, name, directory, defaultMode):
        self.path = Path(directory) / name
        self._openFile()

    def _openFile(self):
        self._file = open(self.path, "r+b" if self.path.exists() else "w+b", buffering=0)
        self._file.seek(0, os.SEEK_END)

    def write(self, text):
        self._file.write(text.encode())

    def flush(self):
        self._file.flush()

    def rotate(self):
        self._file.close()
        self.path.rename(self.path.with_suffix(".old"))
        self._openFile()


class JsonLogOverlayTest(unittest.TestCase):
    def load(self, service, path):
        config = types.SimpleNamespace(
            get=lambda section, key: str(path),
            getboolean=lambda section, key, fallback=False: fallback)
        modules = {
            "core": types.ModuleType("core"),
            "core.output": types.ModuleType("core.output"),
            "core.config": types.ModuleType("core.config"),
            "core.tools": types.ModuleType("core.tools"),
            "core.logfile": types.ModuleType("core.logfile"),
        }
        modules["core"].output = modules["core.output"]
        modules["core.output"].Output = object
        modules["core.config"].CONFIG = config
        modules["core.tools"].mkdir = lambda folder: Path(folder).mkdir(parents=True, exist_ok=True)
        modules["core.logfile"].HoneypotDailyLogFile = DailyLog
        file = ROOT / "docker" / service / "dist" / "jsonlog.py"
        spec = importlib.util.spec_from_file_location("jsonlog_overlay", file)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict("sys.modules", modules):
            spec.loader.exec_module(module)
        writer = module.Output()
        writer.start()
        self.addCleanup(lambda: writer.outfile._file.close())
        return writer

    def test_external_process_append_preserves_probe_lines(self):
        for service in ("elasticpot", "ipphoney"):
            with self.subTest(service=service), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "events.json"
                path.touch()
                writer = self.load(service, path)
                writer.write({"input": "first"})
                subprocess.run([
                    sys.executable, "-c",
                    'import sys; '
                    'f = open(sys.argv[1], "ab", buffering=0); '
                    'f.write(b\'{"input":"geo"}\\n\'); f.close()',
                    str(path),
                ], check=True)
                writer.write({"input": "probe"})
                self.assertEqual([json.loads(line) for line in path.read_text().splitlines()],
                                 [{"input": "first"}, {"input": "geo"}, {"input": "probe"}])

    def test_external_append_survives_next_event_and_rotation(self):
        for service in ("elasticpot", "ipphoney"):
            with self.subTest(service=service), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "events.json"
                writer = self.load(service, path)
                writer.write({"input": "first", "unixtime": 123})
                with path.open("ab", buffering=0) as external:
                    external.write(b'{"input":"geo"}\n')
                writer.write({"input": "probe"})
                self.assertEqual([json.loads(line) for line in path.read_text().splitlines()],
                                 [{"input": "first"}, {"input": "geo"}, {"input": "probe"}])
                writer.outfile.rotate()
                writer.write({"input": "after rotation"})
                with path.open("ab", buffering=0) as external:
                    external.write(b'{"input":"geo again"}\n')
                writer.write({"input": "next probe"})
                self.assertEqual([json.loads(line) for line in path.read_text().splitlines()],
                                 [{"input": "after rotation"}, {"input": "geo again"},
                                  {"input": "next probe"}])

    def test_writer_submits_a_complete_json_record(self):
        with tempfile.TemporaryDirectory() as folder:
            writer = self.load("elasticpot", Path(folder) / "events.json")
            submitted = []
            original = writer.outfile.write

            def write(text):
                submitted.append(text)
                original(text)

            writer.outfile.write = write
            writer.write({"input": "probe\nwith newline"})
            self.assertEqual(submitted, ['{"input":"probe\\nwith newline"}\n'])
