#!/usr/bin/env python3
"""A stand-in for the ingest endpoint of a HIVE (nginx on 64294) for the smoke tests: HTTPS with a
certificate of a test CA (or a self-signed one, as T-Pot makes it), every request recorded as one JSON line.

    hive_stub.py certs DIR NAME [--self-signed] [--san SAN ...]
        DIR/NAME.crt and NAME.key (ECDSA P-384) with the subjectAltName entries given (IP:127.0.0.1,
        DNS:hive.test): signed by DIR/ca.crt and ca.key (P-384, made once), with --self-signed its own
        issuer as the certificate of a HIVE; needs openssl.
    hive_stub.py serve --cert F --key F --log F [--bind IP] [--port N] [--status N | --status-file F]
                       [--probe-file F]
        Answers every request with the status given; --status-file is read on each request (an
        integer, a missing or unreadable file means --status). The record: time, method, path,
        headers (names in lowercase), status, gzip (Content-Encoding gzip) and body (the text,
        gunzipped). The capability probe /tpot-ingest gets the two-stage answer of nginx, by the
        mode of --probe-file (read on each request) and whether it brings an Authorization header:
          mode                  without Authorization   with Authorization
          204 (also a missing   401 + login             204 + 2 (takes the format)
          or unreadable file)
          401 (an old HIVE)     401                     401
          login (wrong login)   401 + login             401 + login
          429 (busy)            429                     429
          any other N           401 + login             N (503: its Logstash not ready)
        (+ login / + 2: the header X-TPot-Ingest); it is recorded too, with the status it got.

Standard library only (it runs with the python3 of the tpotinit image)."""

import argparse
import gzip
import http.server
import json
import os
import ssl
import subprocess
import sys
import threading
import time


def run(*command):
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


P384 = ("-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-384", "-sha384")


def make_certs(folder, name, sans, self_signed=False):
    key, csr, crt = (os.path.join(folder, name + ext) for ext in (".key", ".csr", ".crt"))
    if self_signed:
        # as T-Pot makes the certificate of a HIVE: req -x509 with the subjectAltName entries only
        run("openssl", "req", "-nodes", "-x509", *P384, "-keyout", key, "-out", crt, "-days", "2",
            "-subj", "/CN=hive-stub", *(("-addext", "subjectAltName=" + ",".join(sans)) if sans else ()))
        for path in (crt, key):
            os.chmod(path, 0o644)
        return
    ca_crt, ca_key = os.path.join(folder, "ca.crt"), os.path.join(folder, "ca.key")
    if not os.path.exists(ca_crt):
        run("openssl", "req", "-x509", "-new", "-nodes", *P384,
            "-keyout", ca_key, "-out", ca_crt, "-days", "2", "-subj", "/CN=T-Pot smoke test CA",
            "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    extensions = os.path.join(folder, name + ".ext")
    with open(extensions, "w", encoding="utf-8") as handle:
        handle.write("basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n")
        if sans:
            handle.write("subjectAltName=" + ",".join(sans) + "\n")
    run("openssl", "req", "-new", "-nodes", *P384, "-keyout", key, "-out", csr, "-subj", "/CN=hive-stub")
    run("openssl", "x509", "-req", "-sha384", "-in", csr, "-CA", ca_crt, "-CAkey", ca_key, "-CAcreateserial",
        "-out", crt, "-days", "2", "-extfile", extensions)
    for path in (csr, extensions):
        os.remove(path)
    for path in (ca_crt, crt, key):
        os.chmod(path, 0o644)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    lock = threading.Lock()
    log_path = ""
    status = 200
    status_file = ""
    probe_file = ""

    def current_status(self):
        if self.status_file:
            try:
                with open(self.status_file, encoding="utf-8") as handle:
                    return int(handle.read().strip())
            except (OSError, ValueError):
                pass
        return self.status

    def probe_mode(self):
        if self.probe_file:
            try:
                with open(self.probe_file, encoding="utf-8") as handle:
                    text = handle.read().strip()
                return text if text == "login" else int(text)
            except (OSError, ValueError):
                pass
        return 204

    def probe_answer(self):
        """(status, X-TPot-Ingest or None) of the probe, as nginx answers it (lsweb.conf)."""
        mode = self.probe_mode()
        if mode in (401, 429):
            return mode, None
        if mode == "login" or "Authorization" not in self.headers:
            return 401, "login"
        if mode == 204:
            return 204, "2"
        return mode, None

    def read_body(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            parts = []
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return b"".join(parts)
                parts.append(self.rfile.read(size))
                self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def handle_any(self):
        raw = self.read_body()
        compressed = self.headers.get("Content-Encoding", "").lower() == "gzip"
        body = gzip.decompress(raw) if compressed and raw else raw
        probe = self.path.split("?", 1)[0] == "/tpot-ingest"
        status, mark = self.probe_answer() if probe else (self.current_status(), None)
        record = {"time": time.time(), "method": self.command, "path": self.path,
                  "headers": {key.lower(): value for key, value in self.headers.items()},
                  "status": status, "gzip": compressed, "body": body.decode("utf-8", "surrogateescape")}
        line = json.dumps(record, ensure_ascii=True) + "\n"
        with self.lock, open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(line)
        if probe:
            self.send_response(status)
            if mark:
                self.send_header("X-TPot-Ingest", mark)
            if status != 204:
                self.send_header("Content-Length", "0")
            self.end_headers()
            return
        answer = b'{"ok":true}' if status < 300 else b'{"ok":false}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    do_POST = do_PUT = do_GET = handle_any

    def log_message(self, format, *args):  # noqa: A002 - the name of the base class
        sys.stderr.write("hive_stub: " + (format % args) + "\n")


def serve(args):
    Handler.log_path = args.log
    Handler.status = args.status
    Handler.status_file = args.status_file or ""
    Handler.probe_file = args.probe_file or ""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    server = http.server.ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"hive_stub: listening on {args.bind}:{args.port}", file=sys.stderr, flush=True)
    server.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description="A stub of the ingest endpoint of a HIVE.")
    commands = parser.add_subparsers(dest="command", required=True)
    certs = commands.add_parser("certs", help="make a test CA and a certificate of the stub")
    certs.add_argument("folder")
    certs.add_argument("name")
    certs.add_argument("--self-signed", action="store_true", help="a certificate of its own, without the test CA")
    certs.add_argument("--san", action="append", default=[], help="a subjectAltName entry, e.g. IP:127.0.0.1")
    server = commands.add_parser("serve", help="answer and record requests")
    server.add_argument("--cert", required=True)
    server.add_argument("--key", required=True)
    server.add_argument("--log", required=True)
    server.add_argument("--bind", default="127.0.0.1")
    server.add_argument("--port", type=int, default=64294)
    server.add_argument("--status", type=int, default=200)
    server.add_argument("--status-file")
    server.add_argument("--probe-file")
    args = parser.parse_args(argv)
    if args.command == "certs":
        make_certs(args.folder, args.name, args.san, args.self_signed)
    else:
        serve(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
