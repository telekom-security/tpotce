"""Container-local T-Pot firewall tests. No host namespace or firewall is modified."""

import json
import ipaddress
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time


MARKER = b"tpot-real-service:"
TCP_SERVICES = (64294, 64295, 64297, 2222, 24444)
UDP_SERVICES = (1053, 24444)


def serve_connection(connection):
    with connection:
        while True:
            data = connection.recv(4096)
            if not data:
                return
            connection.sendall(MARKER + data)


def serve_port(protocol, port):
    kind = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", port))
        if protocol == "tcp":
            server.listen()
        while True:
            if protocol == "tcp":
                connection, _ = server.accept()
                threading.Thread(target=serve_connection, args=(connection,), daemon=True).start()
            else:
                data, peer = server.recvfrom(4096)
                server.sendto(MARKER + data, peer)


def serve():
    for protocol, ports in (("tcp", TCP_SERVICES), ("udp", UDP_SERVICES)):
        for port in ports:
            threading.Thread(target=serve_port, args=(protocol, port), daemon=True).start()
    while not Path("/tmp/add-listener").exists():
        time.sleep(0.1)
    for protocol in ("tcp", "udp"):
        threading.Thread(target=serve_port, args=(protocol, 24555), daemon=True).start()
    threading.Event().wait()


def command(*args, input=None, check=True, timeout=60):
    result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{args}: {result.stdout}\n{result.stderr}")
    return result


