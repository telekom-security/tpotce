"""The .env rules of docker/tpotinit/dist/etc/env.schema.yml, checked the way tpotinit does.

docker/tpotinit/dist/bin/env_validate.sh is the other implementation of the same
rules; docker/tpotinit/tests/env_cases.yml holds the cases both have to agree on.
"""

import base64
import binascii
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

import yaml

from tpotctl.bootstrap import REPO_DIR

SCHEMA_PATH = os.path.join(REPO_DIR, "docker", "tpotinit", "dist", "etc", "env.schema.yml")
TYPES = {"text", "path", "enum", "int", "number", "regex", "host", "url", "timezone", "interface",
         "htpasswd_list", "basic_cred"}
SECTIONS = [("base", "T-Pot"), ("honeypots", "Honeypots and tools"), ("system", "Advanced")]

_SAFE_BAD = re.compile(r"[^a-zA-Z0-9_/.:-]")
_BASE64 = re.compile(r"^([A-Za-z0-9+/]{4})*([A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")
_HTPASSWD = re.compile(r"^([^:\s]+):(\$apr1\$|\$2[aby]\$|\$5\$|\$6\$|\{SHA\})")
_BASIC = re.compile(r"^([^:\s]+):.+")
_IPV4 = re.compile(r"^(([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])\.){3}"
                   r"([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])$")
_IPV6 = re.compile(r"^[0-9A-Fa-f]{0,4}(:[0-9A-Fa-f]{0,4}){2,7}$")
_DOMAIN = re.compile(r"^(([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])\.)*"
                     r"([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9])$")
_URL = re.compile(r"^https?://\S+$")
_IFACE = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")


@dataclass
class Rule:
    key: str
    type: str = "text"
    section: str = "base"
    title: str = ""
    help: str = ""
    values: List[str] = field(default_factory=list)
    optional: bool = False
    min: Optional[float] = None
    max: Optional[float] = None
    pattern: str = ""
    message: str = ""
    required: bool = False
    safe: bool = False
    scope: str = ""
    services: List[str] = field(default_factory=list)
    default: str = ""
    on_invalid: str = ""
    required_when: Dict = field(default_factory=dict)
    required_message: str = ""
    warn_empty: str = ""
    warn_when: Dict = field(default_factory=dict)
    warn_message: str = ""
    secret: bool = False
    editable: bool = True


@dataclass
class Problem:
    level: str      # error | warning
    key: str
    text: str


def load_schema(path: str = SCHEMA_PATH) -> Dict[str, Rule]:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    rules = {}
    for key, spec in (data.get("keys") or {}).items():
        spec = dict(spec or {})
        for name in ("values", "services"):
            spec[name] = [str(v) for v in spec.get(name) or []]
        for name in ("default",):
            spec[name] = "" if spec.get(name) is None else str(spec[name])
        rules[key] = Rule(key=key, **spec)
    return rules


def compose_services(path: str) -> Set[str]:
    """What fuCOMPOSE_HAS of tpotinit sees: every name indented by two spaces."""
    try:
        with open(path, encoding="utf-8") as handle:
            return set(re.findall(r"^  ([A-Za-z0-9_.-]+):", handle.read(), re.M))
    except OSError:
        return set()


def applies(rule: Rule, values: Dict[str, str], services: Iterable[str]) -> bool:
    if rule.scope and rule.scope != values.get("TPOT_TYPE", ""):
        return False
    if rule.services and not set(rule.services) & set(services):
        return False
    return True


def _fill(text: str, value: str = "", when: str = "") -> str:
    return text.replace("{value}", value).replace("{when}", when)


def _decoded(entry: str) -> str:
    try:
        return base64.b64decode(entry).decode("utf-8", "replace").replace("\n", "")
    except (binascii.Error, ValueError):
        return ""


def _credentials(rule: Rule, value: str, out: List[Problem]) -> None:
    for entry in value.split():
        if not _BASE64.match(entry):
            out.append(Problem("error", rule.key, "an entry is not a valid base64 string."))
            continue
        line = _decoded(entry)
        if rule.type == "htpasswd_list" and _HTPASSWD.match(line):
            continue
        if rule.type == "basic_cred" and _BASIC.match(line):
            continue
        if rule.type == "htpasswd_list":
            out.append(Problem("error", rule.key, "an entry is not a htpasswd user (name:hash), please create it "
                                                  "with genuser.sh."))
        else:
            out.append(Problem("error", rule.key, "an entry is not base64 of name:password."))


