# ruff: noqa: S110, S323, S603, S607
# Isolated smoke probes deliberately use refused credentials and self-signed TLS.
"""Extended T-Pot Heralding smoke: standard clients and actual captured events."""

import asyncio
import csv
import ftplib
import http.client
import json
import smtplib
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import asyncssh
import ldap3
import paho.mqtt.client as mqtt
import psycopg
import pymysql
import pytds
import redis
from smbprotocol.connection import Connection
from smbprotocol.exceptions import LogonFailure
from smbprotocol.session import Session
from vncdotool import api

host, token = sys.argv[1:3]
logs = Path("/logs")
password = "SmokePass123"
context = ssl._create_unverified_context()
expected = []


def user(protocol):
    name = f"{protocol}-{token}"
    expected.append((protocol, name))
    return name


def denied(call, error):
    try:
        call()
    except error:
        return
    raise AssertionError("honeypot accepted authentication")


async def ssh_login():
    name = user("ssh")
    try:
        async with asyncssh.connect(host, username=name, password=password, known_hosts=None):
            raise AssertionError("SSH accepted authentication")
    except asyncssh.PermissionDenied:
        pass


asyncio.run(ssh_login())
name = user("postgresql")
denied(
    lambda: psycopg.connect(
        host=host, user=name, password=password, dbname="postgres", connect_timeout=5
    ),
    psycopg.OperationalError,
)
name = user("mysql")
denied(
    lambda: pymysql.connect(host=host, user=name, password=password, connect_timeout=5),
    pymysql.err.OperationalError,
)
name = user("mssql")
denied(
    lambda: pytds.connect(
        dsn=host, port=1433, user=name, password=password, login_timeout=5, timeout=5
    ),
    pytds.Error,
)
name = user("redis")
client = redis.Redis(host=host, username=name, password=password, socket_timeout=5)
try:
    denied(client.ping, redis.AuthenticationError)
finally:
    client.close()

for protocol, port in [("ldap", 389), ("ldaps", 636)]:
    name = user(protocol)
    tls = ldap3.Tls(validate=ssl.CERT_NONE)
    connection = ldap3.Connection(
        ldap3.Server(host, port=port, use_ssl=protocol == "ldaps", tls=tls, get_info=ldap3.NONE),
        user=name,
        password=password,
    )
    assert connection.bind() is False
    assert connection.result["result"] == 49
    connection.unbind()


class ImplicitFTP(ftplib.FTP_TLS):
    def connect(self, host, port=990, timeout=5):
        self.sock = self.context.wrap_socket(
            socket.create_connection((host, port), timeout), server_hostname=host
        )
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


name = user("ftps")
client = ImplicitFTP(context=context)
try:
    client.connect(host)
    denied(lambda: client.login(name, password), ftplib.error_perm)
finally:
    client.close()

name = user("submission")
with smtplib.SMTP(host, 587, timeout=5) as client:
    client.ehlo()
    client.starttls(context=context)
    client.ehlo()
    denied(lambda: client.login(name, password), smtplib.SMTPAuthenticationError)

for protocol, port in [("mqtt", 1883), ("mqtts", 8883)]:
    name = user(protocol)
    finished = threading.Event()
    codes = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
    client.username_pw_set(name, password)

    def connected(client, userdata, flags, reason, properties, codes=codes, finished=finished):
        codes.append(reason.is_failure)
        finished.set()

    client.on_connect = connected
    if protocol == "mqtts":
        client.tls_set_context(context)
    client.connect(host, port)
    client.loop_start()
    try:
        assert finished.wait(5) and codes == [True]
    finally:
        client.disconnect()
        client.loop_stop()

name = user("http_proxy")
client = http.client.HTTPConnection(host, 8080, timeout=5)
try:
    import base64

    auth = base64.b64encode(f"{name}:{password}".encode()).decode()
    client.request(
        "GET", "http://example.invalid/", headers={"Proxy-Authorization": f"Basic {auth}"}
    )
    assert client.getresponse().status == 407
finally:
    client.close()

name = user("smb")
connection = Connection(uuid.uuid4(), host, require_signing=False)
try:
    connection.connect(timeout=5)
    session = Session(
        connection, username=name, password=password, require_encryption=False, auth_protocol="ntlm"
    )
    denied(session.connect, LogonFailure)
finally:
    connection.disconnect()

# FreeRDP generates the complete RDP negotiation and TLS/CredSSP exchanges.
for security in ["tls", "nla"]:
    name = f"rdp-{security}-{token}"
    expected.append(("rdp", f"CORP\\{name}" if security == "nla" else name))
    result = subprocess.run(
        [
            "timeout",
            "10",
            "xvfb-run",
            "-a",
            "xfreerdp",
            f"/v:{host}",
            f"/u:{name}",
            f"/p:{password}",
            "/d:CORP",
            f"/sec:{security}",
            "/cert:ignore",
            "/log-level:ERROR",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "RDP unexpectedly opened a desktop"

for transport in ["tcp", "udp"]:
    name = f"sip-{transport}-{token}"
    expected.append(("sip", name))
    result = subprocess.run(
        [
            "sipsak",
            f"--transport={transport}",
            "-U",
            "-s",
            f"sip:{name}@{host}",
            f"--auth-username={name}",
            "-a",
            password,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2, result.stderr

vnc = api.connect(f"{host}::5900", password=password, timeout=5)
try:
    try:
        vnc.refreshScreen()
    except Exception:
        pass
finally:
    try:
        vnc.disconnect()
    except Exception:
        pass
    api.shutdown()
expected.append(("vnc", None))

# Credentials must be present in both CSV and ended-session JSON. Client errors alone
# never count as a successful smoke test. Require separate TLS/NLA and TCP/UDP users.
deadline = time.monotonic() + 45  # UDP sessions end after the configured 30 s idle timeout
while time.monotonic() < deadline:
    with (logs / "auth.csv").open() as f:
        auth = list(csv.DictReader(f))
    events = [
        json.loads(line)
        for line in (logs / "log_session.json").read_text().splitlines()
        if line.strip()
    ]
    missing = []
    for protocol, username in expected:

        def matches(attempt, username=username):
            return (
                attempt.get("username") == username
                if username is not None
                else bool(attempt.get("password_hash"))
            )

        rows = [row for row in auth if row["protocol"] == protocol and matches(row)]
        ended = [
            event
            for event in events
            if event["protocol"] == protocol
            and event.get("session_ended")
            and any(matches(a) for a in event["auth_attempts"])
        ]
        if not rows or not ended:
            missing.append((protocol, username))
    nla = [
        e
        for e in events
        if e.get("auxiliary_data", {}).get("rdp_security") == "nla" and e.get("session_ended")
    ]
    if not missing and nla:
        assert any(
            a.get("password_hash") and a.get("password") is None
            for e in nla
            for a in e["auth_attempts"]
        )
        print(
            "Extended auth/session checks passed: all 27 capabilities, RDP TLS/NLA and SIP TCP/UDP (including 11 basic probes)"
        )
        break
    time.sleep(0.25)
else:
    raise AssertionError(
        f"Missing credential/session captures: {missing}; NLA sessions: {len(nla)}"
    )
