#!/usr/bin/env python3
"""Start Cowrie as one persona with a fresh identity.

COWRIE_PERSONA (empty: all, else persona IDs) picks the persona, the identity is new
on every start. Everything is rendered into the tmpfs /tmp/cowrie, the SSH host
keys of the persona live in etc/<persona>/ of the keys volume (Cowrie creates
the missing ones of every configured type on its first start). Keys of an
earlier T-Pot directly in etc/ are left alone."""
import os
import random
import secrets
import shutil
import sys
from pathlib import Path

LIB = Path("/usr/local/lib/tpot-cowrie")
if LIB.is_dir() and str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from tpot_personas import PersonaError, catalog, tree  # noqa: E402
from tpot_personas.identity import draw  # noqa: E402
from tpot_personas.render import render  # noqa: E402
from tpot_personas.select import choose  # noqa: E402
from tpot_personas.validate import MIN_PICKLE_SIZE, validate  # noqa: E402

COWRIE_ROOT = Path("/home/cowrie/cowrie")
TMP_ROOT = Path("/tmp/cowrie")
TWISTD = "/usr/bin/twistd"
UMASK = 0o027


def prepare(environ, cowrie_root, tmp_root, rng, min_pickle=MIN_PICKLE_SIZE):
    """Select, render and validate a persona into tmp_root/runtime; returns its ID.
    rng only selects the persona, the identity seed comes from secrets."""
    cowrie_root, tmp_root = Path(cowrie_root), Path(tmp_root)
    persona = catalog.by_id(choose(environ.get("COWRIE_PERSONA"), catalog.ids(), rng))
    runtime = tmp_root / "runtime"
    staging = tmp_root / "runtime.new"
    keys_dir = cowrie_root / "etc" / persona.id
    ident = draw(persona, secrets.randbits(64))
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
