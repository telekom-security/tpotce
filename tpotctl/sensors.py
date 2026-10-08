"""Sensors of a HIVE: who may send, where they are, when they were last seen.

Access is LS_WEB_USER in .env (base64 of name:htpasswd hash per sensor), from which
tpotinit builds data/nginx/conf/lswebpasswd for the ingest on 64294. The sensor
sends with TPOT_HIVE_USER (base64 of name:password, http_output.conf) and checks
the HIVE against its copy of data/nginx/cert/nginx.crt (data/hive.crt).

The registry data/sensors.json only holds what the HIVE cannot know otherwise
(address, SSH user, when, which version) and no secrets. It is built from
LS_WEB_USER where it is missing (installations before tpot, a lost file), so the
sensors of 24.04.1 come along. nginx hands the sensor user to Logstash, which puts
it into t-pot_sensor of every event (lsweb.conf, http_input.conf), that is how the
status ties events to sensors.

The deployment itself stays installer/install/deploy.yml, run over SSH.
"""

import base64
import ipaddress
import json
import os
import re
import secrets
import socket
import string
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List

from tpotctl import users
from tpotctl.bootstrap import REPO_DIR
from tpotctl.envfile import EnvError, EnvFile

SSH_PORT = 64295
ES_URL = "http://127.0.0.1:64298"
WORDS = (os.path.join("installer", "install", "a.txt"), os.path.join("installer", "install", "n.txt"))
# the same as tpotinit uses for the first certificate (entrypoint.sh)
CERT_SUBJECT = "/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd"


class SensorsError(Exception):
    """Something that is not done to the sensors."""


@dataclass
class Sensor:
    name: str
    host: str = ""             # address the HIVE reaches it on (SSH)
    ssh_user: str = ""
    ssh_port: int = SSH_PORT   # T-Pot moves sshd there, other ports i.e. behind NAT
    hive_address: str = ""     # address the sensor reaches the HIVE on
    added: str = ""
    version: str = ""
    hostname: str = ""         # t-pot_hostname of its events
    source: str = "migrated"   # deployed | migrated
    access: bool = True        # has an entry in LS_WEB_USER


@dataclass
class Seen:
    last: str = ""
    hostname: str = ""
    ip_ext: str = ""
    ip_int: str = ""


@dataclass
class Status:
    sensors: Dict[str, Seen] = field(default_factory=dict)
    unlinked: Dict[str, Seen] = field(default_factory=dict)   # hostnames of events without t-pot_sensor
    problem: str = ""


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------

def _words(path: str) -> List[str]:
    with open(path, encoding="utf-8") as handle:
        return [w for w in (line.strip() for line in handle) if re.match(r"^[a-z]+$", w)]


def new_name(existing, repo_dir: str = REPO_DIR) -> str:
    adjectives, nouns = (_words(os.path.join(repo_dir, path)) for path in WORDS)
    for _attempt in range(100):
        name = f"sensor-{secrets.choice(adjectives)}-{secrets.choice(nouns)}"
        if name not in existing:
            return name
    raise SensorsError("no free sensor name found")


def new_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hive_user(name: str, password: str) -> str:
    """TPOT_HIVE_USER of the sensor: base64 of name:password, the basic auth of http_output.conf."""
    return base64.b64encode(f"{name}:{password}".encode()).decode()


def _ip(address: str):
    """The IP address, None for anything else; a zone (fe80::1%eth0) is no address of another host."""
    if "%" in address:
        return None
    try:
        return ipaddress.ip_address(address)
    except ValueError:
        return None


def _host_name(address: str, extra: str = "") -> bool:
    """Labels of letters, digits, inner dashes (and extra) of 63 characters at most, 253 in all. Digits
    and dots only are a mistyped IPv4 address (999.1.1.1, 1.2.3, 123), no host name: as deploy.sh."""
    chars = "A-Za-z0-9" + extra
    label = f"[{chars}]([{chars}-]{{0,61}}[{chars}])?"
    return (1 <= len(address) <= 253 and bool(re.match(rf"^({label}\.)*{label}$", address))
            and not re.match(r"^[0-9.]+$", address))


