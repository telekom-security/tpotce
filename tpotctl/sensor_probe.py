"""What tpot sensors hive-address needs to know of a SENSOR before it sets a new TPOT_HIVE_IP there.

tpotctl.sensors.probe_sensor sends this file over SSH to `python3 -` and reads the one JSON object it
prints. Standard library only (an old SENSOR has Python 3.9); it only reads and creates nothing."""

import fcntl
import json
import os
import re
from typing import List, Optional

# the guard of a migration (tpotctl.migration_runtime), update.sh refuses while it is there
HOLD = "/etc/systemd/system/tpot.service.d/90-tpot-migration.conf"


def _setting(key: str):
    """A line of the .env as docker compose reads it (it hands the file to tpotinit and Fluent Bit of a
    sensor): indent and export allowed, blanks around the = or :, a # at the start a comment."""
    return re.compile(r"^[ \t]*(?:export[ \t]+)?" + key + r"[ \t]*[=:]", re.M)


# the line installer/install/sensor_hive.yml replaces
HIVE_IP = _setting("TPOT_HIVE_IP")
COMPOSE = _setting("TPOT_DOCKER_COMPOSE")
ROLE = _setting("TPOT_TYPE")
OS_ID = re.compile(r"^ID=", re.M)
# the rule of tpotinit's fuCOMPOSE_HAS and sensor_cert.yml: every name indented by two spaces
SERVICE = re.compile(r"^  ([A-Za-z0-9_.-]+):", re.M)


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _value(text: str) -> str:
    """The value after the = or : as docker compose takes it: quoted up to its closing quote (in double
    quotes \\" and \\\\ unescaped), else up to a " #" comment, trimmed. A value of more lines and $VARs
    are not resolved, an address has neither."""
    text = text.lstrip(" \t\v\f\r")
    if text[:1] in ("'", '"'):
        quote, chars, index = text[0], [], 1
        while index < len(text):
            char = text[index]
            if char == quote:
                return "".join(chars)
            if quote == '"' and char == "\\" and text[index + 1:index + 2] in ('"', "\\"):
                index += 1
                char = text[index]
            chars.append(char)
            index += 1
        return text.strip()
    return text.split(" #", 1)[0].rstrip()


def _last(pattern, text: Optional[str]) -> Optional[str]:
    """The value of the last line the pattern starts (the last line wins, as for compose); None without one."""
    value = None
    for match in pattern.finditer(text or ""):
        end = text.find("\n", match.end())
        value = text[match.end():] if end < 0 else text[match.end():end]
    return None if value is None else _value(value)


def _update_locked(home: str) -> bool:
    """update.sh holds ~/.local/share/tpotce/update.lock while it runs. A shared lock for an instant tells
    without waiting; an update that starts in that instant stops (flock -n) and can simply be run again.
    A lock file update.sh would refuse itself (a link, unreadable) holds no update."""
    path = os.path.join(home, ".local", "share", "tpotce", "update.lock")
    if not os.path.lexists(path):
        return False
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return False
    try:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    finally:
        os.close(handle)


def _services(repo: str, settings: Optional[str]) -> List[str]:
    compose = _last(COMPOSE, settings) or "./docker-compose.yml"
    text = _read(compose if compose.startswith("/") else os.path.join(repo, compose))
    return SERVICE.findall(text or "")


def probe(home: str, os_release: str = "/etc/os-release", hold: str = HOLD) -> dict:
    repo = os.path.join(home, "tpotce")
    settings = _read(os.path.join(repo, ".env"))
    return {
        "version": (_read(os.path.join(repo, "version")) or "").strip(),
        "os_id": _last(OS_ID, _read(os_release)) or "",
        "hold": os.path.lexists(hold),
        "update_locked": _update_locked(home),
        "hive_ip": _last(HIVE_IP, settings),
        "services": _services(repo, settings),
        "role": _last(ROLE, settings),
    }


if __name__ == "__main__":
    print(json.dumps(probe(os.path.expanduser("~"))))
