"""Deliver the stdlib fleet controller over SSH stdin, including to pre-Manager sensors."""

import json
from pathlib import Path
import shlex
import subprocess
from typing import Callable

from .fleet_remote import RemoteError
from .sensors import Sensor, ssh_command


class TransportError(Exception):
    """SSH or its reply failed; an already-started remote job may still be running."""


BOOTSTRAP = r"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

try:
    raw = sys.stdin.read(1048577)
    if len(raw) > 1048576:
        raise ValueError("request too large")
    payload = json.loads(raw)
    files = payload["files"]
    if (set(files) != {"fleet_remote.py", "envfile.py"}
            or not all(isinstance(value, str) for value in files.values())
            or not isinstance(payload["request"], dict)):
        raise ValueError("invalid controller payload")
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix=".tpot-fleet-control-", dir=Path.home()) as folder:
        for name, source in files.items():
            with open(Path(folder, name), "x", encoding="utf-8") as stream:
                stream.write(source)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        child = subprocess.run([sys.executable, str(Path(folder, "fleet_remote.py"))],
                               input=json.dumps(payload["request"]), text=True, env=env)
    sys.exit(child.returncode)
except (OSError, ValueError, TypeError, KeyError) as err:
    print(json.dumps({"ok": False, "error": "Cannot stage the fleet controller (" + type(err).__name__ + ")."}))
    sys.exit(1)
"""


def call(sensor: Sensor, request: dict, *, password: str = "", run: Callable = subprocess.run,
         timeout: int = 600) -> dict:
    if "password" in request:
        raise TransportError("Pass the password separately, not in a persistable fleet request.")
    data = dict(request)
    if password:
        data["password"] = password
    try:
        files = {name: Path(__file__).with_name(name).read_text(encoding="utf-8")
                 for name in ("fleet_remote.py", "envfile.py")}
        payload = json.dumps({"files": files, "request": data})
    except (OSError, UnicodeError, TypeError, ValueError) as err:
        raise TransportError("Cannot read or encode the fleet controller request.") from err
    if len(payload) > 1_048_576:
        raise TransportError("The fleet controller request is too large.")
    args = ssh_command(sensor.host, sensor.ssh_user, "python3 -c " + shlex.quote(BOOTSTRAP), port=sensor.ssh_port)
    try:
        result = run(args, input=payload, text=True, capture_output=True, timeout=timeout)
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as err:
        raise TransportError(f"SSH to {sensor.name} did not finish; remote work may still be running.") from err
    if result.returncode not in (0, 1):
        raise TransportError(f"SSH to {sensor.name} failed (exit {result.returncode}); remote work may still be running.")
    try:
        if len(result.stdout) > 1_048_576:
            raise ValueError("reply too large")
        reply = json.loads(result.stdout)
    except (ValueError, TypeError) as err:
        raise TransportError(f"Invalid fleet reply from {sensor.name}; remote work may still be running.") from err
    if isinstance(reply, dict):
        if result.returncode == 0 and reply.get("ok") is True and isinstance(reply.get("result"), dict):
            return reply["result"]
        if result.returncode == 1 and reply.get("ok") is False and isinstance(reply.get("error"), str) and reply["error"]:
            raise RemoteError(reply["error"])
    raise TransportError(f"Inconsistent fleet reply from {sensor.name}; remote work may still be running.")
