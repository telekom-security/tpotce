"""The SSH wire protocol, executing its real bootstrap against a synthetic sensor."""

import json
import os
import shlex
import subprocess

from tpotctl import fleet_transport, sensors
from tpotctl.tests.test_fleet_remote import SensorHome


class TransportTest(SensorHome):

    def setUp(self):
        super().setUp()
        self.sensor = sensors.Sensor(self.request["name"], host="sensor.example.org",
                                     ssh_user="genuser", ssh_port=12345)
        self.sent = []

    def local_ssh(self, args, **kwargs):
        self.sent.append((args, kwargs))
        self.assertEqual(args[:3], ["ssh", "-p", "12345"])
        self.assertIn("BatchMode=yes", args)
        self.assertIn("genuser@sensor.example.org", args)
        env = dict(os.environ, HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}",
                   PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(shlex.split(args[-1]), env=env, cwd=self.home, **kwargs)

    def test_real_bootstrap_delivers_prepare_and_removes_temporary_sources(self):
        result = fleet_transport.call(self.sensor, self.request, run=self.local_ssh)
        self.assertEqual((result["name"], result["phase"]), (self.sensor.name, "prepared"))
        self.assertEqual(json.loads((self.state / "state.json").read_text()), result)
        self.assertEqual(list(self.home.glob(".tpot-fleet-control-*")), [])
        self.assertFalse((self.home / "old-ran").exists())

    def test_password_is_sent_only_on_stdin(self):
        (self.home / "need-password").touch()
        result = fleet_transport.call(self.sensor, self.request, password="  private sudo  ", run=self.local_ssh)
        self.assertEqual(result["phase"], "prepared")
        args, options = self.sent[0]
        self.assertNotIn("private sudo", repr(args))
        self.assertNotIn("private sudo", repr({key: value for key, value in options.items() if key != "input"}))
        self.assertIn("  private sudo  ", options["input"])
        self.assertNotIn("password", result)
        self.assertFalse((self.state / "become").exists())

    def test_remote_refusal_is_an_explicit_failure(self):
        self.set_identity(kind="HIVE")
        with self.assertRaisesRegex(fleet_transport.RemoteError, "identity"):
            fleet_transport.call(self.sensor, self.request, run=self.local_ssh)
        self.assertFalse(self.state.exists())

    def test_broken_ssh_and_invalid_reply_never_look_like_success(self):
        cases = ((255, "", "Permission denied"), (0, "", ""), (0, "[]", ""),
                 (0, '{"ok": true, "result": []}', ""),
                 (1, '{"ok": true, "result": {}}', ""),
                 (0, '{"ok": "yes", "result": {}}', ""))
        for code, output, error in cases:
            with self.subTest(code=code, output=output):
                def failed(args, **kwargs):
                    return subprocess.CompletedProcess(args, code, output, error)
                with self.assertRaises(fleet_transport.TransportError):
                    fleet_transport.call(self.sensor, self.request, run=failed)

    def test_timeout_warns_that_remote_work_may_still_run(self):
        def timeout(args, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        with self.assertRaisesRegex(fleet_transport.TransportError, "may still be running"):
            fleet_transport.call(self.sensor, self.request, run=timeout)

    def test_a_password_cannot_be_hidden_in_the_persistable_request(self):
        with self.assertRaisesRegex(fleet_transport.TransportError, "password"):
            fleet_transport.call(self.sensor, dict(self.request, password="secret"), run=self.local_ssh)
        self.assertEqual(self.sent, [])