def check_address(address: str) -> str:
    """The address of a SENSOR (ssh, Ansible): an IP or a host name, nothing that could break the commands;
    an alias of ~/.ssh/config may have an _ (sensor_1)."""
    address = (address or "").strip()
    if _ip(address) is not None or _host_name(address, "_"):
        return address
    raise SensorsError(f"'{address}' is not an IP address or a host name")


def check_hive_address(address: str) -> str:
    """The address the SENSOR reaches this HIVE on, its TPOT_HIVE_IP: an IPv4 address or a host name as
    tpotinit takes it there. No IPv6 address: Logstash sends to https://<address>:64294, without brackets
    that URL breaks (and tpotinit rejects ::ffff:192.0.2.1); a host name of the HIVE works for IPv6 too."""
    address = (address or "").strip()
    found = _ip(address)
    if found is not None and found.version == 4:
        return address
    if found is not None:
        raise SensorsError(f"'{address}' is an IPv6 address, a sensor cannot send to it: give the IPv4 address "
                           f"or a host name of this HIVE")
    if _host_name(address):
        return address
    raise SensorsError(f"'{address}' is not an IPv4 address or a host name of this HIVE")


def check_user(user: str) -> str:
    """A Linux user name: capitals too (useradd of Fedora, RHEL and openSUSE takes them)."""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$", user or ""):
        raise SensorsError(f"'{user}' is not a user name")
    return user


# ---------------------------------------------------------------------------
# registry and access
# ---------------------------------------------------------------------------

