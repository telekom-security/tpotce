#!/usr/bin/env python3
"""Build step of the Cowrie image: clean Cowrie's fs.pickle into
personas/base.pickle, render and validate every persona with several seeds
(the build fails on the first finding) and write personas/personas.json.

The personas themselves are rendered on every container start by
start-cowrie-persona, from base.pickle and a fresh identity."""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tpot_personas import catalog, tree  # noqa: E402
from tpot_personas.identity import draw  # noqa: E402
from tpot_personas.render import COMMON_REMOVE_PATHS, render  # noqa: E402
from tpot_personas.validate import validate  # noqa: E402


def metadata(persona):
    return {"id": persona.id, "family": persona.family, "kind": persona.kind, "user": persona.user,
            "arch": persona.arch, "ssh_banners": sorted({r.ssh_banner for r in persona.releases}),
            "package_managers": sorted(persona.package_managers), "vulnerability": persona.vulnerability}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cowrie-root", required=True, type=Path)
    parser.add_argument("--work-dir", default=Path("/tmp/cowrie-personas"), type=Path)
    parser.add_argument("--seeds", default=5, type=int)
    args = parser.parse_args(argv)
    root = args.cowrie_root.resolve()
    own_work_dir = not args.work_dir.exists()
    personas = root / "personas"
    personas.mkdir(exist_ok=True)
    base = tree.load_pickle(root / "src" / "cowrie" / "data" / "fs.pickle")
    for rel in COMMON_REMOVE_PATHS:
        tree.remove_node(base, rel)
    tree.dump_pickle(base, personas / "base.pickle")
    for persona in catalog.PERSONAS:
        for seed in range(args.seeds):
            out = args.work_dir / persona.id / str(seed)
            keys = args.work_dir / "etc" / persona.id
            ident = draw(persona, seed)
            render(persona, ident, base, out, runtime_dir=out, keys_dir=keys, cowrie_root=root)
            validate(persona, ident, out, out, keys)
        print(f"Persona {persona.id}: {args.seeds} identities rendered and valid", flush=True)
    (personas / "personas.json").write_text(
        json.dumps([metadata(p) for p in catalog.PERSONAS], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if own_work_dir:
        shutil.rmtree(args.work_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
