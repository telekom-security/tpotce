"""One random identity per container start: release, hardware, host name,
uptime, installation date and honeytokens, all drawn from one seed and all
relative to `now`, so the dates of a persona agree with each other."""
import calendar
import dataclasses
import random
import re
import string
import time
from dataclasses import asdict, dataclass

DAY = 86400
MIN_INSTALL_AGE = 90 * DAY   # the system was installed 90 to 900 days ago
MAX_INSTALL_AGE = 900 * DAY
_WORDS = ("app", "web", "db", "api", "node", "srv", "prod", "stage", "build", "data", "edge", "mail")
_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                       "Nov", "Dec"), 1)}
# "Sat Oct 11 09:28:41 UTC 2025", "Mon Jan 28 08:54:02 2019" (date(1) of the kernel build)
_UTS_DATE = re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (" + "|".join(_MONTHS) + r") +(\d{1,2}) "
                       r"(\d{2}):(\d{2}):(\d{2})(?: [A-Z]{2,5})? (\d{4})\b")
# "Debian 6.1.76-1 (2024-02-01)"
_ISO_DATE = re.compile(r"\((\d{4})-(\d{2})-(\d{2})\)")


@dataclass(frozen=True)
class Identity:
    persona_id: str
    seed: int
    now: int
    hostname: str
    release: object
    hardware: object
    boot_offset: int
    installed_at: int
    pid_seed: int
    tokens: dict

    @property
    def booted_at(self):
        return self.now - self.boot_offset


def kernel_time(release):
    """When the kernel of the release was built, from its uname -v (the time
    zone is ignored, the draw keeps a day between build and boot)."""
    found = list(_UTS_DATE.finditer(release.kernel_build))
    if found:
        mon, day, hh, mm, ss, year = found[-1].groups()
        return calendar.timegm((int(year), _MONTHS[mon], int(day), int(hh), int(mm), int(ss)))
    found = _ISO_DATE.findall(release.kernel_build)
    if found:
        year, mon, day = found[-1]
        return calendar.timegm((int(year), int(mon), int(day), 0, 0, 0))
    return None


def _day(release, key):
    text = release.extra.get(key)
    return calendar.timegm(time.strptime(text, "%Y-%m-%d")) if text else 0


def first_install(release):
    """The earliest plausible installation (OS release or image date), 0 if unknown."""
    return _day(release, "first_install")


def updated_at(release):
    """The day the system reached this patch level (its last update), 0 if unknown."""
    return _day(release, "updated")


def ready_at(release):
    """From then on a system can run the release: installed, updated, its kernel built."""
    return max(kernel_time(release) or 0, first_install(release), updated_at(release))


def fits(release, hardware):
    """A release with kernels per board (Raspberry Pi) only fits those boards."""
    flavors = release.extra.get("flavors")
    return not flavors or hardware.board in flavors


def specialize(release, hardware):
    """The release as it runs on the hardware: the kernel flavor of the board."""
    flavors = release.extra.get("flavors")
    if not flavors or "{flavor}" not in release.kernel_version:
        return release
    return dataclasses.replace(release, kernel_version=release.kernel_version.format(flavor=flavors[hardware.board]))


def _token(kind, rng):
    alnum = string.ascii_letters + string.digits
    if kind == "hf":
        return "hf_" + "".join(rng.choice(alnum) for _ in range(34))
    if kind == "openai":
        return "sk-proj-" + "".join(rng.choice(alnum + "-_") for _ in range(56))
    if kind == "aws":
        return "AKIA" + "".join(rng.choice(string.ascii_uppercase + "234567") for _ in range(16))
    raise ValueError(f"unknown token kind {kind}")


def expand_hostname(pattern, rng, hardware=None):
    subs = {
        "n": lambda: str(rng.randint(1, 254)),
        "nn": lambda: f"{rng.randint(1, 99):02d}",
        "hex6": lambda: f"{rng.getrandbits(24):06x}",
        "word": lambda: rng.choice(_WORDS),
        # a plan name (ubuntu-s-2vcpu-4gb-...) must name the hardware it runs on
        "cpus": lambda: str(hardware.cpus),
        "gib": lambda: str(max(1, round(hardware.mem_kb / 1024 / 1024))),
        # "NVIDIA A100-SXM4-80GB" -> a100, "NVIDIA GeForce RTX 4090" -> rtx4090
        "gpu": lambda: _gpu_short(hardware.gpus[0]),
    }
    return re.sub(r"\{(n|nn|hex6|word|cpus|gib|gpu)\}", lambda m: subs[m.group(1)](), pattern)


def _gpu_short(name):
    model = name.replace("NVIDIA ", "", 1).replace("GeForce ", "", 1)
    return model.split("-", 1)[0].replace(" ", "").lower()


def _ready(release, now):
    """The release existed long enough before now to have been installed and booted."""
    return ready_at(release) <= now - 2 * DAY and first_install(release) <= now - MIN_INSTALL_AGE


def draw(persona, seed, now=None):
    rng = random.Random(seed)
    now = int(time.time()) if now is None else int(now)
    hardware = rng.choice(persona.hardware)
    fitting = [r for r in persona.releases if fits(r, hardware)] or list(persona.releases)
    release = specialize(rng.choice([r for r in fitting if _ready(r, now)] or fitting), hardware)

    # booted after its last update (and so after its kernel was built), up to 120 / 400 days ago
    max_up = (400 if persona.kind == "device" else 120) * DAY
    latest_boot = now - DAY
    earliest_boot = min(max(now - max_up, ready_at(release) + DAY), latest_boot)
    booted_at = rng.randint(earliest_boot, latest_boot)
    # installed 90 to 900 days ago, not after the boot, not before the release existed
    latest_install = min(booted_at, now - MIN_INSTALL_AGE)
    earliest_install = min(max(now - MAX_INSTALL_AGE, first_install(release)), latest_install)
    installed_at = rng.randint(earliest_install, latest_install)
    return Identity(
        persona_id=persona.id,
        seed=seed,
        now=now,
        hostname=expand_hostname(rng.choice(persona.hostnames), rng, hardware),
        release=release,
        hardware=hardware,
        boot_offset=now - booted_at,
        installed_at=installed_at,
        pid_seed=rng.getrandbits(32),
        tokens={name: _token(kind, rng) for name, kind in persona.tokens},
    )


def to_json(ident):
    data = asdict(ident)
    data["persona"] = data.pop("persona_id")
    data["booted_at"] = ident.booted_at
    return data