class Registry:

    def __init__(self, repo_dir: str = REPO_DIR, hasher: Callable = users.hash_line):
        self.repo_dir = repo_dir
        self.hasher = hasher
        try:
            self.env = EnvFile(os.path.join(repo_dir, ".env"))
        except OSError as err:
            raise SensorsError(f"cannot read the .env of T-Pot: {err}")
        if (self.env.get("TPOT_TYPE") or "HIVE") != "HIVE":
            raise SensorsError("sensors are managed on the HIVE")
        data = os.path.normpath(os.path.join(repo_dir, self.env.get("TPOT_DATA_PATH") or "./data"))
        self.path = os.path.join(data, "sensors.json")
        self.lswebpasswd = os.path.join(data, "nginx", "conf", "lswebpasswd")
        self.cert = os.path.join(data, "nginx", "cert", "nginx.crt")
        self.key = os.path.join(data, "nginx", "cert", "nginx.key")
        self.records: Dict[str, Sensor] = {}
        try:
            with open(self.path, encoding="utf-8") as handle:
                for item in (json.load(handle).get("sensors") or []):
                    sensor = Sensor(**{k: v for k, v in item.items() if k in Sensor.__dataclass_fields__})
                    self.records[sensor.name] = sensor
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as err:
            raise SensorsError(f"{self.path} cannot be read: {err}")
        self.migrated = self._merge()
        if self.migrated:
            self.save()

    def entries(self) -> List[users.WebUser]:
        return users.parse(self.env.get("LS_WEB_USER") or "")

    def _merge(self) -> List[str]:
        """Sensors of LS_WEB_USER the registry does not know yet (24.04.1, a lost file)."""
        names = {u.name for u in self.entries()}
        added = []
        for name in sorted(names - set(self.records)):
            self.records[name] = Sensor(name=name, source="migrated")
            added.append(name)
        for sensor in self.records.values():
            sensor.access = sensor.name in names
        return added

    def sensors(self) -> List[Sensor]:
        return sorted(self.records.values(), key=lambda s: s.name)

    def get(self, name: str) -> Sensor:
        if name not in self.records:
            raise SensorsError(f"there is no sensor {name}, see tpot sensors list")
        return self.records[name]

    def save(self) -> None:
        data = {"version": 1, "sensors": [asdict(s) for s in self.sensors()]}
        directory = os.path.dirname(self.path)
        try:
            os.makedirs(directory, exist_ok=True)
            handle, temp = tempfile.mkstemp(prefix=".sensors-", dir=directory)
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(data, out, indent=2)
                out.write("\n")
            os.chmod(temp, 0o660)
            os.replace(temp, self.path)
        except OSError as err:
            raise SensorsError(f"cannot write {self.path}: {err}")

    # -- access ----------------------------------------------------------------

    def _write_access(self, entries: List[str]) -> str:
        try:
            self.env.set("LS_WEB_USER", " ".join(entries))
            self.env.save()
        except EnvError as err:
            raise SensorsError(str(err))
        try:
            # in place: lswebpasswd is bind mounted on its own into nginx, as nginxpasswd
            with open(self.lswebpasswd, "w", encoding="utf-8") as handle:
                for user in users.parse(" ".join(entries)):
                    if user.line:
                        handle.write(user.line + "\n")
            return "this counts right away"
        except OSError:
            return "it counts from the next start of T-Pot on (tpot restart)"

    def grant(self, name: str, password: str) -> str:
        if any(u.name == name for u in self.entries()):
            raise SensorsError(f"{name} has access already")
        entry = users.encode(self.hasher(name, password, self.repo_dir))
        note = self._write_access([u.entry for u in self.entries()] + [entry])
        self.records.setdefault(name, Sensor(name=name)).access = True
        return note

    def revoke(self, name: str, forget: bool = True) -> str:
        if name not in self.records and not any(u.name == name for u in self.entries()):
            raise SensorsError(f"there is no sensor {name}")
        note = self._write_access([u.entry for u in self.entries() if u.name != name])
        if forget:
            self.records.pop(name, None)
        elif name in self.records:
            self.records[name].access = False
        self.save()
        return note

    def record(self, sensor: Sensor) -> None:
        self.records[sensor.name] = sensor
        self.save()

    def update(self, name: str, host: str = "", ssh_user: str = "", ssh_port="", hive_address: str = "") -> Sensor:
        """Where a sensor is (tpot sensors set, Edit on the Sensors page); empty values stay as they are."""
        sensor = self.get(name)
        if host:
            sensor.host = check_address(host)
        if ssh_user:
            sensor.ssh_user = check_user(ssh_user)
        if hive_address:
            sensor.hive_address = check_hive_address(hive_address)
        if ssh_port:
            sensor.ssh_port = check_port(ssh_port)
        self.record(sensor)
        return sensor

    def hive_addresses(self) -> List[str]:
        return sorted({s.hive_address for s in self.sensors() if s.hive_address})


# ---------------------------------------------------------------------------
# SSH and deployment
# ---------------------------------------------------------------------------

def check_port(port) -> int:
    if not str(port).isdigit() or not 1 <= int(port) <= 65535:
        raise SensorsError(f"'{port}' is not a port")
    return int(port)


