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
MIN_INSTALL_AGE = 90 * DAY   # the system was installed 90 days to Persona.max_install_days ago
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


def superseded_at(release):
    """The day the next patch level of the pool came out, 0 for the newest."""
    return _day(release, "superseded")


def is_medium(release):
    """The versions are those of an installation image (point release), so a
    system installed from it after the next patch level came out still has them."""
    return bool(release.extra.get("medium"))


def is_firmware(release):
    """A device firmware: no package updates, it stays on the device until the
    vendor's next firmware is flashed (extra["superseded"])."""
    return bool(release.extra.get("firmware"))


def ready_at(release):
    """From then on a system can run the release: installed, updated, its kernel built."""
    return max(kernel_time(release) or 0, first_install(release), updated_at(release))


STALE_AFTER = 90 * DAY  # a patch level counts as current for at most 90 days after its update


def stale_at(release):
    """From then on the mirror gave newer versions than the release's: the next
    openssh / kernel security update (extra["stale_from"]), the next patch level
    of the pool, at the latest 90 days after the update; 0 if unknown. A device
    firmware goes stale when the next firmware came out, the last one never (0)."""
    if is_firmware(release):
        return superseded_at(release)
    updated = updated_at(release)
    if not updated:
        return 0
    days = [d for d in (_day(release, "stale_from"), superseded_at(release)) if d]
    return min(days + [updated + STALE_AFTER])


def latest_install(release):
    """Installed later, a system would have got newer versions (0: no limit)."""
    return 0 if is_medium(release) else stale_at(release)


def log_window(ident):
    """(first, last) time of package log entries that pull the release's
    versions: from the installation (or the update to the release) until its
    versions went stale (stale_at); None when the system was installed from an
    image after that and never touched its packages."""
    r = ident.release
    first = max(ident.installed_at, updated_at(r))
    last = ident.now - 3600
    if stale_at(r):
        last = min(last, stale_at(r))
    if first > last:
        return None
    return first, last


def fits(release, hardware):
    """A release with kernels per board (Raspberry Pi) only fits those boards, one
    with a provider kernel (Ubuntu -aws) only that provider's hardware."""
    flavors = release.extra.get("flavors")
    providers = release.extra.get("providers")
    return (not flavors or hardware.board in flavors) and (not providers or hardware.provider in providers)


def provider_of(pattern):
    """"aws:ip-172-31-{n}-{n}" -> "aws"; "" for a pattern of any provider."""
    head, sep, _ = pattern.partition(":")
    return head if sep and head.isalpha() and head.islower() else ""


def host_patterns(persona, hardware):
    """The host name patterns for the provider of the hardware, without the prefix."""
    found = [p for p in persona.hostnames if provider_of(p) in ("", hardware.provider)]
    return [p.split(":", 1)[1] if provider_of(p) else p for p in found]


def hostname_regex(pattern, hardware):
    """The names a pattern can expand to on this hardware, as a regex."""
    tokens = {
        "n": r"(?:[1-9]\d?|1\d\d|2[0-4]\d|25[0-4])",
        "nn": r"\d{2}",
        "hex6": r"[0-9a-f]{6}",
        "HEX6": r"[0-9A-F]{6}",
        "word": "(?:" + "|".join(_WORDS) + ")",
        "cpus": str(hardware.cpus),
        "gib": str(max(1, round(hardware.mem_kb / 1024 / 1024))),
        "gpu": re.escape(_gpu_short(hardware.gpus[0])) if hardware.gpus else "(?!)",
    }
    parts = re.split(r"\{(n|nn|hex6|HEX6|word|cpus|gib|gpu)\}", pattern)
    return "".join(re.escape(part) if i % 2 == 0 else tokens[part] for i, part in enumerate(parts))


def specialize(release, hardware):
    """The release as it runs on the hardware: the kernel flavor of the board."""
    flavors = release.extra.get("flavors")
    if not flavors or "{flavor}" not in release.kernel_version:
        return release
    return dataclasses.replace(release, kernel_version=release.kernel_version.format(flavor=flavors[hardware.board]))


