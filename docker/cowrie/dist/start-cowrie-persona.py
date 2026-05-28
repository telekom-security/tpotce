#!/usr/bin/env python3

import json
import os
import random
import shutil
import sys
from pathlib import Path


COWRIE_ROOT = Path("/home/cowrie/cowrie")
PERSONAS_ROOT = COWRIE_ROOT / "personas"
RUNTIME_ROOT = Path("/tmp/cowrie/runtime")
SELECTED_PERSONA_FILE = Path("/tmp/cowrie/persona")


def load_personas():
    metadata_path = PERSONAS_ROOT / "personas.json"
    with metadata_path.open(encoding="utf-8") as handle:
        personas = json.load(handle)
    if not isinstance(personas, list) or not personas:
        raise RuntimeError(f"No personas found in {metadata_path}")
    return personas


def choose_persona(personas):
    requested = os.environ.get("COWRIE_PERSONA", "").strip()
    ids = [persona["id"] for persona in personas]
    if requested and requested != "random":
        if requested not in ids:
            raise RuntimeError(
                f"Unknown COWRIE_PERSONA={requested!r}; expected one of: {', '.join(ids)}"
            )
        return requested
    return random.choice(ids)


def activate_persona(persona_id):
    persona_dir = PERSONAS_ROOT / persona_id
    config_path = persona_dir / "cowrie.cfg"
    if not config_path.is_file():
        raise RuntimeError(f"Persona config not found: {config_path}")
    if not (persona_dir / "fs.pickle").is_file():
        raise RuntimeError(f"Persona fs.pickle not found: {persona_dir / 'fs.pickle'}")
    if not (persona_dir / "honeyfs").is_dir():
        raise RuntimeError(f"Persona honeyfs not found: {persona_dir / 'honeyfs'}")

    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_path, RUNTIME_ROOT / "cowrie.cfg")
    SELECTED_PERSONA_FILE.write_text(persona_id + "\n", encoding="utf-8")


def main():
    try:
        personas = load_personas()
        persona_id = choose_persona(personas)
        activate_persona(persona_id)
    except Exception as exc:
        print(f"Could not activate Cowrie persona: {exc}", file=sys.stderr)
        return 1

    print(f"Starting Cowrie persona: {persona_id}", flush=True)
    os.environ.setdefault("PYTHONPATH", f"{COWRIE_ROOT}:{COWRIE_ROOT / 'src'}")
    os.chdir(RUNTIME_ROOT)
    os.execv(
        "/usr/bin/twistd",
        [
            "/usr/bin/twistd",
            "--nodaemon",
            "--pidfile",
            "/tmp/cowrie/cowrie.pid",
            "cowrie",
        ],
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