def ssh_command(host: str, user: str, *remote: str, port: int = SSH_PORT) -> List[str]:
    return ["ssh", "-p", str(check_port(port)), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new", f"{check_user(user)}@{check_address(host)}"] + list(remote)


def check_ssh(host: str, user: str, run: Callable = subprocess.run, port: int = SSH_PORT) -> str:
    """ok, key (reachable, but no key login) or unreachable."""
    proc = run(ssh_command(host, user, "true", port=port), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
               universal_newlines=True)
    if proc.returncode == 0:
        return "ok"
    if "Permission denied" in (proc.stderr or "") or "Too many authentication failures" in (proc.stderr or ""):
        return "key"
    return "unreachable"


def copy_id_command(host: str, user: str, port: int = SSH_PORT) -> List[str]:
    return ["ssh-copy-id", "-p", str(check_port(port)), f"{check_user(user)}@{check_address(host)}"]


def keygen_command() -> List[str]:
    return ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", os.path.expanduser("~/.ssh/id_ed25519")]


def has_ssh_key() -> bool:
    return any(os.path.exists(os.path.expanduser(f"~/.ssh/{n}")) for n in ("id_ed25519", "id_rsa", "id_ecdsa"))


def deploy_command(host: str, user: str, become_pass: bool = True, repo_dir: str = REPO_DIR,
                   port: int = SSH_PORT) -> List[str]:
    command = ["ansible-playbook", os.path.join(repo_dir, "installer", "install", "deploy.yml"),
               "-i", f"{check_address(host)},", "-c", "ssh", "-u", check_user(user),
               "-e", f"ansible_port={check_port(port)}"]
    return command + (["--ask-become-pass"] if become_pass else [])


def deploy_env(hive_user_value: str, hive_address: str, repo_dir: str = REPO_DIR) -> Dict[str, str]:
    """deploy.yml reads both from the environment, as with deploy.sh."""
    env = dict(os.environ)
    env.update(myTPOT_HIVE_USER=hive_user_value, myTPOT_HIVE_IP=check_hive_address(hive_address),
               ANSIBLE_LOG_PATH=os.path.join(repo_dir, "data", "deploy_sensor.log"))
    return env


def distribute_command(inventory: str, become_pass: bool = True, repo_dir: str = REPO_DIR) -> List[str]:
    command = ["ansible-playbook", os.path.join(repo_dir, "installer", "install", "sensor_cert.yml"), "-i", inventory]
    return command + (["--ask-become-pass"] if become_pass else [])


def inventory_text(sensors: List[Sensor]) -> str:
    lines = ["[sensors]"]
    for sensor in sensors:
        lines.append(f"{sensor.name} ansible_host={check_address(sensor.host)} ansible_user={check_user(sensor.ssh_user)} "
                     f"ansible_port={check_port(sensor.ssh_port)}")
    return "\n".join(lines) + "\n"


def default_hive_address(host: str, run: Callable = subprocess.run) -> str:
    """The address of this HIVE on the way to the sensor (ip route get)."""
    try:
        target = socket.gethostbyname(check_address(host))
        proc = run(["ip", "route", "get", target], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                   universal_newlines=True)
        match = re.search(r"\bsrc (\S+)", proc.stdout or "")
        return match.group(1) if match else ""
    except (OSError, SensorsError):
        return ""


# ---------------------------------------------------------------------------
# certificate of the HIVE
# ---------------------------------------------------------------------------

def san_of(address: str) -> str:
    address = check_address(address)
    try:
        ipaddress.ip_address(address)
        return f"IP:{address}"
    except ValueError:
        return f"DNS:{address}"


def parse_sans(text: str) -> List[str]:
    """IP:1.2.3.4 / DNS:name entries of `openssl x509 -ext subjectAltName` or `-text`."""
    sans = []
    for kind, value in re.findall(r"\b(IP Address|DNS):([^\s,]+)", text):
        sans.append(f"{'IP' if kind.startswith('IP') else 'DNS'}:{value}")
    return sans


def cert_sans(path: str, run: Callable = subprocess.run) -> List[str]:
    proc = run(["openssl", "x509", "-in", path, "-noout", "-text"], stdout=subprocess.PIPE,
               stderr=subprocess.PIPE, universal_newlines=True)
    if proc.returncode != 0:
        raise SensorsError(f"cannot read the certificate {path}: {(proc.stderr or '').strip()}")
    return parse_sans(proc.stdout or "")


def covers(address: str, sans: List[str]) -> bool:
    return san_of(address) in sans


def renew_command(sans: List[str], key: str, cert: str) -> List[str]:
    return ["openssl", "req", "-nodes", "-x509", "-sha512", "-newkey", "rsa:8192", "-keyout", key, "-out", cert,
            "-days", "3650", "-subj", CERT_SUBJECT, "-addext", "subjectAltName = " + ",".join(sans)]


def renew_cert(registry: Registry, sans: List[str], run: Callable = subprocess.run) -> str:
    """New key and certificate with these SANs, the old ones are kept as .bak-<time>."""
    directory = os.path.dirname(registry.cert)
    key_tmp, cert_tmp = registry.key + ".new", registry.cert + ".new"
    proc = run(renew_command(sans, key_tmp, cert_tmp), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
               universal_newlines=True)
    if proc.returncode != 0:
        for path in (key_tmp, cert_tmp):
            if os.path.exists(path):
                os.unlink(path)
        raise SensorsError(f"openssl failed: {(proc.stderr or '').strip()}")
    stamp = time.strftime("%Y%m%d%H%M%S")
    try:
        for current, new in ((registry.key, key_tmp), (registry.cert, cert_tmp)):
            mode = os.stat(current).st_mode & 0o777 if os.path.exists(current) else 0o640
            if os.path.exists(current):
                os.replace(current, f"{current}.bak-{stamp}")
            os.chmod(new, mode)
            os.replace(new, current)
    except OSError as err:
        raise SensorsError(f"cannot replace the certificate in {directory}: {err}")
    return stamp


# ---------------------------------------------------------------------------
# status from Elasticsearch
# ---------------------------------------------------------------------------

def status_query(days: int) -> dict:
    latest = {"top_hits": {"size": 1, "sort": [{"@timestamp": "desc"}],
                           "_source": ["t-pot_hostname", "t-pot_ip_ext", "t-pot_ip_int", "@timestamp"]}}
    return {
        "size": 0,
        "query": {"range": {"@timestamp": {"gte": f"now-{int(days)}d"}}},
        "aggs": {
            "sensors": {"terms": {"field": "t-pot_sensor.keyword", "size": 1000}, "aggs": {"latest": latest}},
            "unlinked": {"filter": {"bool": {"must_not": {"exists": {"field": "t-pot_sensor"}}}},
                         "aggs": {"hosts": {"terms": {"field": "t-pot_hostname.keyword", "size": 1000},
                                            "aggs": {"latest": latest}}}},
        },
    }


def _seen(bucket: dict) -> Seen:
    hits = (((bucket.get("latest") or {}).get("hits") or {}).get("hits") or [{}])
    source = hits[0].get("_source", {}) if hits else {}
    return Seen(source.get("@timestamp", ""), source.get("t-pot_hostname", ""), source.get("t-pot_ip_ext", ""),
                source.get("t-pot_ip_int", ""))


def parse_status(answer: dict, own_hostname: str = "") -> Status:
    aggs = answer.get("aggregations") or {}
    status = Status()
    for bucket in (aggs.get("sensors") or {}).get("buckets", []):
        status.sensors[bucket["key"]] = _seen(bucket)
    for bucket in ((aggs.get("unlinked") or {}).get("hosts") or {}).get("buckets", []):
        if bucket["key"] != own_hostname:
            status.unlinked[bucket["key"]] = _seen(bucket)
    return status


def fetch_status(days: int = 7, url: str = ES_URL, opener: Callable = urllib.request.urlopen) -> Status:
    request = urllib.request.Request(f"{url}/logstash-*/_search", data=json.dumps(status_query(days)).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener(request, timeout=10) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as err:
        return Status(problem=f"Elasticsearch on {url} cannot be asked ({err})")
    return parse_status(answer, socket.gethostname())


def link_hostnames(registry: Registry, status: Status) -> None:
    """Keep the hostname events tell for a sensor in the registry."""
    changed = False
    for name, seen in status.sensors.items():
        sensor = registry.records.get(name)
        if sensor and seen.hostname and sensor.hostname != seen.hostname:
            sensor.hostname = seen.hostname
            changed = True
    if changed:
        registry.save()
