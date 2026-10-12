#!/usr/bin/env python3
"""Start Cowrie as one persona with its identity.

COWRIE_PERSONA (empty: all, else persona IDs) picks the persona. With
COWRIE_PERSONA_ROTATION=0 (the default) persona and identity are new on every
start; with N days a start within N days of the draw keeps both (the same
release, hardware, host name, tokens and boot, so the uptime goes on), as long
as the persona is still selected. etc/persona-state.json in the keys volume
(0640) holds what is needed for that; it is written on every start, so a
change from 0 to N keeps the persona that runs. A state that is missing,
broken, of another persona or of another image is not used: a new persona is
drawn and the start goes on, with one log line why.

Everything is rendered into the tmpfs /tmp/cowrie, the SSH host keys of the
persona live in etc/<persona>/ of the keys volume (Cowrie creates the missing
ones of every configured type on its first start). Keys of an earlier T-Pot
directly in etc/ are left alone."""
import json
import os
import random
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

LIB = Path("/usr/local/lib/tpot-cowrie")
if LIB.is_dir() and str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from tpot_personas import PersonaError, catalog, tree  # noqa: E402
from tpot_personas.identity import DAY, draw, restore, to_state  # noqa: E402
from tpot_personas.render import render  # noqa: E402
from tpot_personas.select import parse_selection  # noqa: E402
from tpot_personas.validate import MIN_PICKLE_SIZE, validate  # noqa: E402

COWRIE_ROOT = Path("/home/cowrie/cowrie")
TMP_ROOT = Path("/tmp/cowrie")
TWISTD = "/usr/bin/twistd"
UMASK = 0o027
STATE_FILE = "persona-state.json"
STATE_MODE = 0o640
STATE_MAX_BYTES = 64 * 1024


def log(text):
    print(text, flush=True)


def rotation_days(environ):
    """COWRIE_PERSONA_ROTATION as tpotinit checks it: empty is 0, else 0 to 365 days."""
    value = environ.get("COWRIE_PERSONA_ROTATION") or "0"
    if not re.fullmatch(r"[0-9]+", value) or int(value) > 365:
        raise PersonaError(f'COWRIE_PERSONA_ROTATION "{value}" is no number of days from 0 to 365')
    return int(value)


def load_state(path):
    """The saved state, None when there is none; ValueError when it cannot be read."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(STATE_MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"unreadable: {exc.strerror or exc}") from None
    if len(data) > STATE_MAX_BYTES:
        raise ValueError("too large")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("no JSON") from None


def kept_identity(state_path, selection, days, now):
    """The identity of the saved state when it is kept for this start, else None
    (after one log line when a state was there)."""
    if not days:
        return None
    try:
        state = load_state(state_path)
        if state is None:
            return None
        persona_id = state.get("persona") if isinstance(state, dict) else None
        if not isinstance(persona_id, str) or persona_id not in catalog.ids():
            raise ValueError(f"unknown persona {persona_id!r}" if persona_id is not None else "no persona")
        if persona_id not in selection:
            log(f"Cowrie persona {persona_id} is no longer in COWRIE_PERSONA, drawing a new persona")
            return None
        chosen = state.get("chosen_at")
        if type(chosen) is int and chosen <= now and now - chosen >= days * DAY:
            log(f"Cowrie persona {persona_id} was kept for {days} days, drawing a new persona")
            return None
        return restore(catalog.by_id(persona_id), state, now)
    except Exception as exc:  # noqa: BLE001 - a state of any shape never stops the start
        log(f"Cowrie persona state {state_path.name} not used ({exc}), drawing a new persona")
        return None


def save_state(path, data):
    """Written in full next to the state, then renamed over it; a failure only logs."""
    staged = path.with_name(path.name + ".new")
    try:
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, STATE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), STATE_MODE)
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    except OSError as exc:
        log(f"Could not save the Cowrie persona state {path.name} ({exc.strerror or exc}), "
            "the next start draws a new persona")
        try:
            staged.unlink()
        except OSError:
            pass


def _activate(persona, ident, cowrie_root, tmp_root, min_pickle):
    runtime = tmp_root / "runtime"
    staging = tmp_root / "runtime.new"
    keys_dir = cowrie_root / "etc" / persona.id
    base = tree.load_pickle(cowrie_root / "personas" / "base.pickle")
    try:
        render(persona, ident, base, staging, runtime_dir=runtime, keys_dir=keys_dir, cowrie_root=cowrie_root)
        validate(persona, ident, staging, runtime, keys_dir, min_pickle=min_pickle)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if runtime.exists():
        shutil.rmtree(runtime)
    staging.rename(runtime)
    (tmp_root / "data").mkdir(exist_ok=True)
    # Cowrie writes missing host keys here, it does not create the directory
    keys_dir.mkdir(mode=0o770, parents=True, exist_ok=True)
    shutil.copy2(runtime / "identity.json", tmp_root / "identity.json")
    (tmp_root / "persona").write_text(persona.id + "\n", encoding="utf-8")


def prepare(environ, cowrie_root, tmp_root, rng, min_pickle=MIN_PICKLE_SIZE, now=None):
    """Select (or keep), render and validate a persona into tmp_root/runtime; returns
    its ID. rng only selects the persona, the identity seed comes from secrets."""
    cowrie_root, tmp_root = Path(cowrie_root), Path(tmp_root)
    selection = parse_selection(environ.get("COWRIE_PERSONA"), catalog.ids())
    days = rotation_days(environ)
    now = int(time.time()) if now is None else int(now)
    state_path = cowrie_root / "etc" / STATE_FILE
    ident = kept_identity(state_path, selection, days, now)
    if ident is not None:
        persona = catalog.by_id(ident.persona_id)
        try:
            _activate(persona, ident, cowrie_root, tmp_root, min_pickle)
        except Exception as exc:  # noqa: BLE001 - the kept identity failed, a fresh one may not
            log(f"Cowrie persona {persona.id} of {state_path.name} not used ({type(exc).__name__}: {exc}), "
                "drawing a new persona")
            ident = None
        else:
            drawn = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ident.chosen_at))
            log(f"Keeping Cowrie persona {persona.id} drawn {drawn} (COWRIE_PERSONA_ROTATION={days})")
    if ident is None:
        persona = catalog.by_id(rng.choice(selection))
        ident = draw(persona, secrets.randbits(64), now)
        _activate(persona, ident, cowrie_root, tmp_root, min_pickle)
    save_state(state_path, to_state(persona, ident))
    return persona.id


def main():
    try:
        persona_id = prepare(os.environ, COWRIE_ROOT, TMP_ROOT, random.SystemRandom())
    except (PersonaError, OSError) as exc:
        print(f"Could not activate Cowrie persona: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - a corrupt base.pickle or a builder bug: one line, no traceback
        print(f"Could not activate Cowrie persona: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Starting Cowrie persona: {persona_id}", flush=True)
    os.environ.setdefault("PYTHONPATH", f"{COWRIE_ROOT}:{COWRIE_ROOT / 'src'}")
    os.chdir(TMP_ROOT / "runtime")
    # 0640 / 0750: private host keys not world readable; Logstash and ewsposter read the logs as 2000:2000.
    # twistd applies --umask only after the plugin opened log/cowrie.json, so the process gets it first.
    os.umask(UMASK)
    os.execv(TWISTD, [TWISTD, "-n", f"--umask={UMASK:04o}", "--pidfile=", "cowrie"])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
