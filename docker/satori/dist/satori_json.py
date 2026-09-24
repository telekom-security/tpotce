#!/usr/bin/env python3
import argparse
import datetime
import ipaddress
import json
import os
import signal
import subprocess
import sys
import warnings
from pathlib import Path


DEFAULT_MODULES = "tcp"
SUPPORTED_MODULES = "tcp,dhcp,smb,http,ssl,dns,ntp,ssh"
DEFAULT_LOG_PATH = "/var/log/satori/satori.json"
UNKNOWN_MATCH = "???"
ETHERNET = None


def default_interface():
    proc = subprocess.run(
        ["ip", "route", "show", "default"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    for line in proc.stdout.splitlines():
        fields = line.split()
        if "dev" in fields:
            return fields[fields.index("dev") + 1]
    return "eth0"


def valid_ip(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def parse_matches(value):
    matches = []
    for raw in [part for part in value.replace(";", "|").split("|") if part]:
        name, sep, weight = raw.rpartition(":")
        if not sep:
            name, weight = raw, ""
        weight_num = None
        try:
            weight_num = int(weight)
        except ValueError:
            pass
        matches.append({"name": name, "weight": weight_num, "raw": raw})
    matches.sort(key=lambda item: item["weight"] if item["weight"] is not None else -1, reverse=True)
    return matches[:10]


def top_matches(matches):
    if not matches:
        return []
    top_weight = matches[0].get("weight")
    return [match for match in matches if match.get("weight") == top_weight]


def selected_match(matches):
    if matches:
        return matches[0]["name"]
    return UNKNOWN_MATCH


def annotate_matches(event, matches):
    top = top_matches(matches)
    event["satori"].update({
        "match": selected_match(matches),
        "match_count": len(matches),
        "top_match_count": len(top),
        "ambiguous": len(top) > 1,
    })
    if top:
        event["satori"]["top_weight"] = top[0].get("weight")
    if len(top) == 1:
        event["satori"]["match_weight"] = top[0].get("weight")
    return event


def base_event(timestamp, src_ip, src_mac, module, raw_line, fingerprint):
    matches = parse_matches(fingerprint)
    event = {
        "timestamp": timestamp,
        "src_mac": src_mac,
        "mod": module.lower(),
        "subject": "cli",
        "satori": {
            "module": module,
            "raw": raw_line,
            "fingerprint": fingerprint,
            "commit": os.environ.get("SATORI_COMMIT", ""),
        },
    }
    if valid_ip(src_ip):
        event["src_ip"] = src_ip
    return annotate_matches(event, matches)


def parse_line(line):
    raw_line = line.rstrip("\n")
    parts = raw_line.split(";")
    if len(parts) < 5:
        return None

    timestamp, src_ip, src_mac, module = parts[:4]
    module = module.upper()
    fingerprint = parts[-1]
    event = base_event(timestamp, src_ip, src_mac, module, raw_line, fingerprint)

    if module == "TCP" and len(parts) >= 7:
        flags, signature = parts[4], parts[5]
        event.update({
            "raw_sig": signature,
            "params": flags,
            "subject": "srv" if flags == "SA" else "cli",
            "mod": "syn+ack" if flags == "SA" else "syn" if flags == "S" else "tcp",
            "os": event["satori"]["match"],
        })
        event["satori"].update({"protocol": "tcp", "tcp_flags": flags, "signature": signature})
    elif module == "USERAGENT" and len(parts) >= 6:
        user_agent = parts[4]
        event.update({"http_user_agent": user_agent, "raw_sig": user_agent, "params": user_agent, "mod": "http user-agent", "os": event["satori"]["match"]})
        event["user_agent"] = {"original": user_agent}
        event["satori"].update({"protocol": "http", "signature": user_agent})
    elif module == "HTTPSERVER" and len(parts) >= 6:
        server = parts[4]
        event.update({"app": event["satori"]["match"], "http_server": server, "raw_sig": server, "params": server, "mod": "http server"})
        event["satori"].update({"protocol": "http", "signature": server})
    elif module == "SSL" and len(parts) >= 7:
        fp_type, fp_hash = parts[4], parts[5]
        normalized = fp_type.lower()
        event.update({"app": event["satori"]["match"], "raw_sig": fp_hash, "params": fp_type, "mod": "tls"})
        event["tls_fingerprint_type"] = fp_type
        event["tls_fingerprint_hash"] = fp_hash
        if normalized in ("ja3", "ja3s", "ja4"):
            event[normalized] = fp_hash
        event["satori"].update({"protocol": "tls", "fingerprint_type": fp_type, "hash": fp_hash})
    elif module == "DHCP" and len(parts) >= 8:
        message_type, signature_type, signature = parts[4], parts[5], parts[6]
        event.update({"raw_sig": signature, "params": f"{message_type}/{signature_type}", "mod": "dhcp", "os": event["satori"]["match"]})
        event["satori"].update({"protocol": "dhcp", "message_type": message_type, "signature_type": signature_type, "signature": signature})
    elif module == "SMBNATIVE" and len(parts) >= 7:
        signature_type, signature = parts[4], parts[5]
        event.update({"raw_sig": signature, "params": signature_type, "mod": "smb native", "os": event["satori"]["match"]})
        event["satori"].update({"protocol": "smb", "signature_type": signature_type, "signature": signature})
    elif module == "SMBBROWSER" and len(parts) >= 6:
        signature = parts[4]
        event.update({"raw_sig": signature, "params": signature, "mod": "smb browser", "os": event["satori"]["match"]})
        event["satori"].update({"protocol": "smb", "signature": signature})
    elif module == "NTP" and len(parts) >= 6:
        signature = parts[4]
        event.update({"raw_sig": signature, "params": signature, "mod": "ntp", "os": event["satori"]["match"]})
        event["satori"].update({"protocol": "ntp", "signature": signature})
    elif module in ("DNS", "SSH") and len(parts) >= 6:
        signature = parts[4]
        event.update({"app": event["satori"]["match"], "raw_sig": signature, "params": signature, "mod": module.lower()})
        event["satori"].update({"protocol": module.lower(), "signature": signature})
    else:
        event["raw_sig"] = ";".join(parts[4:-1])
        event["params"] = event["raw_sig"]
        event["satori"]["signature"] = event["raw_sig"]

    return event


def enrich_event(event, metadata):
    for key in ("src_ip", "dest_ip", "src_port", "dest_port", "src_mac", "dest_mac"):
        if metadata.get(key) not in (None, ""):
            event.setdefault(key, metadata[key])
    if metadata.get("transport"):
        event["network_transport"] = metadata["transport"]
    if metadata.get("layer"):
        event["satori"]["layer"] = metadata["layer"]
    return event


def packet_metadata(pkt, layer):
    metadata = {"layer": layer}
    try:
        ip4 = pkt.upper_layer
        metadata["src_ip"] = ip4.src_s
        metadata["dest_ip"] = ip4.dst_s
        transport = ip4.upper_layer
        if hasattr(transport, "sport"):
            metadata["src_port"] = transport.sport
        if hasattr(transport, "dport"):
            metadata["dest_port"] = transport.dport
        metadata["transport"] = transport.__class__.__name__.lower()
    except Exception:
        pass

    try:
        if layer == "eth" and ETHERNET is not None:
            eth = pkt[ETHERNET.Ethernet]
            metadata["src_mac"] = eth.src_s
            metadata["dest_mac"] = eth.dst_s
    except Exception:
        pass

    return metadata


def packet_type(buf, deps):
    tcp_packet = dhcp_packet = http_packet = udp_packet = False
    ssl_packet = smb_packet = dns_packet = ntp_packet = False
    quic_packet = ssh_packet = False
    pkt = None
    layer = ""

    candidates = (
        ("eth", deps["ethernet"].Ethernet),
        ("lcc", deps["linuxcc"].LinuxCC),
    )
    for candidate_layer, root in candidates:
        frame = root(buf)
        if hex(frame.type) != "0x800":
            continue
        pkt = frame
        layer = candidate_layer

        if frame[root, deps["ip"].IP, deps["tcp_layer"].TCP] is not None and frame[deps["tcp_layer"].TCP] is not None:
            tcp_packet = True
            tcp1 = frame[deps["ip"].IP].upper_layer
            if frame[root, deps["ip"].IP, deps["tcp_layer"].TCP, deps["ssl_layer"].SSL] is not None and frame[deps["ssl_layer"].SSL] is not None:
                ssl_packet = True
            if frame[root, deps["ip"].IP, deps["tcp_layer"].TCP, deps["http_layer"].HTTP] is not None and frame[deps["http_layer"].HTTP] is not None:
                http_packet = True
            if frame[root, deps["ip"].IP, deps["tcp_layer"].TCP, deps["dns_layer"].DNS] is not None and frame[deps["dns_layer"].DNS] is not None:
                dns_packet = True
            if tcp1.sport in (138, 139, 445) or tcp1.dport in (138, 139, 445):
                smb_packet = True
            if tcp1.dport == 3389:
                ssl_packet = True
            try:
                if "SSH" in tcp1.body_bytes.decode("utf-8"):
                    ssh_packet = True
            except Exception:
                pass

        if frame[root, deps["ip"].IP, deps["udp_layer"].UDP] is not None and frame[deps["udp_layer"].UDP] is not None:
            udp_packet = True
            udp1 = frame[deps["ip"].IP].upper_layer
            if frame[root, deps["ip"].IP, deps["udp_layer"].UDP, deps["dhcp_layer"].DHCP] is not None and frame[deps["dhcp_layer"].DHCP] is not None:
                dhcp_packet = True
            if frame[root, deps["ip"].IP, deps["udp_layer"].UDP, deps["dns_layer"].DNS] is not None and frame[deps["dns_layer"].DNS] is not None:
                dns_packet = True
            if frame[root, deps["ip"].IP, deps["udp_layer"].UDP, deps["ntp_layer"].NTP] is not None and frame[deps["ntp_layer"].NTP] is not None:
                ntp_packet = True
            if udp1.sport in (138, 139, 445) or udp1.dport in (138, 139, 445):
                smb_packet = True
            if udp1.dport == 443:
                quic_packet = True
        break

    return pkt, layer, tcp_packet, dhcp_packet, http_packet, udp_packet, ssl_packet, smb_packet, dns_packet, ntp_packet, quic_packet, ssh_packet


def should_emit(timestamp, fingerprint, history, minutes):
    if fingerprint is None:
        return False
    if minutes <= 0:
        return True
    previous = history.get(fingerprint)
    if previous is not None:
        try:
            delta = datetime.datetime.fromisoformat(timestamp) - datetime.datetime.fromisoformat(previous)
            if delta <= datetime.timedelta(minutes=minutes):
                return False
        except ValueError:
            pass
    history[fingerprint] = timestamp
    return True


def write_fingerprint(log, timestamp, fingerprint, metadata, history, limit_minutes):
    if not should_emit(timestamp, fingerprint, history, limit_minutes):
        return
    event = parse_line(f"{timestamp};{fingerprint}")
    if event is None:
        return
    event = enrich_event(event, metadata)
    log.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
    log.flush()


def parse_modules(configured):
    configured = (configured or DEFAULT_MODULES).strip()
    if not configured:
        configured = DEFAULT_MODULES
    if configured.lower() == "all":
        configured = SUPPORTED_MODULES
    return {module.strip().lower() for module in configured.split(",") if module.strip()}


def non_negative_int(value):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be greater than or equal to 0")
    return parsed


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run Satori live capture and write normalized NDJSON.")
    parser.add_argument(
        "-i",
        "--interface",
        default=os.environ.get("SATORI_INTERFACE", ""),
        help="capture interface; defaults to the system default route interface",
    )
    parser.add_argument(
        "-m",
        "--modules",
        default=os.environ.get("SATORI_MODULES", DEFAULT_MODULES),
        help=f"comma-separated module list or 'all' for {SUPPORTED_MODULES} (default: {DEFAULT_MODULES})",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=non_negative_int,
        default=os.environ.get("SATORI_LIMIT", "0") or "0",
        help="suppress duplicate fingerprints for this many minutes (default: 0)",
    )
    parser.add_argument(
        "-f",
        "--filter",
        default=os.environ.get("SATORI_FILTER", ""),
        help="optional BPF capture filter",
    )
    parser.add_argument(
        "--log",
        default=os.environ.get("SATORI_LOG", DEFAULT_LOG_PATH),
        help=f"NDJSON log path (default: {DEFAULT_LOG_PATH})",
    )
    return parser.parse_args(argv)


def load_satori():
    global ETHERNET

    sys.path.insert(0, "/opt/satori")
    warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API", category=UserWarning)
    try:
        import pcapy
    except ImportError:
        import pcapyplus as pcapy
    from pypacker import pypacker
    from pypacker.layer12 import ethernet, linuxcc
    from pypacker.layer3 import ip
    from pypacker.layer4 import tcp, udp, ssl
    from pypacker.layer567 import dhcp as dhcp_layer, dns, http, ntp
    import satoriCommon
    import satoriDHCP
    import satoriDNS
    import satoriHTTP
    import satoriNTP
    import satoriSMB
    import satoriSSH
    import satoriSSL
    import satoriTCP

    ETHERNET = ethernet
    pypacker.logger.setLevel(pypacker.logging.ERROR)

    return {
        "pcapy": pcapy,
        "ethernet": ethernet,
        "linuxcc": linuxcc,
        "ip": ip,
        "tcp_layer": tcp,
        "udp_layer": udp,
        "ssl_layer": ssl,
        "dhcp_layer": dhcp_layer,
        "dns_layer": dns,
        "http_layer": http,
        "ntp_layer": ntp,
        "satori_tcp": satoriTCP,
        "satori_dhcp": satoriDHCP,
        "satori_http": satoriHTTP,
        "satori_smb": satoriSMB,
        "satori_ssl": satoriSSL,
        "satori_dns": satoriDNS,
        "satori_ntp": satoriNTP,
        "satori_ssh": satoriSSH,
        "pypacker_version": satoriCommon.checkPyPackerVersion(),
        "tcp_lists": satoriTCP.BuildTCPFingerprintFiles(),
        "ssl_lists": satoriSSL.BuildSSLFingerprintFiles(),
        "dhcp_lists": satoriDHCP.BuildDHCPFingerprintFiles(),
        "http_user_agent_lists": satoriHTTP.BuildHTTPUserAgentFingerprintFiles(),
        "http_server_lists": satoriHTTP.BuildHTTPServerFingerprintFiles(),
        "smb_tcp_lists": satoriSMB.BuildSMBTCPFingerprintFiles(),
        "smb_udp_lists": satoriSMB.BuildSMBUDPFingerprintFiles(),
        "dns_lists": satoriDNS.BuildDNSFingerprintFiles(),
        "ntp_lists": satoriNTP.BuildNTPFingerprintFiles(),
        "ssh_lists": satoriSSH.BuildSSHFingerprintFiles(),
    }


def process_packet(buf, ts, deps, modules, log, history, limit_minutes):
    pkt, layer, tcp_packet, dhcp_packet, http_packet, udp_packet, ssl_packet, smb_packet, dns_packet, ntp_packet, _quic_packet, ssh_packet = packet_type(buf, deps)
    if pkt is None:
        return
    metadata = packet_metadata(pkt, layer)

    try:
        if tcp_packet and "tcp" in modules:
            timestamp, fingerprint = deps["satori_tcp"].tcpProcess(pkt, layer, ts, deps["pypacker_version"], *deps["tcp_lists"])
            write_fingerprint(log, timestamp, fingerprint, metadata, history, limit_minutes)
    except Exception:
        pass

    try:
        if ssl_packet and "ssl" in modules:
            timestamp, fingerprints = deps["satori_ssl"].sslProcess(pkt, layer, ts, *deps["ssl_lists"])
            for fingerprint in fingerprints:
                write_fingerprint(log, timestamp, fingerprint, metadata, history, limit_minutes)
    except Exception:
        pass

    try:
        if dhcp_packet and "dhcp" in modules:
            timestamp, fingerprint_options, fingerprint_option55, fingerprint_vendor = deps["satori_dhcp"].dhcpProcess(pkt, layer, ts, *deps["dhcp_lists"])
            write_fingerprint(log, timestamp, fingerprint_options, metadata, history, limit_minutes)
            write_fingerprint(log, timestamp, fingerprint_option55, metadata, history, limit_minutes)
            write_fingerprint(log, timestamp, fingerprint_vendor, metadata, history, limit_minutes)
    except Exception:
        pass

    try:
        if http_packet and "http" in modules:
            timestamp, fingerprint_hdr_ua, fingerprint_body_ua = deps["satori_http"].httpUserAgentProcess(pkt, layer, ts, *deps["http_user_agent_lists"])
            write_fingerprint(log, timestamp, fingerprint_hdr_ua, metadata, history, limit_minutes)
            write_fingerprint(log, timestamp, fingerprint_body_ua, metadata, history, limit_minutes)
            timestamp, fingerprint_hdr_server, fingerprint_body_server = deps["satori_http"].httpServerProcess(pkt, layer, ts, *deps["http_server_lists"])
            write_fingerprint(log, timestamp, fingerprint_hdr_server, metadata, history, limit_minutes)
            write_fingerprint(log, timestamp, fingerprint_body_server, metadata, history, limit_minutes)
    except Exception:
        pass

    try:
        if tcp_packet and smb_packet and "smb" in modules:
            timestamp, fingerprint_os, fingerprint_lanman = deps["satori_smb"].smbTCPProcess(pkt, layer, ts, *deps["smb_tcp_lists"])
            write_fingerprint(log, timestamp, fingerprint_os, metadata, history, limit_minutes)
            write_fingerprint(log, timestamp, fingerprint_lanman, metadata, history, limit_minutes)
    except Exception:
        pass

    try:
        if udp_packet and smb_packet and "smb" in modules:
            timestamp, fingerprint = deps["satori_smb"].smbUDPProcess(pkt, layer, ts, *deps["smb_udp_lists"])
            write_fingerprint(log, timestamp, fingerprint, metadata, history, limit_minutes)
    except Exception:
        pass

    try:
        if dns_packet and "dns" in modules:
            timestamp, fingerprint = deps["satori_dns"].dnsProcess(pkt, layer, ts, *deps["dns_lists"])
            write_fingerprint(log, timestamp, fingerprint, metadata, history, limit_minutes)
    except Exception:
        pass

    try:
        if ntp_packet and "ntp" in modules:
            timestamp, fingerprint = deps["satori_ntp"].ntpProcess(pkt, layer, ts, *deps["ntp_lists"])
            write_fingerprint(log, timestamp, fingerprint, metadata, history, limit_minutes)
    except Exception:
        pass

    try:
        if ssh_packet and "ssh" in modules:
            timestamp, fingerprint = deps["satori_ssh"].sshProcess(pkt, layer, ts, *deps["ssh_lists"])
            write_fingerprint(log, timestamp, fingerprint, metadata, history, limit_minutes)
    except Exception:
        pass


def main():
    args = parse_args()
    deps = load_satori()
    modules = parse_modules(args.modules)
    interface = args.interface.strip() or default_interface()
    bpf_filter = args.filter.strip()
    limit_minutes = args.limit
    log_path = Path(args.log)
    keep_running = {"value": True}

    def stop(signum, frame):
        keep_running["value"] = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        preader = deps["pcapy"].open_live(interface, 65536, False, 1)
        if bpf_filter:
            preader.setfilter(bpf_filter)
    except Exception as exc:
        print(exc, file=sys.stderr, flush=True)
        return 1

    log_path.parent.mkdir(parents=True, exist_ok=True)
    history = {}
    with log_path.open("a", encoding="utf-8") as log:
        while keep_running["value"]:
            try:
                header, buf = preader.next()
                process_packet(buf, header.getts()[0], deps, modules, log, history, limit_minutes)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
