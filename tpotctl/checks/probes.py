"""One probe per honeypot: a client from this host talks to its published ports.

The interactions are those of the smoke tests (docker/_tests/tests/<honeypot>.sh and
docker/_tests/probes/), in the standard library only: plain sockets, ssl, no raw sockets, no
root. A probe sends nothing beyond what the smoke tests send, and nothing that changes the state
of a honeypot (no keys stored, no files written). Where the protocol carries text, the token of
the run (`tpot-check-<run id>`) goes in: a login name, a path, a header, an SMTP EHLO / MAIL
FROM, an SSH client banner, a command; binary protocols are found in the logs by the source port
of the probe (`ProbeResult.src_ports`).

Every port of a probe has 5 s to connect and 10 s in all, a honeypot 10 s per port up to 60 s;
a port the time of its honeypot did not reach is "not reached", never closed (`ProbeResult.state`). `run_probes` has
a budget for all probes. A refused or timed-out connection is a result (`port_open=False`), never
an exception. The LLM honeypots (galah, beelzebub) are only knocked at: an answer would call the
LLM. Some ports are left alone (skipped, the reason in the detail): dionaea's FTP data port 20
(nothing listens), its mirror port 42 (the mirror service connects back to the source) and its
TFTP port 69 (it answers only the first request after a start); its HTTP port 81 is checked for
being open only (it answers no GET). A
honeypot that does not log some sources by its configuration names them (`PortProbe.ignores`):
the probe of an address among them still runs, `ProbeResult.ignored` says which entry holds it.
"""

import base64
import ipaddress
import os
import re
import socket
import ssl
import shutil
import struct
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from tpotctl.checks.inventory import EXTERNAL, EXTERNAL_HINT, Honeypot, Port

CONNECT_TIMEOUT = 5.0     # seconds to connect
PORT_TIME = 10.0          # seconds for one port of a probe
PROBE_TIME_MAX = 60.0     # seconds for a probe at most: PORT_TIME per port up to this
RUN_BUDGET = 120.0        # seconds for every probe of a run
WORKERS = 8               # probes at the same time
PASSWORD = "tpot-check"   # the password of the login attempts
TOKEN_PREFIX = "tpot-check-"
_RUN_ID = re.compile(r"^[a-z0-9]{1,32}$")
_TOKEN = re.compile(r"^tpot-check-[a-z0-9]{1,32}$")

__all__ = ["EXTERNAL", "EXTERNAL_HINT", "PROBES", "ProbeResult", "STATES", "run_probes", "token_for", "uncovered"]


def token_for(run_id: str) -> str:
    """The token of a run: `tpot-check-<run id>`, lower case letters and digits only, so it
    fits a host name (SMTP EHLO), a path and a login name as it is."""
    if not _RUN_ID.match(run_id or ""):
        raise ValueError(f"run id {run_id!r}: 1 to 32 lower case letters and digits")
    return TOKEN_PREFIX + run_id


# The state of a port, and of a honeypot (its ports together):
#   open         the connection was taken (UDP: an answer came)
#   closed       a connect was refused, or timed out on the port's own, full clock
#   not_reached  not tried, or cut short: the time of the honeypot or of the run ran out
#   skipped      left alone on purpose (P2P mode, not running, no port, ...): the detail says why
OPEN, CLOSED, NOT_REACHED, SKIPPED = "open", "closed", "not_reached", "skipped"
STATES = (OPEN, CLOSED, NOT_REACHED, SKIPPED)


@dataclass
class ProbeResult:
    state: str              # see STATES: closed when one port is closed, open when the others are open
    answered: bool          # every port whose answer is checked answered as the honeypot should
    detail: str             # per port what happened
    answer_checked: bool = True   # False: only the ports were checked (e.g. DICOM without a client)
    token_sent: bool = False      # the token went into at least one request
    src_ports: List[int] = field(default_factory=list)  # the source ports of the probe
    port_states: List[Tuple[Port, str]] = field(default_factory=list)  # every port and its state

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"state {self.state!r} is none of {', '.join(STATES)}")

    # the range of the source addresses the honeypot does not log (its configuration) that holds
    # the probe address: the probe still runs, its log line and event never come ("" for none)
    ignored: str = ""

    @property
    def port_open(self) -> bool:
        """Stage 1: derived from the state only, so "not reached" never reads as closed."""
        return self.state == OPEN

    @property
    def not_reached(self) -> List[Port]:
        return [port for port, state in self.port_states if state == NOT_REACHED]


class _NotTried(Exception):
    """The port is left alone on purpose (the text says why): skipped, never closed."""


class _NoAnswer(Exception):
    """The port took the connection, but what came back is not the honeypot's answer."""


@dataclass
class _Outcome:
    port: Port
    state: str
    answered: Optional[bool]    # None: not checked (port only)
    note: str


class _Clock:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        return self.end - time.monotonic()

    def timeout(self, most: float = CONNECT_TIMEOUT) -> float:
        # never 0: settimeout(0) would make the socket non-blocking
        return max(0.05, min(most, self.left()))


def _tls_context() -> ssl.SSLContext:
    # the honeypots present self-signed certificates of their own
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class _Ctx:
    """One port of one probe: its address, the token, the clock and what the probe saw."""

    def __init__(self, host: str, port: Port, token: str, clock: _Clock):
        self.host = port.host_ip or host
        self.port = port
        self.token = token
        self.clock = clock
        self.opened = False
        self.token_sent = False
        self.src_ports: List[int] = []

    # -- TCP ----------------------------------------------------------------
    def tcp(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port.published), timeout=self.clock.timeout())
        self.opened = True
        try:
            self.src_ports.append(sock.getsockname()[1])
        except OSError:
            pass
        return sock

    def tls(self, sock: socket.socket) -> ssl.SSLSocket:
        sock.settimeout(self.clock.timeout())
        return _tls_context().wrap_socket(sock, server_hostname=None)

    def send(self, sock: socket.socket, data: bytes, token: bool = False) -> None:
        sock.settimeout(self.clock.timeout())
        sock.sendall(data)
        if token:
            self.token_sent = True

    def recv_until(self, sock: socket.socket, done: Callable[[bytes], bool], limit: int = 65536) -> bytes:
        """What comes back until done(data), the peer closes, `limit` bytes or the clock ends."""
        data = b""
        while not done(data) and len(data) < limit and self.clock.left() > 0:
            sock.settimeout(self.clock.timeout(1.0))
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except ConnectionResetError:
                break   # what came before the reset still counts
            if not chunk:
                break
            data += chunk
        return data

    def line(self, sock: socket.socket) -> bytes:
        return self.recv_until(sock, lambda d: b"\n" in d)

    # -- UDP ----------------------------------------------------------------
    def udp(self, payload: bytes, token: bool = False, answer: bool = True, wait: float = 3.0) -> bytes:
        """One datagram, and the answer (b"" without one). Refused (ICMP) is ConnectionRefusedError;
        without `answer` a datagram that is not refused within `wait` counts as taken."""
        family, kind, proto, _, address = socket.getaddrinfo(self.host, self.port.published,
                                                             type=socket.SOCK_DGRAM)[0]
        with socket.socket(family, kind, proto) as sock:
            sock.settimeout(self.clock.timeout(wait))
            sock.connect(address)
            try:
                self.src_ports.append(sock.getsockname()[1])
            except OSError:
                pass
            sock.send(payload)
            if token:
                self.token_sent = True
            try:
                data = sock.recv(65535)
            except socket.timeout:
                if not answer:
                    self.opened = True
                return b""
            self.opened = True
            return data


