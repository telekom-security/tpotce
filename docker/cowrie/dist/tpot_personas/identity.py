"""One random identity per container start: release, hardware, host name,
uptime, installation date and honeytokens, all drawn from one seed and all
relative to `now`, so the dates of a persona agree with each other."""
import random
import re
import string
import time
from dataclasses import asdict, dataclass

DAY = 86400
_WORDS = ("app", "web", "db", "api", "node", "srv", "prod", "stage", "build", "data", "edge", "mail")


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


def _token(kind, rng):
    alnum = string.ascii_letters + string.digits
    if kind == "hf":
        return "hf_" + "".join(rng.choice(alnum) for _ in range(34))
    if kind == "openai":
        return "sk-proj-" + "".join(rng.choice(alnum + "-_") for _ in range(56))
    if kind == "aws":
        return "AKIA" + "".join(rng.choice(string.ascii_uppercase + "234567") for _ in range(16))
    raise ValueError(f"unknown token kind {kind}")


def expand_hostname(pattern, rng):
    subs = {
        "n": lambda: str(rng.randint(1, 254)),
        "nn": lambda: f"{rng.randint(1, 99):02d}",
        "hex6": lambda: f"{rng.getrandbits(24):06x}",
        "word": lambda: rng.choice(_WORDS),
    }
    return re.sub(r"\{(n|nn|hex6|word)\}", lambda m: subs[m.group(1)](), pattern)


def draw(persona, seed, now=None):
    rng = random.Random(seed)
    now = int(time.time()) if now is None else int(now)
    max_up = 400 if persona.kind == "device" else 120
    boot_offset = rng.randint(DAY, max_up * DAY)
    booted_at = now - boot_offset
    installed_at = min(booted_at - rng.randint(DAY, 800 * DAY), now - 90 * DAY)
    return Identity(
        persona_id=persona.id,
        seed=seed,
        now=now,
        hostname=expand_hostname(rng.choice(persona.hostnames), rng),
        release=rng.choice(persona.releases),
        hardware=rng.choice(persona.hardware),
        boot_offset=boot_offset,
        installed_at=installed_at,
        pid_seed=rng.getrandbits(32),
        tokens={name: _token(kind, rng) for name, kind in persona.tokens},
    )


def to_json(ident):
    data = asdict(ident)
    data["persona"] = data.pop("persona_id")
    data["booted_at"] = ident.booted_at
    return data
