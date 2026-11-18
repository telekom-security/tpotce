"""The log table of tpotctl.logs_config: the one source of the file inputs of Logstash."""

import contextlib
import fnmatch
import io
import re
import unittest

from tpotctl import logs_config
from tpotctl.checks import inventory

# Inputs no honeypot of the checks reads: NGINX is the web server of T-Pot itself
NOT_IN_INVENTORY = {"NGINX": "nginx/log/access.log"}


def relative(path):
    return path[len("/data/"):] if path.startswith("/data/") else path


class LogsConfigTest(unittest.TestCase):

    def test_inputs_are_today(self):
        self.assertEqual(len(logs_config.INPUTS), 32)
        types = [entry.type for entry in logs_config.INPUTS]
        self.assertEqual(len(set(types)), len(types))
        decode = {entry.type: entry.decode for entry in logs_config.INPUTS}
        self.assertEqual(decode.pop("Endlessh"), "plain")
        self.assertEqual(decode.pop("Heralding"), "csv")
        self.assertEqual(set(decode.values()), {"json"})

    def test_check_is_clean(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(logs_config.main(["--check"]), 0, stderr.getvalue())

    def test_inventory_logs_covered(self):
        globs = [(relative(path), entry.type) for entry in logs_config.INPUTS for path in entry.paths]
        used = set()
        for name, (kind, path) in sorted(inventory.LOGS.items()):
            with self.subTest(honeypot=name):
                matches = [(pattern, pattern_type) for pattern, pattern_type in globs
                           if fnmatch.fnmatch(path, pattern)]
                self.assertEqual(len(matches), 1, f"{path}: {matches}")
                self.assertEqual(matches[0][1], kind)
                used.add(matches[0][0])
        unused = {pattern_type: pattern for pattern, pattern_type in globs if pattern not in used}
        self.assertEqual(unused, NOT_IN_INVENTORY)

    def test_globs_are_plain(self):
        # Fluent Bit takes glob(3) without braces
        for entry in logs_config.INPUTS:
            for path in entry.paths:
                with self.subTest(path=path):
                    self.assertTrue(path.startswith("/data/"))
                    for token in ("**", "{", "[", '"'):
                        self.assertNotIn(token, path)

    def test_decode_lists_json_types(self):
        text = (logs_config.PIPELINE_DIR / "30-decode.conf").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(logs_config.HEADER))
        found = re.search(r"^  if \[type\] in \[(.*?)\] \{$", text, re.M | re.S)
        self.assertIsNotNone(found, "no list of the JSON types")
        listed = re.findall(r'"([^"]+)"', found[1])
        self.assertEqual(listed, [entry.type for entry in logs_config.INPUTS if entry.decode == "json"])

    def test_inputs_read_every_byte(self):
        text = (logs_config.PIPELINE_DIR / "10-input-files.conf").read_text(encoding="utf-8")
        blocks = re.findall(r"^  file \{\n(.*?)^  \}", text, re.M | re.S)
        self.assertEqual(len(blocks), len(logs_config.INPUTS))
        for entry, block in zip(logs_config.INPUTS, blocks):
            with self.subTest(type=entry.type):
                self.assertIn(f'type => "{entry.type}"', block)
                # the plain codec keeps the source file in [path] until 30-decode.conf moves it; ISO-8859-1
                # keeps every byte (UTF-8 would turn a line with an invalid byte into escaped text),
                # 12-files.conf makes UTF-8 of it again
                codecs = re.findall(r"^    codec => (.*)$", block, re.M)
                self.assertEqual(codecs, ['plain { charset => "ISO-8859-1" }'])


if __name__ == "__main__":
    unittest.main()