Handler = Callable[[_Ctx], Tuple[Optional[bool], str]]


def _short(data: bytes, size: int = 60) -> str:
    text = data.decode("utf-8", errors="replace").strip().splitlines()
    return (text[0] if text else "")[:size]


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, socket.timeout):
        return "timed out"
    return str(exc) or exc.__class__.__name__


# ---------------------------------------------------------------------------
# handlers of the protocol families: ctx -> (answered or None, note)
# ---------------------------------------------------------------------------

def connect_only(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """The port takes the connection; what the service speaks there is not checked."""
    with ctx.tcp():
        pass
    return None, "port open (port only)"


def send_only(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """A UDP datagram with the token; the service is not expected to answer it."""
    ctx.udp(ctx.token.encode("ascii"), token=True, answer=False, wait=1.0)
    return None, "datagram taken (port only)"


def _http_request(method: str, path: str, host: str, headers: Dict[str, str], body: bytes = b"") -> bytes:
    lines = [f"{method} {path} HTTP/1.1", f"Host: {host}"]
    lines += [f"{name}: {value}" for name, value in headers.items()]
    if body or method == "POST":
        lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


def http(method: str = "GET", path: str = "/{token}", headers: Optional[Dict[str, str]] = None,
         body: str = "", tls: bool = False) -> Handler:
    """One HTTP(S) request; {token} in the path, the headers and the body is the token of the run.
    The answer is its status line (tarpits stream on, only the head is read)."""
    def handler(ctx: _Ctx) -> Tuple[Optional[bool], str]:
        fill = lambda text: text.replace("{token}", ctx.token)  # noqa: E731
        sent_headers = {"User-Agent": f"Mozilla/5.0 (T-Pot check) {ctx.token}"}
        sent_headers.update({name: fill(value) for name, value in (headers or {}).items()})
        host = f"[{ctx.host}]" if ":" in ctx.host else ctx.host
        request = _http_request(method, fill(path), host, sent_headers, fill(body).encode("utf-8"))
        first_line: List[float] = []

        def head(data: bytes) -> bool:
            # the whole head, or the status line and a second more: a tarpit sends its head slowly
            if b"\r\n\r\n" in data:
                return True
            if b"\n" in data:
                if not first_line:
                    first_line.append(time.monotonic())
                return not data.startswith(b"HTTP/") or time.monotonic() - first_line[0] >= 1.0
            return False

        with ctx.tcp() as raw:
            sock = ctx.tls(raw) if tls else raw
            try:
                ctx.send(sock, request, token=True)
                data = ctx.recv_until(sock, head)
            finally:
                if sock is not raw:
                    sock.close()
        if not data.startswith(b"HTTP/"):
            raise _NoAnswer(f"no HTTP answer ({_short(data) or 'nothing'})")
        return True, _short(data)
    return handler


def ssh_banner(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """The banner of the server, then the client's own with the token as its comment (RFC 4253)."""
    with ctx.tcp() as sock:
        banner = ctx.line(sock)
        if not banner.startswith(b"SSH-"):
            raise _NoAnswer(f"no SSH banner ({_short(banner) or 'nothing'})")
        ctx.send(sock, f"SSH-2.0-TPotCheck {ctx.token}\r\n".encode("ascii"), token=True)
        time.sleep(min(0.2, max(0.0, ctx.clock.left())))
    return True, _short(banner)


def tarpit(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """Endlessh: the client banner with the token; the tarpit holds the connection and sends its
    first line only after its delay (6 - 14 s), so a connection still open after 2 s answers."""
    with ctx.tcp() as sock:
        ctx.send(sock, f"SSH-2.0-TPotCheck {ctx.token}\r\n".encode("ascii"), token=True)
        held_until = time.monotonic() + min(2.0, max(0.1, ctx.clock.left()))
        while time.monotonic() < held_until:
            sock.settimeout(max(0.05, held_until - time.monotonic()))
            try:
                chunk = sock.recv(256)
            except socket.timeout:
                break
            if not chunk:
                raise _NoAnswer("closed the connection")
            return True, f"sent {_short(chunk, 30)!r}"
    return True, "holds the connection (tarpit)"


_IAC, _DONT, _DO, _WONT, _WILL, _SB, _SE = 255, 254, 253, 252, 251, 250, 240


def _telnet_until(ctx: _Ctx, sock: socket.socket, needles: Tuple[str, ...]) -> str:
    """Text until one of the needles; telnet options are refused as the smoke tests do."""
    text, sub = bytearray(), False
    while ctx.clock.left() > 0:
        sock.settimeout(ctx.clock.timeout(1.0))
        try:
            chunk = sock.recv(256)
        except socket.timeout:
            continue
        if not chunk:
            break
        index = 0
        while index < len(chunk):
            byte = chunk[index]
            if byte == _IAC and index + 1 < len(chunk):
                command = chunk[index + 1]
                if command in (_DO, _DONT, _WILL, _WONT) and index + 2 < len(chunk):
                    reply = _WONT if command in (_DO, _DONT) else _DONT
                    sock.sendall(bytes([_IAC, reply, chunk[index + 2]]))
                    index += 3
                    continue
                sub = True if command == _SB else False if command == _SE else sub
                index += 2
                continue
            if not sub:
                text.append(byte)
            index += 1
        lower = text.decode("utf-8", errors="ignore").lower()
        if any(needle in lower for needle in needles):
            return lower
    raise _NoAnswer(f"no {' / '.join(needles)} prompt")


def telnet_login(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """The login prompt, the token as the login name, a password."""
    with ctx.tcp() as sock:
        _telnet_until(ctx, sock, ("login:", "username:"))
        ctx.send(sock, f"{ctx.token}\r\n".encode("ascii"), token=True)
        try:
            _telnet_until(ctx, sock, ("password:",))
            ctx.send(sock, f"{PASSWORD}\r\n".encode("ascii"))
            time.sleep(min(0.3, max(0.0, ctx.clock.left())))
        except (_NoAnswer, OSError):
            pass
    return True, "login prompt"


def _reply(ctx: _Ctx, sock: socket.socket, command: bytes, token: bool = False) -> bytes:
    ctx.send(sock, command, token=token)
    return ctx.line(sock)


def ftp_login(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """The 220 banner, USER with the token, PASS."""
    with ctx.tcp() as sock:
        banner = ctx.line(sock)
        if not banner.startswith(b"220"):
            raise _NoAnswer(f"no FTP banner ({_short(banner) or 'nothing'})")
        answer = _reply(ctx, sock, f"USER {ctx.token}\r\n".encode("ascii"), token=True)
        if answer.startswith(b"331"):
            _reply(ctx, sock, f"PASS {PASSWORD}\r\n".encode("ascii"))
        try:
            ctx.send(sock, b"QUIT\r\n")
        except OSError:
            pass
    return True, _short(banner)


def pop3_login(tls: bool = False) -> Handler:
    def handler(ctx: _Ctx) -> Tuple[Optional[bool], str]:
        with ctx.tcp() as raw:
            sock = ctx.tls(raw) if tls else raw
            try:
                banner = ctx.line(sock)
                if not banner.startswith(b"+OK"):
                    raise _NoAnswer(f"no POP3 banner ({_short(banner) or 'nothing'})")
                if _reply(ctx, sock, f"USER {ctx.token}\r\n".encode("ascii"), token=True).startswith(b"+OK"):
                    _reply(ctx, sock, f"PASS {PASSWORD}\r\n".encode("ascii"))
                ctx.send(sock, b"QUIT\r\n")
            finally:
                if sock is not raw:
                    sock.close()
        return True, _short(banner)
    return handler


def imap_login(tls: bool = False) -> Handler:
    def handler(ctx: _Ctx) -> Tuple[Optional[bool], str]:
        with ctx.tcp() as raw:
            sock = ctx.tls(raw) if tls else raw
            try:
                banner = ctx.line(sock)
                if not banner.startswith(b"* OK"):
                    raise _NoAnswer(f"no IMAP banner ({_short(banner) or 'nothing'})")
                ctx.send(sock, f'a001 LOGIN "{ctx.token}" "{PASSWORD}"\r\n'.encode("ascii"), token=True)
                ctx.recv_until(sock, lambda d: b"a001 " in d.lower())
                ctx.send(sock, b"a002 LOGOUT\r\n")
            finally:
                if sock is not raw:
                    sock.close()
        return True, _short(banner)
    return handler


def _smtp_response(ctx: _Ctx, sock: socket.socket) -> bytes:
    """A whole SMTP reply (continuation lines "250-...")."""
    def complete(data: bytes) -> bool:
        lines = data.split(b"\r\n")
        return len(lines) > 1 and any(len(line) >= 4 and line[3:4] == b" " for line in lines[:-1])
    return ctx.recv_until(sock, complete)


def smtp(auth: bool = False, mail: bool = False, tls: bool = False) -> Handler:
    """The 220 banner and EHLO with the token as the host name; then AUTH PLAIN with the token
    (Heralding, honeypots) or MAIL FROM / RCPT TO with the token (Mailoney), and QUIT."""
    def handler(ctx: _Ctx) -> Tuple[Optional[bool], str]:
        with ctx.tcp() as raw:
            sock = ctx.tls(raw) if tls else raw
            try:
                banner = _smtp_response(ctx, sock)
                if not banner.startswith(b"220"):
                    raise _NoAnswer(f"no SMTP banner ({_short(banner) or 'nothing'})")
                ctx.send(sock, f"EHLO {ctx.token}.example\r\n".encode("ascii"), token=True)
                ehlo = _smtp_response(ctx, sock)
                if not ehlo[:1].isdigit():
                    raise _NoAnswer("no answer to EHLO")
                if auth:
                    plain = base64.b64encode(f"\0{ctx.token}\0{PASSWORD}".encode("ascii")).decode("ascii")
                    ctx.send(sock, f"AUTH PLAIN {plain}\r\n".encode("ascii"))
                    _smtp_response(ctx, sock)
                if mail:
                    ctx.send(sock, f"MAIL FROM:<{ctx.token}@example.org>\r\n".encode("ascii"))
                    _smtp_response(ctx, sock)
                    ctx.send(sock, f"RCPT TO:<{ctx.token}@example.net>\r\n".encode("ascii"))
                    _smtp_response(ctx, sock)
                ctx.send(sock, b"QUIT\r\n")
                _smtp_response(ctx, sock)
            finally:
                if sock is not raw:
                    sock.close()
        return True, _short(banner)
    return handler


def socks5_login(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """The method user / password, then the token as the user name."""
    with ctx.tcp() as sock:
        ctx.send(sock, b"\x05\x01\x02")
        method = ctx.recv_until(sock, lambda d: len(d) >= 2)
        if not method.startswith(b"\x05"):
            raise _NoAnswer(f"no SOCKS5 answer ({method[:8].hex() or 'nothing'})")
        if method[:2] == b"\x05\x02":
            user, password = ctx.token.encode("ascii"), PASSWORD.encode("ascii")
            ctx.send(sock, b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password, token=True)
            ctx.recv_until(sock, lambda d: len(d) >= 2)
    return True, f"method {method[:2].hex()}"


def tcp_exchange(payload: bytes, expect: Callable[[bytes], bool], what: str,
                 enough: Optional[Callable[[bytes], bool]] = None) -> Handler:
    """A request without text of its own (binary protocols of Conpot, RDP, ...) and its answer."""
    def handler(ctx: _Ctx) -> Tuple[Optional[bool], str]:
        with ctx.tcp() as sock:
            if payload:
                ctx.send(sock, payload)
            data = ctx.recv_until(sock, enough or expect)
        if not expect(data):
            raise _NoAnswer(f"no {what} answer ({data[:16].hex() or 'nothing'})")
        return True, f"{what} answer"
    return handler


def udp_exchange(payload: bytes, expect: Callable[[bytes], bool], what: str, token: bool = False) -> Handler:
    def handler(ctx: _Ctx) -> Tuple[Optional[bool], str]:
        data = ctx.udp(payload.replace(b"{token}", ctx.token.encode("ascii")), token=token)
        if not data:
            return False, f"no {what} answer"
        if not expect(data):
            raise _NoAnswer(f"no {what} answer ({data[:16].hex()})")
        return True, f"{what} answer"
    return handler


def port_only(why: str) -> Handler:
    """The port, with the reason the answer is not checked."""
    def handler(ctx: _Ctx) -> Tuple[Optional[bool], str]:
        with ctx.tcp():
            pass
        return None, f"port open, {why}"
    handler.leave_out = why     # hptest.sh --nmap sends nothing to it either (hptest_table)
    return handler


LLM_REASON = "an answer would call the LLM"
# open the TCP connection and close it, nothing sent: no request reaches the LLM (nor Galah's
# lookup of the source address); the ports of galah and beelzebub are all TCP
knock = port_only(LLM_REASON)
P2P_REASON = "P2P mode would share the probe's source with its peers"


def _dicom_echo(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """A DICOM C-ECHO with echoscu of DCMTK where it is installed, otherwise the port only."""
    echoscu = shutil.which("echoscu")
    if not echoscu:
        return port_only("DICOM not checked (DCMTK echoscu is not installed)")(ctx)
    with ctx.tcp():
        pass
    wait = str(max(1, int(ctx.clock.timeout(PORT_TIME))))
    argv = [echoscu, "-to", wait, "-ta", wait, "-td", wait, ctx.host, str(ctx.port.published)]
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=max(0.5, ctx.clock.left()))
    except subprocess.TimeoutExpired:
        raise _NoAnswer("no DICOM C-ECHO answer (echoscu timed out)")
    if done.returncode != 0:
        lines = (done.stdout or "").strip().splitlines()
        raise _NoAnswer("no DICOM C-ECHO answer" + (f" ({lines[-1][:60]})" if lines else ""))
    return True, "DICOM C-ECHO answer (echoscu)"


# ---------------------------------------------------------------------------
# the requests of the single honeypots, as their smoke tests send them
# ---------------------------------------------------------------------------

def _adb_packet(command: bytes, arg0: int, arg1: int, payload: bytes) -> bytes:
    command_id = struct.unpack("<I", command)[0]
    return struct.pack("<6I", command_id, arg0, arg1, len(payload), sum(payload) & 0xFFFFFFFF,
                       command_id ^ 0xFFFFFFFF) + payload


def adb(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """CNXN, then OPEN of `shell:echo <token>` (adbhoney.sh)."""
    with ctx.tcp() as sock:
        ctx.send(sock, _adb_packet(b"CNXN", 0x01000000, 4096, f"host::{ctx.token}\0".encode("ascii")), token=True)
        data = ctx.recv_until(sock, lambda d: len(d) >= 24 and len(d) >= 24 + struct.unpack("<I", d[12:16])[0])
        if len(data) < 24 or data[:4] not in (b"CNXN", b"AUTH"):
            raise _NoAnswer(f"no ADB answer ({data[:8].hex() or 'nothing'})")
        if data[:4] == b"CNXN":
            ctx.send(sock, _adb_packet(b"OPEN", 1, 0, f"shell:echo {ctx.token}\0".encode("ascii")))
            ctx.recv_until(sock, lambda d: len(d) >= 24)
    return True, f"ADB {data[:4].decode('ascii')}"


# IKEv1 SA (3DES / SHA1 / PSK / group 2), negotiation only, as hptest.sh fuIKEPROBE sends it
_IKE_SA = bytes.fromhex("00000001000000010000002001010001000000180101000080010005800200028003000180040002")
_IKE_SPI = b"tpotprob"
_IKE = (_IKE_SPI + b"\0" * 8 + bytes([1, 0x10, 2, 0]) + struct.pack("!II", 0, 32 + len(_IKE_SA))
        + struct.pack("!BBH", 0, 0, 4 + len(_IKE_SA)) + _IKE_SA)


def ike(natt: bool) -> Handler:
    marker = b"\0" * 4 if natt else b""
    return udp_exchange(marker + _IKE, lambda d: d.startswith(marker + _IKE_SPI), "IKE")


def _snmp_get_sysdescr() -> bytes:
    def tlv(tag: int, value: bytes) -> bytes:
        return bytes([tag, len(value)]) + value
    oid = bytes([0x2B, 6, 1, 2, 1, 1, 1, 0])   # 1.3.6.1.2.1.1.1.0 sysDescr
    varbind = tlv(0x30, tlv(0x30, tlv(0x06, oid) + b"\x05\x00"))
    pdu = tlv(0xA0, tlv(0x02, b"\x01") + b"\x02\x01\x00\x02\x01\x00" + varbind)
    return tlv(0x30, b"\x02\x01\x01" + tlv(0x04, b"public") + pdu)


def _opcua_hello(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    url = f"opc.tcp://{ctx.host}:{ctx.port.published}".encode("ascii")
    body = struct.pack("<IIIII", 0, 65536, 65536, 0, 0) + struct.pack("<i", len(url)) + url
    return tcp_exchange(b"HELF" + struct.pack("<I", 8 + len(body)) + body,
                        lambda d: d.startswith(b"ACKF"), "OPC UA", lambda d: len(d) >= 8)(ctx)


def _kamstrup_management(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    with ctx.tcp() as sock:
        banner = ctx.recv_until(sock, lambda d: b"Welcome" in d)
    if b"Welcome" not in banner:
        raise _NoAnswer(f"no Kamstrup management banner ({_short(banner) or 'nothing'})")
    return True, "Kamstrup management banner"


def _hart_ip(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    initiate = struct.pack("!BBBBHH", 1, 0, 0, 0, 1, 13) + b"\x01\x00\x00\x75\x30"
    return tcp_exchange(initiate, lambda d: len(d) >= 8 and d[1] == 1 and d[2] == 0, "HART-IP",
                        lambda d: len(d) >= 13)(ctx)


def _dns_version_bind(tcp: bool) -> Handler:
    question = b"\x07version\x04bind\x00" + struct.pack("!HH", 16, 3)   # TXT CH
    query = struct.pack("!HHHHHH", 0x7470, 0x0100, 1, 0, 0, 0) + question
    expect = lambda d: len(d) >= 12 and d[:2] == b"\x74\x70" and bool(d[2] & 0x80)  # noqa: E731
    if tcp:
        framed = struct.pack("!H", len(query)) + query
        return tcp_exchange(framed, lambda d: expect(d[2:]), "DNS",
                            lambda d: len(d) >= 2 and len(d) >= 2 + struct.unpack("!H", d[:2])[0])
    return udp_exchange(query, expect, "DNS")


_SSDP = (b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 1\r\n"
         b"ST: upnp:rootdevice\r\nUSER-AGENT: {token}\r\n\r\n")


def _rdp(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    # TPKT + X.224 Connection Request + RDP Negotiation Request (SSL | HYBRID), rdphoneypot.sh
    request = b"\x03\x00\x00\x13\x0e\xe0\x00\x00\x00\x00\x00\x01\x00\x08\x00\x03\x00\x00\x00"
    return tcp_exchange(request, lambda d: len(d) >= 6 and d[:2] == b"\x03\x00" and d[5] == 0xD0,
                        "RDP", lambda d: len(d) >= 4 and len(d) >= struct.unpack(">H", d[2:4])[0])(ctx)


def _hl7(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    message = (f"MSH|^~\\&|TPOTCHECK|TEST|MEDPOT|TEST|202601010000||ADT^A01|{ctx.token}|P|2.6\r"
               f"EVN|A01|202601010000\rPID|||{ctx.token}||CHECK^TPOT||19700101|U\r").encode("ascii")
    with ctx.tcp() as sock:
        ctx.send(sock, message, token=True)
        sock.shutdown(socket.SHUT_WR)
        data = ctx.recv_until(sock, lambda d: b"MSA|" in d)
    if b"MSA|" not in data:
        raise _NoAnswer(f"no HL7 ACK ({_short(data) or 'nothing'})")
    return True, "HL7 ACK"


def _pjl(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    request = f"@PJL INFO ID\r\n@PJL ECHO {ctx.token}\r\n".encode("ascii")
    with ctx.tcp() as sock:
        ctx.send(sock, request, token=True)
        sock.shutdown(socket.SHUT_WR)
        data = ctx.recv_until(sock, lambda d: ctx.token.encode("ascii") in d)
    if not data.startswith(b"@PJL"):
        raise _NoAnswer(f"no PJL answer ({_short(data) or 'nothing'})")
    return True, "PJL answer"


def _resp(*parts: str) -> bytes:
    out = [f"*{len(parts)}\r\n".encode("ascii")]
    for part in parts:
        raw = part.encode("utf-8")
        out += [f"${len(raw)}\r\n".encode("ascii"), raw + b"\r\n"]
    return b"".join(out)


def _redis(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """PING and a GET of the token (a read: nothing is stored), QUIT."""
    with ctx.tcp() as sock:
        ctx.send(sock, _resp("PING"))
        pong = ctx.line(sock)
        if not pong.startswith(b"+PONG"):
            raise _NoAnswer(f"no PONG ({_short(pong) or 'nothing'})")
        ctx.send(sock, _resp("GET", ctx.token), token=True)
        ctx.line(sock)
        ctx.send(sock, _resp("QUIT"))
    return True, "PONG"


def _sip_options(udp: bool) -> Handler:
    def handler(ctx: _Ctx) -> Tuple[Optional[bool], str]:
        transport = "UDP" if udp else "TCP"
        request = (f"OPTIONS sip:1000@{ctx.host} SIP/2.0\r\n"
                   f"Via: SIP/2.0/{transport} 127.0.0.1:50999;branch=z9hG4bK-{ctx.token}\r\n"
                   "Max-Forwards: 70\r\n"
                   f"From: \"Check\" <sip:check@127.0.0.1>;tag={ctx.token}\r\n"
                   f"To: <sip:1000@{ctx.host}>\r\n"
                   f"Call-ID: {ctx.token}@127.0.0.1\r\n"
                   "CSeq: 1 OPTIONS\r\n"
                   "Contact: <sip:check@127.0.0.1:50999>\r\n"
                   f"User-Agent: {ctx.token}\r\n"
                   "Content-Length: 0\r\n\r\n").encode("ascii")
        if udp:
            data = ctx.udp(request, token=True)
            if not data:
                return False, "no SIP answer"
        else:
            with ctx.tcp() as sock:
                ctx.send(sock, request, token=True)
                data = ctx.recv_until(sock, lambda d: b"\r\n\r\n" in d)
        if not data.startswith(b"SIP/2.0"):
            raise _NoAnswer(f"no SIP answer ({_short(data) or 'nothing'})")
        return True, _short(data)
    return handler


def _mqtt(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    return tcp_exchange(b"\x10\x0c\x00\x04MQTT\x04\x02\x00\x3c\x00\x00", lambda d: d.startswith(b"\x20\x02"),
                        "MQTT", lambda d: len(d) >= 4)(ctx)


def _mysql_greeting(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    return tcp_exchange(b"", lambda d: len(d) >= 5 and d[4] == 0x0A, "MySQL", lambda d: len(d) >= 5)(ctx)


_LOGIN_FORM = {"Content-Type": "application/x-www-form-urlencoded"}


class PortProbe:
    """The probe of a honeypot: one handler per port inside the container (target, protocol).
    Every published port of the honeypot is probed with the handler of its target; a port the
    table does not name is only checked to be open (the coverage test wants every port named)."""

    def __init__(self, handlers: Dict[Tuple[int, str], Handler], note: str = "",
                 default: Optional[Handler] = None, ignores: Iterable[str] = ()):
        self.handlers = handlers
        self.note = note
        self.default = default
        # the sources the honeypot does not log by its configuration (addresses or networks)
        self.ignores = tuple(ignores)

    def ignored(self, host: str) -> str:
        """The entry of `ignores` that holds the address `host`, "" for none (or a name)."""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return ""
        for entry in self.ignores:
            network = ipaddress.ip_network(entry, strict=False)
            if address.version == network.version and address in network:
                return entry
        return ""

    def handler_for(self, port: Port) -> Handler:
        found = self.handlers.get((port.target, port.protocol))
        if found:
            return found
        if self.default is not None:
            return self.default
        return send_only if port.protocol == "udp" else connect_only

    def __call__(self, host: str, ports: List[Port], token: str) -> ProbeResult:
        if not _TOKEN.match(token or ""):
            raise ValueError(f"token {token!r} is not tpot-check-<run id>")
        # every port has PORT_TIME of its own, the honeypot PORT_TIME per port up to PROBE_TIME_MAX
        total = min(PROBE_TIME_MAX, PORT_TIME * max(1, len(ports)))
        overall = _Clock(total)
        outcomes: List[_Outcome] = []
        token_sent, src_ports = False, []
        for port in ports:
            if overall.left() <= 0:
                outcomes.append(_Outcome(port, NOT_REACHED, None, f"not reached (the {total:g} s of the probe ran out)"))
                continue
            allotted = min(PORT_TIME, overall.left())
            # a port whose clock the honeypot's cap cut below its own connect time had no full try
            cut = allotted < min(CONNECT_TIMEOUT, PORT_TIME)
            ctx = _Ctx(host, port, token, _Clock(allotted))
            try:
                answered, note = self.handler_for(port)(ctx)
                if ctx.opened:
                    outcomes.append(_Outcome(port, OPEN, answered, note))
                elif cut:   # a UDP answer that did not come within a cut-short clock
                    outcomes.append(_Outcome(port, NOT_REACHED, None, f"not reached (cut short: {note})"))
                else:       # a UDP answer that did not come within the port's own clock: timed out
                    outcomes.append(_Outcome(port, CLOSED, answered, note))
            except _NotTried as exc:
                outcomes.append(_Outcome(port, SKIPPED, None, str(exc)))
            except _NoAnswer as exc:
                outcomes.append(_Outcome(port, OPEN if ctx.opened else CLOSED, False, str(exc)))
            except (OSError, ValueError, struct.error, IndexError, UnicodeError) as exc:
                outcomes.append(_failed(port, exc, ctx.opened, cut))
            token_sent = token_sent or ctx.token_sent
            src_ports += ctx.src_ports
        result = _combine(outcomes, token_sent, src_ports, self.note)
        result.ignored = self.ignored(host)
        return result


def _failed(port: Port, exc: BaseException, opened: bool, cut: bool) -> _Outcome:
    """A port whose handler failed: open when the connection had been taken; closed only when the
    connect was refused, or timed out on the port's own full clock; not reached otherwise."""
    text = _error_text(exc)
    if opened:
        return _Outcome(port, OPEN, False, text)
    if isinstance(exc, ConnectionRefusedError) or (isinstance(exc, socket.timeout) and not cut):
        return _Outcome(port, CLOSED, False, f"not open: {text}")
    if isinstance(exc, socket.timeout):
        return _Outcome(port, NOT_REACHED, None, f"not reached (cut short: {text})")
    return _Outcome(port, NOT_REACHED, None, f"not tried: {text}")


def _combine(outcomes: List[_Outcome], token_sent: bool, src_ports: List[int], note: str = "") -> ProbeResult:
    if not outcomes:
        return ProbeResult(SKIPPED, False, "no published port", answer_checked=False)
    states = {o.state for o in outcomes}
    # one closed port makes the honeypot closed; open when the others were tried and open;
    # nothing but ports not reached / skipped is never closed
    state = next(s for s in (CLOSED, OPEN, NOT_REACHED, SKIPPED) if s in states)
    checked = [o for o in outcomes if o.state in (OPEN, CLOSED) and o.answered is not None]
    detail = "; ".join(f"{o.port}: {o.note}" for o in outcomes)
    if note:
        detail = f"{detail} ({note})"
    return ProbeResult(
        state=state,
        answered=bool(checked) and all(o.answered for o in checked),
        detail=detail,
        answer_checked=bool(checked),
        token_sent=token_sent,
        src_ports=src_ports,
        port_states=[(o.port, o.state) for o in outcomes],
    )


def _basic_auth(tls: bool) -> Handler:
    """Heralding: GET with basic auth of the token (heralding.sh http_probe)."""
    def handler(ctx: _Ctx) -> Tuple[Optional[bool], str]:
        auth = base64.b64encode(f"{ctx.token}:{PASSWORD}".encode("ascii")).decode("ascii")
        return http("GET", "/tpot-check/{token}", {"Authorization": "Basic " + auth}, tls=tls)(ctx)
    return handler


def _qhp_http(tls: bool) -> Handler:
    """honeypots: a login form with the token (honeypots.sh http_probe)."""
    return http("POST", "/login.html", _LOGIN_FORM, f"username={{token}}&password={PASSWORD}", tls=tls)


def _qhp_elastic(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """honeypots: a search with basic auth of the token over HTTPS (honeypots.sh elastic_probe)."""
    auth = base64.b64encode(f"{ctx.token}:{PASSWORD}".encode("ascii")).decode("ascii")
    return http("POST", "/test/_search", {"Authorization": "Basic " + auth, "Content-Type": "application/json"},
                '{"size":1}', tls=True)(ctx)


def _tftp(ctx: _Ctx) -> Tuple[Optional[bool], str]:
    """A TFTP read request for <token>.txt (dionaea.sh); Dionaea is not expected to answer it."""
    ctx.udp(b"\x00\x01" + f"{ctx.token}.txt".encode("ascii") + b"\x00octet\x00", token=True, answer=False, wait=1.0)
    return None, "TFTP request taken (port only)"


def not_tried(why: str) -> Handler:
    """A port left alone: nothing is sent, not even a connection (UDP has no knock)."""
    def handler(ctx: _Ctx) -> Tuple[Optional[bool], str]:
        raise _NotTried(f"not probed, {why}")
    handler.leave_out = why     # hptest.sh --nmap sends nothing to it either (hptest_table)
    return handler


TCP, UDP = "tcp", "udp"
# Honeypots that write the line of a probe well after it: the check keeps the hold (and waits for
# the log line) this many seconds after its last probe, plus LATE_MARGIN. ddospot v2 writes the
# scanner event of a source only `Classify` seconds after its first packet (internal/app/config.go,
# default 60 s, written by the 1 s tick of internal/limit); ciscoasa v2 writes the ike.ratelimit
# summaries of a source a minute later (internal/ike/summary.go).
LATE_WRITE = {"ddospot": 60.0, "ciscoasa": 60.0}
LATE_MARGIN = 10.0
# Honeypots hptest.sh --nmap leaves out as a whole although the check probes them: nmap's default
# scripts (dns-recursion) make ddospot ask Quad9, so they would leave the host
NMAP_LEAVE_OUT = {"ddospot": "nmap's DNS scripts make it recurse to Quad9 (DDOSPOT_DNS_RECURSION)"}
FTP_DATA_REASON = "FTP data port, nothing listens on it"
MIRROR_REASON = "dionaea mirrors a connection on this port back to its source"
TFTP_REASON = "dionaea's TFTP answers only the first request after its start, later ones are refused"
HTTP81_REASON = "dionaea's HTTP service (port 81) closes a GET without an answer"
# elasticpot, ipphoney and rdphoneypot log no event of a source in `blacklist` of honeypot.cfg; its
# fallback in honeypot.py (CONFIG.get('honeypot', 'blacklist', fallback=...)) holds while the
# dist/honeypot.cfg of T-Pot leaves the line commented out
BLACKLIST_FALLBACK = ("127.0.0.1", "192.168.0.0/16")

PROBES: Dict[str, Callable[[str, List[Port], str], ProbeResult]] = {
    "adbhoney": PortProbe({(5555, TCP): adb}),
    # every port of the LLM edition too (default): an answer would call the LLM
    "beelzebub": PortProbe({(22, TCP): knock}, default=knock),
    "ciscoasa": PortProbe({
        (8443, TCP): http("POST", "/+webvpn+/index.html", _LOGIN_FORM,
                          f"username={{token}}&password={PASSWORD}&group_list=Employees", tls=True),
        (500, UDP): ike(natt=False),
        (4500, UDP): ike(natt=True),
    }),
    "citrixhoneypot": PortProbe({(443, TCP): http("GET", "/vpn/../vpns/cfg/smb.conf?{token}", tls=True)}),
    "conpot_IEC104": PortProbe({
        (2404, TCP): tcp_exchange(bytes.fromhex("680407000000"),
                                  lambda d: d.startswith(bytes.fromhex("68040b000000")), "IEC 104 STARTDT",
                                  lambda d: len(d) >= 6),
        (20000, TCP): port_only("DNP3 not checked (the outstation address is per install)"),
        (161, UDP): udp_exchange(_snmp_get_sysdescr(), lambda d: d[:1] == b"\x30", "SNMP"),
    }),
    "conpot_guardian_ast": PortProbe({
        (10001, TCP): tcp_exchange(b"\x01I20100\n", lambda d: b"I20100" in d, "Guardian AST",
                                   lambda d: b"I20100" in d and b"IN-TANK" in d),
    }),
    "conpot_ipmi": PortProbe({
        (623, UDP): udp_exchange(bytes.fromhex("0600ff070000000000000000000920" "18c88100388e04b5"),
                                 lambda d: d.startswith(bytes.fromhex("0600ff07")), "IPMI"),
    }),
    "conpot_kamstrup_382": PortProbe({
        (1025, TCP): port_only("the meter protocol is not checked"),
        (50100, TCP): _kamstrup_management,
    }),
    "conpot_beckhoff_cx": PortProbe({
        (48898, TCP): port_only("ADS not checked (the AMS Net ID is per install)"),
        (48899, UDP): udp_exchange(struct.pack("<III6sHI", 0x71146603, 0, 1, bytes([10, 0, 0, 2, 1, 1]), 10000, 0),
                                   lambda d: d[:4] == struct.pack("<I", 0x71146603), "ADS discovery"),
        (4840, TCP): _opcua_hello,
    }),
    "conpot_building_automation": PortProbe({
        (3671, UDP): udp_exchange(bytes.fromhex("06100201000e") + bytes([8, 1]) + b"\0" * 6,
                                  lambda d: d[2:4] == b"\x02\x02", "KNXnet/IP"),
        (47808, UDP): udp_exchange(bytes.fromhex("810a000c0120ffff00ff1008"), lambda d: b"\x10\x00\xc4" in d,
                                   "BACnet"),
    }),
    "conpot_hart_gateway": PortProbe({(5094, TCP): _hart_ip}),
    "conpot_iccp": PortProbe({
        (102, TCP): tcp_exchange(bytes.fromhex("0300000b06e00000000100"), lambda d: len(d) >= 6 and d[5] == 0xD0,
                                 "COTP", lambda d: len(d) >= 4 and len(d) >= struct.unpack("!H", d[2:4])[0]),
    }),
    "conpot_plc_modbus": PortProbe({(502, TCP): port_only("Modbus not checked (the unit id is per template)")}),
    "cowrie": PortProbe({(22, TCP): ssh_banner, (23, TCP): telnet_login}),
    "ddospot": PortProbe({
        (19, UDP): udp_exchange(b"{token}\n", lambda d: len(d) > 0, "CHARGEN", token=True),
        (53, UDP): _dns_version_bind(tcp=False),
        (53, TCP): _dns_version_bind(tcp=True),
        (123, UDP): udp_exchange(b"\x1b" + b"\0" * 47, lambda d: len(d) >= 48 and d[0] & 0x07 == 4, "NTP"),
        (1900, UDP): udp_exchange(_SSDP, lambda d: d.startswith(b"HTTP/1.1 200"), "SSDP", token=True),
    }),
    "dicompot": PortProbe({(11112, TCP): _dicom_echo}),
    "dionaea": PortProbe({
        # the source port of FTP data in active mode: nothing listens on it
        (20, TCP): not_tried(FTP_DATA_REASON),
        (21, TCP): ftp_login,
        # the mirror service connects back to the source of a connection (dionaea's mirror.py,
        # mirrord on 42): a probe would start a loop of connections, hundreds of events
        (42, TCP): not_tried(MIRROR_REASON),
        # the TFTP listener answered only the first request after a start of dionaea, every later
        # one was refused (E2E runs 3 and 3b): the probe could take it down for real attackers
        (69, UDP): not_tried(TFTP_REASON),
        # the http service of dionaea (services/http.yaml: ports 81) closes a GET without an answer
        (81, TCP): port_only(HTTP81_REASON),
        (135, TCP): connect_only,
        (445, TCP): connect_only,
        (1433, TCP): connect_only,
        (1723, TCP): connect_only,
        (1883, TCP): _mqtt,
        (3306, TCP): _mysql_greeting,
        (27017, TCP): connect_only,
    }),
    "elasticpot": PortProbe({(9200, TCP): http("GET", "/_cluster/health?{token}")}, ignores=BLACKLIST_FALLBACK),
    "endlessh": PortProbe({(2222, TCP): tarpit}),
    # knocked at only, on every port: an answer would call the LLM
    "galah": PortProbe({(80, TCP): knock, (8080, TCP): knock, (443, TCP): knock, (8443, TCP): knock},
                       default=knock),
    "go-pot": PortProbe({(8080, TCP): http("GET", "/tpot-check/{token}?probe={token}")}),
    "h0neytr4p": PortProbe({
        (80, TCP): http("GET", "/.env?probe={token}"),
        (443, TCP): http("GET", "/.env?probe={token}", tls=True),
    }),
    "hellpot": PortProbe({(8080, TCP): http("GET", "/tpot-check/{token}?probe={token}")}),
    "heralding": PortProbe({
        (21, TCP): ftp_login,
        (22, TCP): ssh_banner,
        (23, TCP): telnet_login,
        (25, TCP): smtp(auth=True),
        (80, TCP): _basic_auth(tls=False),
        (110, TCP): pop3_login(),
        (143, TCP): imap_login(),
        (443, TCP): _basic_auth(tls=True),
        (465, TCP): smtp(auth=True, tls=True),
        (636, TCP): connect_only,
        (990, TCP): connect_only,
        (993, TCP): imap_login(tls=True),
        (995, TCP): pop3_login(tls=True),
        (1080, TCP): socks5_login,
        (3306, TCP): connect_only,
        (3389, TCP): connect_only,
        (5432, TCP): connect_only,
        (5900, TCP): connect_only,
        (8883, TCP): connect_only,
    }),
    "honeyaml": PortProbe({(8080, TCP): http("GET", "/api/v1/internal/status?probe={token}")}),
    # qHoneypots: ftp, telnet, pop3, imap, smtp, http(s), socks5 and elastic as honeypots.sh
    # probes them, every other port of it only for being open
    "honeypots": PortProbe({
        (21, TCP): ftp_login,
        (22, TCP): connect_only,
        (23, TCP): telnet_login,
        (25, TCP): smtp(auth=True),
        (53, TCP): connect_only,
        (67, UDP): send_only,
        (80, TCP): _qhp_http(tls=False),
        (110, TCP): pop3_login(),
        (123, TCP): connect_only,
        (143, TCP): imap_login(),
        (161, TCP): connect_only,
        (389, TCP): connect_only,
        (443, TCP): _qhp_http(tls=True),
        (445, TCP): connect_only,
        (631, TCP): connect_only,
        (1080, TCP): socks5_login,
        (1433, TCP): connect_only,
        (1521, TCP): connect_only,
        (3306, TCP): connect_only,
        (3389, TCP): connect_only,
        (5060, TCP): connect_only,
        (5432, TCP): connect_only,
        (5900, TCP): connect_only,
        (6379, TCP): connect_only,
        (6667, TCP): connect_only,
        (8080, TCP): connect_only,
        (9100, TCP): connect_only,
        (9200, TCP): _qhp_elastic,
        (11211, TCP): connect_only,
    }),
    "ipphoney": PortProbe({(631, TCP): http("GET", "/tpot-check/{token}")}, ignores=BLACKLIST_FALLBACK),
    "log4pot": PortProbe({(8080, TCP): http("GET", "/tpot-check/{token}?probe={token}")}),
    "mailoney": PortProbe({
        (25, TCP): smtp(mail=True),
        (465, TCP): smtp(mail=True, tls=True),
        (587, TCP): smtp(mail=True),
    }),
    "medpot": PortProbe({(2575, TCP): _hl7}),
    "miniprint": PortProbe({
        (9100, TCP): _pjl,
        (8000, TCP): http("POST", "/login", _LOGIN_FORM, f"username={{token}}&password={PASSWORD}"),
    }),
    "rdphoneypot": PortProbe({(3389, TCP): _rdp}, ignores=BLACKLIST_FALLBACK),
    "redishoneypot": PortProbe({(6379, TCP): _redis}),
    "sentrypeer": PortProbe({(5060, TCP): _sip_options(udp=False), (5060, UDP): _sip_options(udp=True)}),
    "wordpot": PortProbe({
        (80, TCP): http("POST", "/wp-login.php", _LOGIN_FORM, f"log={{token}}&pwd={PASSWORD}&wp-submit=Log+In"),
    }),
}


# sentrypeer with SENTRYPEER_PEER_TO_PEER shares the sources it sees with its peers, so the
# probe only knocks at the TCP port and leaves the UDP one alone
SENTRYPEER_P2P = PortProbe({(5060, TCP): port_only(P2P_REASON), (5060, UDP): not_tried(P2P_REASON)},
                           default=not_tried(P2P_REASON))


# probes of a honeypot whose settings in the compose file ask for another one: name -> (the
# environment key, the probe); hptest.sh takes the same (hptest_table)
VARIANTS: Dict[str, Tuple[str, PortProbe]] = {"sentrypeer": ("SENTRYPEER_PEER_TO_PEER", SENTRYPEER_P2P)}


def _variant(honeypot: Honeypot) -> Optional[Callable[[str, List[Port], str], ProbeResult]]:
    """Another probe for a honeypot whose settings in the compose file ask for it."""
    found = VARIANTS.get(honeypot.name)
    if found is not None and found[0] in honeypot.environment:
        return found[1]
    return None


def uncovered(names: Iterable[str]) -> List[str]:
    """The honeypots of `names` with neither a probe nor a place in EXTERNAL."""
    return sorted(name for name in names if name not in PROBES and name not in EXTERNAL)


def probe(honeypot: Honeypot, host: str, token: str,
          probes: Optional[Dict[str, Callable[[str, List[Port], str], ProbeResult]]] = None) -> ProbeResult:
    """The probe of one honeypot, never an exception: one that is not running, has no published
    port or no probe says so in its result."""
    probes = PROBES if probes is None else probes
    if not honeypot.running:
        return ProbeResult(SKIPPED, False, f"not running (container {honeypot.container})", answer_checked=False)
    if not honeypot.ports:
        return ProbeResult(SKIPPED, False, "no published port", answer_checked=False)
    function = probes.get(honeypot.name)
    if function is not None:
        function = _variant(honeypot) or function
    if function is None:
        return ProbeResult(SKIPPED, False, "no probe for this honeypot", answer_checked=False)
    try:
        return function(host, honeypot.ports, token)
    except Exception as exc:  # a probe must not end the check
        return ProbeResult(SKIPPED, False, f"probe failed: {_error_text(exc)}", answer_checked=False)


def run_probes(host: str, honeypots: Iterable[Honeypot], token: str, budget: float = RUN_BUDGET,
               workers: int = WORKERS,
               probes: Optional[Dict[str, Callable[[str, List[Port], str], ProbeResult]]] = None,
               check: Optional[Callable[[], None]] = None,
               into: Optional[Dict[str, ProbeResult]] = None,
               skip: Optional[Callable[[Honeypot], str]] = None) -> Dict[str, ProbeResult]:
    """Every honeypot that is not external, `workers` at a time; a probe starts only within
    `budget` seconds, one that did not start in time says so. `check` (EwsHold.check) is called
    before each probe starts: when it raises (the hold of ewsposter ended), no further probe
    starts and the error is raised once the probes that run have ended. No probe outlives this
    call, so the hold can end right after it. `into` gets the results also when the call ends by
    an exception (Ctrl+C, the hold): the cleanup still knows what the probes sent. `skip` is asked
    right before each probe: a reason it gives leaves that honeypot alone (skipped, the reason as
    detail)."""
    targets = [h for h in honeypots if not h.external]
    results: Dict[str, ProbeResult] = into if into is not None else {}
    if not targets:
        return results
    end = time.monotonic() + budget
    stop = threading.Event()
    errors: List[BaseException] = []
    late = ProbeResult(NOT_REACHED, False, "not probed: the time for the probes ran out", answer_checked=False)
    stopped = ProbeResult(SKIPPED, False, "not probed: the check stopped", answer_checked=False)

    def one(honeypot: Honeypot) -> ProbeResult:
        if stop.is_set():
            return stopped if errors else late
        if check is not None:
            try:
                check()
            except BaseException as exc:
                stop.set()
                errors.append(exc)
                return stopped
        if time.monotonic() >= end:
            return late
        why = skip(honeypot) if skip is not None else ""
        if why:
            return ProbeResult(SKIPPED, False, why, answer_checked=False)
        return probe(honeypot, host, token, probes)

    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = [(pool.submit(one, honeypot), honeypot) for honeypot in targets]
    try:
        wait([future for future, _ in futures], timeout=max(0.0, end - time.monotonic()))
    finally:
        # what has not started does not start any more; what runs ends within its PROBE_TIME_MAX
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
        for future, honeypot in futures:
            if future.cancelled() or not future.done():
                results[honeypot.name] = late
            else:
                results[honeypot.name] = future.result()
    if errors:
        raise errors[0]
    return results


# -- hptest.sh: the same table ------------------------------------------------------------------

HPTEST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "docker", "tpotinit", "dist", "bin", "hptest.sh")
HPTEST_BEGIN = "# >>> probe table >>>"
HPTEST_END = "# <<< probe table <<<"


def hptest_table() -> Dict[str, object]:
    """What hptest.sh --nmap takes from the probe table: per honeypot the ports (inside the
    container) it leaves out, or "*" for all of them, and the environment key that makes a variant
    leave all of them out; the late writers and their seconds."""
    def left_out(name: str, table: PortProbe) -> object:
        if name in NMAP_LEAVE_OUT or getattr(getattr(table, "default", None), "leave_out", None):
            return "*"
        return sorted([str(target), protocol] for (target, protocol), handler in table.handlers.items()
                      if getattr(handler, "leave_out", None))

    skipped: Dict[str, object] = {}
    for name, table in sorted(PROBES.items()):
        found = left_out(name, table)
        if found:
            skipped[name] = found
    # a variant: with its environment key in the service, its own list instead
    variants = {name: {"env": key, "skipped": left_out(name, table)} for name, (key, table) in sorted(VARIANTS.items())}
    return {"skipped": skipped, "variants": variants,
            "late": {name: int(seconds) for name, seconds in sorted(LATE_WRITE.items())}}


def hptest_block() -> str:
    """The generated lines between HPTEST_BEGIN and HPTEST_END (python of fuPORTS, no single quote)."""
    import json
    data = hptest_table()
    lines = [HPTEST_BEGIN,
             "# generated from tpotctl/checks/probes.py by python3 -m tpotctl.checks.probes --hptest",
             "skipped = " + json.dumps(data["skipped"], sort_keys=True),
             "variants = " + json.dumps(data["variants"], sort_keys=True),
             "late = " + json.dumps(data["late"], sort_keys=True),
             HPTEST_END]
    text = "\n".join(lines)
    assert "'" not in text
    return text


def write_hptest(path: str = HPTEST, check: bool = False) -> bool:
    """Writes the block into hptest.sh (in place); with `check` only compares. True when it is current."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    start, end = text.index(HPTEST_BEGIN), text.index(HPTEST_END) + len(HPTEST_END)
    new = text[:start] + hptest_block() + text[end:]
    if new == text:
        return True
    if check:
        return False
    with open(path, "r+", encoding="utf-8") as handle:
        handle.write(new)
        handle.truncate()
    return True


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] != ["--hptest"]:
        sys.exit("usage: python3 -m tpotctl.checks.probes --hptest [--check]")
    current = write_hptest(check="--check" in sys.argv)
    if not current:
        print(f"{HPTEST}: the probe table block is not current, run python3 -m tpotctl.checks.probes --hptest")
    sys.exit(0 if current else 1)
