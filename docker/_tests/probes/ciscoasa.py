"""Inert protocol probes; no exploit payload is constructed or executed."""
import hashlib
from http.client import HTTPSConnection, HTTPException
import json
from pathlib import Path
import socket
import ssl
import struct
import sys
import time

host, https, ike, natt, directory, timeout = sys.argv[1:]
https, ike, natt, timeout = map(int, (https, ike, natt, timeout))
root = Path(directory)
context = ssl._create_unverified_context()
deadline = time.monotonic() + timeout

def http(method, path, body=None, headers=None):
    conn = HTTPSConnection(host, https, context=context, timeout=3)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode("utf-8", "replace")
    finally:
        conn.close()

while True:
    try:
        assert http("GET", "/")[0] == 200
        break
    except (OSError, HTTPException):
        if time.monotonic() >= deadline:
            raise
        time.sleep(.2)

profile = json.loads((root / "identity/identity.json").read_text())
status, headers, body = http("GET", "/+CSCOE+/logon.html")
assert status == 200 and "SSL VPN Service" in body and "/+CSCOU+/portal.css" in body
assert headers["X-Transcend-Version"] == "1" and "Server" not in headers
assert http("HEAD", "/", headers={"X-Aggregate-Auth": "1"})[0] == 200
assert "config-auth" in http("GET", "/", headers={"X-Aggregate-Auth": "1"})[2]
status, _, body = http("POST", "/", '<config-auth client="vpn" type="init"><version>smoke</version></config-auth>', {"Content-Type": "application/xml"})
assert status == 200 and profile["version"] in body and 'type="auth-request"' in body
status, _, body = http("POST", "/", '<config-auth type="auth-reply"><auth><username>smoke</username><password>inert</password></auth></config-auth>', {"Content-Type": "application/xml"})
assert 'id="15"' in body and "Login failed." in body
assert http("POST", "/+webvpn+/index.html", "username=smoke&password=inert&group_list=Employees", {"Content-Type": "application/x-www-form-urlencoded"})[0] == 302
assert http("GET", "/admin/", headers={"Authorization": "Basic c21va2U6aW5lcnQ="})[0] == 401
# A patched legacy traversal is tagged but returns no file data.
assert http("GET", "/+CSCOT+/translation-table?textdomain=../inert")[0] == 404
# Public recognition path, inert body, followed by a normal multipart upload.
path = "/+CSCOU+//../+CSCOE+/files/file_action.html"
status, headers, body = http("POST", path, "placeholder", {"Content-Type": "application/octet-stream"})
if status == 200:
    assert "CSRF token mismatch" in body
    cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
    payload = b"Ciscoasa smoke test: inert uploaded evidence."
    boundary = "ciscoasa-smoke-boundary"
    multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="../../inert.txt"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + payload + f'\r\n--{boundary}--\r\n'.encode())
    assert http("POST", "/+CSCOE+/files/file_action.html", multipart, {"Content-Type": f"multipart/form-data; boundary={boundary}", "Cookie": cookie})[0] == 200
    assert (root / "payloads" / hashlib.sha256(payload).hexdigest()).read_bytes() == payload
else:
    assert status == 403  # identities beyond the chain fix deny the simulated bypass

# IKEv1 SA: 3DES/SHA1/PSK/group2. Only negotiation is sent.
sa = bytes.fromhex("00000001000000010000002001010001000000180101000080010005800200028003000180040002")
spi = socket.inet_aton("192.0.2.1") + b"test"
packet = spi + b"\0" * 8 + bytes([1, 0x10, 2, 0]) + struct.pack("!II", 0, 28 + 4 + len(sa)) + struct.pack("!BBH", 0, 0, 4 + len(sa)) + sa
for port, marker in ((ike, b""), (natt, b"\0" * 4)):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.sendto(marker + packet, (host, port))
        response, _ = sock.recvfrom(65535)
    assert response.startswith(marker + spi)
    assert bytes.fromhex("12f5f28c457168a9702d9fe274cc0100") in response

required = {"webvpn.login", "anyconnect.init", "anyconnect.auth", "asdm.login", "ike.proposal"}
while True:
    events = [json.loads(line) for line in (root / "log/ciscoasa.json").read_text().splitlines()]
    if required <= {event["event_type"] for event in events}:
        break
    if time.monotonic() >= deadline:
        raise AssertionError("Missing JSON events")
    time.sleep(.1)
assert any(e.get("username") == "smoke" and e.get("password") == "inert" for e in events)
assert any("CVE-2020-3452" in e["cve"] for e in events)
assert any("CVE-2025-20362" in e["cve"] for e in events)
assert all(e["timestamp"].endswith("Z") and "asa" in e for e in events)
print("Verified WebVPN, AnyConnect, ASDM, inert chain, JSON credentials and both IKE ports")