def test(image, init_image, directory, project, bind_ip, timeout):
    address = ipaddress.ip_address(bind_ip)
    if address.version != 4 or not address.is_loopback:
        raise ValueError("Glutton smoke tests require an IPv4 loopback --bind-ip")
    directory = Path(directory)
    for name in ("log", "payloads", "samples"):
        (directory / name).mkdir()
        (directory / name).chmod(0o777)
    fixture = directory / "fixture.json"
    fixture.write_text(json.dumps({"services": {
        "glutton": {"network_mode": "host"},
        "example": {"ports": ["2222-2223:2222-2223", "1053:53/udp"]},
    }}))
    tcp_ports = (*TCP_SERVICES, 2223, 24555, 31337, 31338, 22)
    udp_ports = (*UDP_SERVICES, 24555, 5060)
    services = {
        "sandbox": {
            "image": init_image, "container_name": project + "-service", "user": "0:0",
            "entrypoint": ["python3", "/probe.py", "serve"],
            "cap_add": ["NET_ADMIN"], "environment": {"TPOT_CAPTURE_INTERFACE": "eth0"},
            "volumes": [f"{Path(__file__).resolve()}:/probe.py:ro", f"{fixture}:/fixture.json:ro"],
            "ports": [f"{bind_ip}::{port}/tcp" for port in tcp_ports]
                     + [f"{bind_ip}::{port}/udp" for port in udp_ports],
        },
        "glutton": {
            "image": image, "network_mode": "service:sandbox", "read_only": True,
            "cap_add": ["NET_ADMIN"], "environment": {"TPOT_CAPTURE_INTERFACE": "eth0"},
            "tmpfs": ["/var/lib/glutton:uid=2000,gid=2000", "/run:uid=2000,gid=2000"],
            "volumes": [f"{directory / name}:/opt/glutton/{name}" for name in ("payloads", "samples")]
                       + [f"{directory / 'log'}:/var/log/glutton"],
        },
    }
    compose_file = directory / "docker-compose.yml"
    compose_file.write_text(json.dumps({"services": services}))

    def compose(*args, **kwargs):
        return command("docker", "compose", "-f", str(compose_file), "-p", project, *args, **kwargs)

    def execute(*args, **kwargs):
        return compose("exec", "-T", "sandbox", *args, **kwargs)

    def wait_for(predicate, description):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.2)
        raise AssertionError(f"Timed out: {description}")

    def state():
        container = compose("ps", "-aq", "glutton").stdout.strip()
        return json.loads(command("docker", "inspect", container).stdout)[0]["State"]

    def mapping(port, protocol):
        result = compose("port", "--protocol", protocol, "sandbox", str(port)).stdout.strip()
        return bind_ip, int(result.rsplit(":", 1)[1])

    def connect(port):
        return socket.create_connection(mapping(port, "tcp"), timeout=3)

    def check_services(dynamic=False):
        for port in (*TCP_SERVICES, *((24555,) if dynamic else ())):
            with connect(port) as client:
                client.sendall(b"still-ours")
                assert client.recv(4096) == MARKER + b"still-ours", f"TCP service stolen: {port}"
        for port in (*UDP_SERVICES, *((24555,) if dynamic else ())):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(3)
                client.sendto(b"still-ours", mapping(port, "udp"))
                assert client.recv(4096) == MARKER + b"still-ours", f"UDP service stolen: {port}"

    def check_catchall():
        token = f"tpot-glutton-{time.monotonic_ns()}"
        with connect(31337) as client:
            client.sendall(f"GET /{token} HTTP/1.0\r\nHost: test\r\n\r\n".encode())
            assert b"HTTP/" in client.recv(8192), "No HTTP response through catch-all"
        with connect(22) as client:
            assert client.recv(4096).startswith(b"SSH-"), "New SSH handler did not answer"
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with connect(31338) as client, context.wrap_socket(client, server_hostname="glutton.test") as tls:
            tls.sendall(f"GET /tls-{token} HTTP/1.0\r\nHost: glutton.test\r\n\r\n".encode())
            assert b"HTTP/" in tls.recv(8192), "TLS catch-all did not answer"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(5)
            client.sendto(
                b"OPTIONS sip:test SIP/2.0\r\nVia: SIP/2.0/UDP test;branch=z9hG4bK-tpot\r\n"
                b"From: <sip:test@test>;tag=test\r\nTo: <sip:test@test>\r\n"
                b"Call-ID: tpot-test\r\nCSeq: 1 OPTIONS\r\nContent-Length: 0\r\n\r\n",
                mapping(5060, "udp"))
            assert client.recv(8192).startswith(b"SIP/2.0"), "New UDP SIP handler did not answer"
        log = directory / "log/glutton.log"
        wait_for(lambda: log.exists() and token in log.read_text(), "real JSON event with probe token")
        events = [json.loads(line) for line in log.read_text().splitlines() if token in line]
        assert any(str(event.get("dstPort")) == "31337" and event.get("srcHost")
                   and event.get("srcPort") and event.get("timestamp")
                   and event.get("event_kind") == "session" and event.get("transport") == "tcp"
                   and event.get("handlers") and event.get("decoded") for event in events), events
        wait_for(lambda: any(json.loads(line).get("transport") == "udp"
                             and json.loads(line).get("handler") == "sip"
                             for line in log.read_text().splitlines()), "structured UDP session")
        wait_for(lambda: any(json.loads(line).get("tls", {}).get("ja4")
                             and json.loads(line).get("tls", {}).get("ja3")
                             and "tls" in json.loads(line).get("handlers", [])
                             for line in log.read_text().splitlines() if "tls-" + token in line),
                 "real TLS fingerprints and protocol stack")
        assert all(json.loads(line).get("event_kind") == "session"
                   for line in log.read_text().splitlines()), "Diagnostics mixed into session log"
        assert (directory / "log/diagnostic.log").exists(), "Diagnostic log missing"

    def check_closed(port):
        # docker-proxy may accept before it learns that the target port is closed.
        try:
            with connect(port) as client:
                client.sendall(b"GET /must-not-be-glutton HTTP/1.0\r\nHost: test\r\n\r\n")
                assert client.recv(4096) == b"", f"Closed port {port} was taken by Glutton"
        except (ConnectionRefusedError, ConnectionResetError):
            pass

    compose("up", "-d", "sandbox")
    wait_for(lambda: execute("test", "-r", "/proc/net/tcp", check=False).returncode == 0, "sandbox")
    check_services()
    persistent = connect(64295)
    compose("up", "-d", "glutton")
    wait_for(lambda: state()["Status"] == "exited", "Glutton refusal without tpotinit firewall")
    assert state()["ExitCode"] == 3, state()
    assert "tpotinit has not installed" in compose("logs", "glutton").stdout
    check_services()

    probe = execute("iptables", "-w", "-t", "mangle", "-N", "TPOT_TEST_LEGACY", check=False)
    if probe.returncode == 0:
        probe = execute("iptables", "-w", "-t", "mangle", "-A", "TPOT_TEST_LEGACY",
                        "-p", "tcp", "-m", "state", "!", "--state", "ESTABLISHED,RELATED",
                        "-j", "TPROXY", "--on-port", "5000", "--on-ip", "127.0.0.1", check=False)
        execute("iptables", "-w", "-t", "mangle", "-F", "TPOT_TEST_LEGACY")
        execute("iptables", "-w", "-t", "mangle", "-X", "TPOT_TEST_LEGACY")
    legacy_supported = probe.returncode == 0
    if legacy_supported:
        for protocol, port in (("tcp", "5000"), ("udp", "5001")):
            execute("iptables", "-w", "-t", "mangle", "-A", "PREROUTING", "-i", "eth0",
                    "-p", protocol, "-m", "state", "!", "--state", "ESTABLISHED,RELATED",
                    "!", "--dport", "22", "-j", "TPROXY", "--on-port", port, "--on-ip", "127.0.0.1")
        execute("iptables", "-w", "-t", "mangle", "-A", "PREROUTING", "-i", "tpot-unused0",
                "-p", "tcp", "-m", "comment", "--comment", "foreign-rule",
                "-j", "TPROXY", "--on-port", "5999", "--on-ip", "127.0.0.1")
    else:
        print(f"Legacy iptables migration subtest unavailable on this kernel: {probe.stderr}", flush=True)
    execute("nft", "add", "table", "ip", "tpot_test_foreign")
    execute("/opt/tpot/bin/rules.sh", "/fixture.json", "set")
    if legacy_supported:
        migrated = execute("iptables", "-w", "-t", "mangle", "-S", "PREROUTING").stdout
        assert "--on-port 5000" not in migrated and "--on-port 5001" not in migrated, migrated
        assert "foreign-rule" in migrated, migrated
    original = execute("nft", "list", "table", "ip", "tpot_glutton").stdout
    execute("/opt/tpot/bin/rules.sh", "/fixture.json", "set")
    assert original == execute("nft", "list", "table", "ip", "tpot_glutton").stdout
    rejected = execute("nft", "-f", "-", input=(
        "flush table ip tpot_glutton\nadd rule ip tpot_glutton missing-chain drop\n"), check=False)
    assert rejected.returncode != 0
    assert original == execute("nft", "list", "table", "ip", "tpot_glutton").stdout

    for iteration in range(3):
        compose("start", "glutton")
        wait_for(lambda: "127.0.0.1:5000" in execute("ss", "-lnt").stdout, "Glutton listener")
        check_services(dynamic=iteration > 0)
        check_catchall()
        if iteration == 0:
            execute("touch", "/tmp/add-listener")
            time.sleep(0.5)
            check_services(dynamic=True)
            check_closed(2223)
        started = time.monotonic()
        if iteration == 1:
            compose("kill", "-s", "SIGKILL", "glutton")
        else:
            compose("stop", "-t", "10", "glutton")
        elapsed = time.monotonic() - started
        assert elapsed < 10, f"Shutdown hung for {elapsed:.1f}s"
        assert state()["ExitCode"] == (137 if iteration == 1 else 0), state()
        check_services(dynamic=True)
        persistent.sendall(b"existing-session")
        assert persistent.recv(4096) == MARKER + b"existing-session", "Existing SSH-port session lost"
        check_closed(31337)
        print(f"Stop cycle {iteration + 1}: {elapsed:.2f}s; TCP/UDP services reachable", flush=True)

    persistent.close()
    compose("start", "glutton")
    wait_for(lambda: "127.0.0.1:5000" in execute("ss", "-lnt").stdout, "Glutton listener")
    execute("/opt/tpot/bin/rules.sh", "/fixture.json", "unset")
    execute("/opt/tpot/bin/rules.sh", "/fixture.json", "unset")
    check_services(dynamic=True)
    check_closed(31337)
    assert state()["Running"], "Removing tpotinit rules unexpectedly killed Glutton"
    execute("nft", "list", "table", "ip", "tpot_test_foreign")
    if legacy_supported:
        assert "foreign-rule" in execute("iptables", "-w", "-t", "mangle", "-S", "PREROUTING").stdout
    print("Atomic/idempotent tpotinit set/unset and JSON pipeline fields verified", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        serve()
    else:
        test(*sys.argv[2:-1], float(sys.argv[-1]))