def _timezone_known(value: str) -> bool:
    if ".." in value:
        return False
    if os.path.isdir("/usr/share/zoneinfo"):
        return os.path.isfile(os.path.join("/usr/share/zoneinfo", value))
    try:
        import zoneinfo
        return value in zoneinfo.available_timezones()
    except ImportError:
        return True


def _interfaces() -> Optional[List[str]]:
    try:
        return sorted(os.listdir("/sys/class/net"))
    except OSError:
        return None


def check(rule: Rule, values: Dict[str, str], defaults: Dict[str, str], check_host: bool = True) -> List[Problem]:
    """The problems of one key, as fuENV_RULE of env_validate.sh finds them."""
    out: List[Problem] = []
    value = values.get(rule.key, "")
    if rule.required and not value:
        return [Problem("error", rule.key, "is not set or empty.")]
    if rule.required_when and not value:
        when = values.get(rule.required_when["key"], "") or defaults.get(rule.required_when["key"], "")
        if when in [str(v) for v in rule.required_when.get("values", [])]:
            return [Problem("error", rule.key, _fill(rule.required_message, when=when))]
    if rule.warn_when and not value:
        when = values.get(rule.warn_when["key"], "") or defaults.get(rule.warn_when["key"], "")
        if when in [str(v) for v in rule.warn_when.get("values", [])]:
            out.append(Problem("warning", rule.key, _fill(rule.warn_message, when=when)))
    if not value:
        if rule.warn_empty:
            out.append(Problem("warning", rule.key, rule.warn_empty))
        if rule.type == "enum" and not rule.optional:
            out.append(Problem("error", rule.key, f'invalid value "", allowed: {" ".join(rule.values)}.'))
        if rule.on_invalid == "default":
            out.append(Problem("warning", rule.key, rule.message))
        return out
    if rule.safe and _SAFE_BAD.search(value):
        return out + [Problem("error", rule.key, "contains unsafe characters.")]
    kind = rule.type
    if kind == "enum" and value not in rule.values:
        out.append(Problem("error", rule.key, f'invalid value "{value}", allowed: {" ".join(rule.values)}.'))
    elif kind == "int":
        if not re.match(r"^[0-9]+$", value) or not rule.min <= int(value) <= rule.max:
            level = "warning" if rule.on_invalid == "default" else "error"
            out.append(Problem(level, rule.key, _fill(rule.message, value)))
    elif kind == "number":
        if not re.match(r"^[0-9]+(\.[0-9]+)?$", value) or not rule.min <= float(value) <= rule.max:
            out.append(Problem("error", rule.key, _fill(rule.message, value)))
    elif kind == "regex" and not re.search(rule.pattern, value):
        out.append(Problem("error", rule.key, _fill(rule.message, value)))
    elif kind == "host" and not (_IPV4.match(value) or _IPV6.match(value) or _DOMAIN.match(value)):
        out.append(Problem("error", rule.key, f'"{value}" is not a valid IPv4 / IPv6 address or domain name.'))
    elif kind == "url" and not _URL.match(value):
        out.append(Problem("error", rule.key, f'"{value}" is not an http(s) URL.'))
    elif kind == "timezone" and not _timezone_known(value):
        out.append(Problem("error", rule.key, f'"{value}" is not a known time zone (i.e. UTC, Europe/Berlin).'))
    elif kind == "interface":
        if not _IFACE.match(value):
            out.append(Problem("error", rule.key, "is not a valid interface name."))
        elif check_host and _interfaces() is not None and value not in _interfaces():
            available = " ".join(i for i in _interfaces() if not re.match(r"^(lo|docker|br-|veth)", i))
            out.append(Problem("error", rule.key,
                               f'interface "{value}" does not exist on this host, available: {available}'))
    elif kind in ("htpasswd_list", "basic_cred"):
        _credentials(rule, value, out)
    return out


def validate(values: Dict[str, str], services: Iterable[str], schema: Optional[Dict[str, Rule]] = None,
             check_host: bool = True) -> List[Problem]:
    """All problems of a .env, in the order of the schema."""
    schema = load_schema() if schema is None else schema
    services = set(services)
    defaults = {key: rule.default for key, rule in schema.items()}
    problems: List[Problem] = []
    for rule in schema.values():
        if applies(rule, values, services):
            problems.extend(check(rule, values, defaults, check_host))
    return problems


def mask(value: str) -> str:
    if not value:
        return ""
    entries = value.split()
    if len(entries) > 1:
        return f"•••••• ({len(entries)} entries)"
    return "••••••"