def _token(kind, rng):
    """A random (never valid) token in the format secret scanners look for (gitleaks,
    trufflehog): Hugging Face hf_ + 34 letters, an OpenAI project key sk-proj- + 74 +
    the T3BlbkFJ marker + 74 of [A-Za-z0-9_-] (164 characters, a word character last),
    an AWS access key id AKIA + 16 of [A-Z2-7]."""
    alnum = string.ascii_letters + string.digits
    if kind == "hf":
        return "hf_" + "".join(rng.choice(string.ascii_letters) for _ in range(34))
    if kind == "openai":
        def run():
            return "".join(rng.choice(alnum + "-_") for _ in range(73)) + rng.choice(alnum)
        return f"sk-proj-{run()}T3BlbkFJ{run()}"
    if kind == "aws":
        return "AKIA" + "".join(rng.choice(string.ascii_uppercase + "234567") for _ in range(16))
    raise ValueError(f"unknown token kind {kind}")


def expand_hostname(pattern, rng, hardware=None):
    subs = {
        "n": lambda: str(rng.randint(1, 254)),
        "nn": lambda: f"{rng.randint(1, 99):02d}",
        "hex6": lambda: f"{rng.getrandbits(24):06x}",
        # QNAP names a NAS after the last three bytes of its MAC: NAS5F3A2B
        "HEX6": lambda: f"{rng.getrandbits(24):06X}",
        "word": lambda: rng.choice(_WORDS),
        # a plan name (ubuntu-s-2vcpu-4gb-...) must name the hardware it runs on
        "cpus": lambda: str(hardware.cpus),
        "gib": lambda: str(max(1, round(hardware.mem_kb / 1024 / 1024))),
        # "NVIDIA A100-SXM4-80GB" -> a100, "NVIDIA GeForce RTX 4090" -> rtx4090
        "gpu": lambda: _gpu_short(hardware.gpus[0]),
    }
    return re.sub(r"\{(n|nn|hex6|HEX6|word|cpus|gib|gpu)\}", lambda m: subs[m.group(1)](), pattern)


def _gpu_short(name):
    model = name.replace("NVIDIA ", "", 1).replace("GeForce ", "", 1)
    return model.split("-", 1)[0].replace(" ", "").lower()


def _ready(release, now, max_age):
    """The release existed long enough before now to have been installed and booted,
    and an installation 90 days to max_age back can have it."""
    if ready_at(release) > now - 2 * DAY or first_install(release) > now - MIN_INSTALL_AGE:
        return False
    latest = latest_install(release)
    return not latest or max(now - max_age, first_install(release)) <= min(latest, now - MIN_INSTALL_AGE)


def draw(persona, seed, now=None):
    rng = random.Random(seed)
    now = int(time.time()) if now is None else int(now)
    hardware = rng.choice(persona.hardware)
    fitting = [r for r in persona.releases if fits(r, hardware)] or list(persona.releases)
    max_age = getattr(persona, "max_install_days", 900) * DAY
    ready = [r for r in fitting if _ready(r, now, max_age)]
    # a clock far outside the pool has no consistent release: any one, installed in range, no logs
    release = specialize(rng.choice(ready or fitting), hardware)

    # booted after its last update (and so after its kernel was built), up to 120 / 400 days ago
    max_up = (400 if persona.kind == "device" else 120) * DAY
    latest_boot = now - DAY
    earliest_boot = min(max(now - max_up, ready_at(release) + DAY), latest_boot)
    booted_at = rng.randint(earliest_boot, latest_boot)
    # installed 90 to 900 days ago, not after the boot, not before the release existed,
    # and not after the next patch level came out (unless from the image of this one)
    last_install = min(booted_at, now - MIN_INSTALL_AGE)
    if ready and latest_install(release):
        last_install = min(last_install, latest_install(release))
    earliest_install = min(max(now - max_age, first_install(release)), last_install)
    installed_at = rng.randint(earliest_install, last_install)
    patterns = host_patterns(persona, hardware) or list(persona.hostnames)
    return Identity(
        persona_id=persona.id,
        seed=seed,
        now=now,
        hostname=expand_hostname(rng.choice(patterns), rng, hardware),
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
