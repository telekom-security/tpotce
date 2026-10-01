#!/usr/bin/env python3
"""T-Pot overlay for Conpot templates.

build  <templates_dir> <spec>   Assemble and override the upstream TOML templates
                                in a Conpot source tree (before pip install).
check  <spec>                   Render every deployed template with a throwaway
                                identity and validate it with Conpot's own schemas
                                (after pip install).
render <template>               Container entrypoint: load or create the per-install
                                identity, render the template to $CONPOT_TMP and
                                exec conpot.

Every override in the spec must hit something upstream (a key path that exists,
a literal that occurs), otherwise the build fails. That way an upstream rename
does not silently bring back the fingerprintable upstream defaults.
"""

import ipaddress
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

TOKEN_RE = re.compile(r"@@([A-Z0-9_]+)@@")
DEFAULT_SPEC = "/opt/tpot/templates.toml"
IDENTITY_DIR = "/var/lib/conpot"


def die(msg):
    print(f"[tpot_templates] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg):
    print(f"[tpot_templates] {msg}", file=sys.stderr)


def load_spec(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def deployed(spec):
    return [n for n, t in spec["templates"].items() if t.get("deploy", False)]


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------


def _walk(data, path, create, where):
    keys = path.split(".")
    node = data
    for i, key in enumerate(keys[:-1]):
        if isinstance(node, list):
            if not key.isdigit() or int(key) >= len(node):
                die(f"{where}: list index '{key}' invalid in '{path}'")
            node = node[int(key)]
            continue
        if key not in node:
            if not create:
                die(f"{where}: key '{'.'.join(keys[: i + 1])}' not found upstream ('{path}')")
            node[key] = {}
        node = node[key]
    last = keys[-1]
    if isinstance(node, list):
        if not last.isdigit() or int(last) >= len(node):
            die(f"{where}: list index '{last}' invalid in '{path}'")
        return node, int(last)
    if not create and last not in node:
        die(f"{where}: key '{path}' not found upstream")
    if create and last in node:
        die(f"{where}: key '{path}' already exists upstream, use 'set' instead of 'add'")
    return node, last


def build(templates_dir, spec_path):
    import tomli_w

    templates_dir = Path(templates_dir)
    spec = load_spec(spec_path)
    for name, tpl in spec["templates"].items():
        target = templates_dir / name
        base = tpl.get("base", name)
        if base != name:
            if target.exists():
                die(f"{name}: target exists upstream, cannot create it from '{base}'")
            if not (templates_dir / base).is_dir():
                die(f"{name}: base template '{base}' not found upstream")
            shutil.copytree(templates_dir / base, target)
        elif not target.is_dir():
            die(f"{name}: template not found upstream")

        for dst, src in tpl.get("include", {}).items():
            if not (templates_dir / src).is_file():
                die(f"{name}: include '{src}' not found upstream")
            shutil.copy2(templates_dir / src, target / dst)
        for rel in tpl.get("exclude", []):
            path = target / rel
            if not path.exists():
                die(f"{name}: exclude '{rel}' not found upstream")
            shutil.rmtree(path) if path.is_dir() else path.unlink()

        files = set(tpl.get("set", {})) | set(tpl.get("add", {}))
        if "template" in tpl:
            files.add("template.toml")
        for fname in sorted(files):
            fpath = target / fname
            if not fpath.is_file():
                die(f"{name}: '{fname}' not found")
            with open(fpath, "rb") as f:
                data = tomllib.load(f)
            where = f"{name}/{fname}"
            if fname == "template.toml" and "template" in tpl:
                data["core"]["template"] = dict(tpl["template"])
            for path, value in tpl.get("set", {}).get(fname, {}).items():
                node, key = _walk(data, path, False, where)
                node[key] = value
            for path, value in tpl.get("add", {}).get(fname, {}).items():
                node, key = _walk(data, path, True, where)
                node[key] = value
            fpath.write_text(tomli_w.dumps(data), encoding="utf-8")

        for fname, pairs in tpl.get("replace", {}).items():
            fpath = target / fname
            if not fpath.is_file():
                die(f"{name}: '{fname}' not found")
            text = fpath.read_text(encoding="utf-8")
            for old, new in pairs.items():
                if old not in text:
                    die(f"{name}/{fname}: literal '{old}' not found upstream")
                text = text.replace(old, new)
            fpath.write_text(text, encoding="utf-8")
        info(f"built template {name}")


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------


def _rand_lan_ip():
    if secrets.randbelow(2):
        net = f"10.{secrets.randbelow(256)}.{secrets.randbelow(256)}"
    else:
        net = f"192.168.{secrets.randbelow(256)}"
    return f"{net}.{10 + secrets.randbelow(240)}"


def _generate(gen):
    kind = gen["gen"]
    if kind == "lan_ip":
        return _rand_lan_ip()
    if kind == "mac":
        tail = [secrets.randbelow(256) for _ in range(3)]
        return gen["oui"].upper() + "".join(f":{b:02X}" for b in tail)
    if kind == "digits":
        n = gen["n"]
        first = str(1 + secrets.randbelow(9))
        return first + "".join(str(secrets.randbelow(10)) for _ in range(n - 1))
    if kind == "hex":
        value = "".join(secrets.choice("0123456789abcdef") for _ in range(gen["n"]))
        return value.upper() if gen.get("upper") else value
    if kind == "hexbytes":
        return " ".join(f"{secrets.randbelow(256):02X}" for _ in range(gen["n"]))
    if kind == "int":
        return gen["min"] + secrets.randbelow(gen["max"] - gen["min"] + 1)
    if kind == "choice":
        return secrets.choice(gen["values"])
    die(f"unknown generator '{kind}'")


def _derive(gen, values):
    src = values[gen["of"]]
    kind = gen["derive"]
    if kind == "mac_tail":
        return src.replace(":", "")[-6:].upper()
    if kind == "mac_hex":
        return "0x" + src.replace(":", "").lower()
    if kind == "mac_plain":
        return src.replace(":", "").lower()
    if kind == "ip_gateway":
        return str(ipaddress.ip_network(f"{src}/24", strict=False).network_address + 1)
    if kind == "ip_suffix":
        return src + gen["suffix"]
    if kind == "format":
        return gen["fmt"].format(src)
    die(f"unknown derivation '{kind}'")


def resolve_identity(tpl, stored):
    """Return (values, changed). Missing primary values are generated."""
    spec = tpl.get("identity", {})
    values = dict(stored)
    changed = False
    for token, gen in spec.items():
        if "gen" in gen and token not in values:
            values[token] = _generate(gen)
            changed = True
    for token, gen in spec.items():
        if "derive" in gen:
            values[token] = _derive(gen, values)
    return values, changed


def _toml_literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value))


def render_dir(src, dst, values):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    for path in dst.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "@@" not in text:
            continue

        def full(m):
            token = m.group(1)
            if token not in values:
                die(f"{path}: no identity value for @@{token}@@")
            return _toml_literal(values[token])

        def inline(m):
            token = m.group(1)
            if token not in values:
                die(f"{path}: no identity value for @@{token}@@")
            return str(values[token])

        # A quoted token is replaced by a typed TOML literal (numbers stay numbers),
        # a token inside a longer string by its text.
        text = re.sub(r'"@@([A-Z0-9_]+)@@"', full, text)
        text = TOKEN_RE.sub(inline, text)
        path.write_text(text, encoding="utf-8")
        if path.suffix == ".toml":
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                die(f"{path}: invalid TOML after rendering: {exc}")


def load_identity(name, tpl):
    ident_file = Path(IDENTITY_DIR) / f"{name}.json"
    stored = {}
    if ident_file.is_file():
        try:
            stored = json.loads(ident_file.read_text())["values"]
        except (ValueError, KeyError) as exc:
            die(f"{ident_file}: unreadable identity ({exc}); remove it to regenerate")
    values, changed = resolve_identity(tpl, stored)
    if changed:
        primary = {k: values[k] for k, g in tpl.get("identity", {}).items() if "gen" in g}
        try:
            fd, tmp = tempfile.mkstemp(dir=IDENTITY_DIR, prefix=f".{name}.")
            with os.fdopen(fd, "w") as f:
                json.dump({"version": 1, "template": name, "values": primary}, f, indent=2)
            os.replace(tmp, ident_file)
            info(f"stored new identity in {ident_file}")
        except OSError as exc:
            info(f"WARNING: cannot persist identity in {IDENTITY_DIR} ({exc}), using an ephemeral one")
    return values


def package_templates_dir():
    import conpot

    return Path(conpot.__file__).parent / "templates"


def render(name):
    spec = load_spec(os.environ.get("TPOT_TEMPLATE_SPEC", DEFAULT_SPEC))
    if name not in spec["templates"]:
        die(f"template '{name}' is not part of the T-Pot spec")
    tmp = os.environ.get("CONPOT_TMP", "/tmp/conpot")
    dst = Path(tmp) / "templates" / name
    render_dir(package_templates_dir() / name, dst, load_identity(name, spec["templates"][name]))
    argv = [
        "conpot",
        "--mibcache", tmp,
        "--temp_dir", tmp,
        "--template", str(dst),
        "--logfile", os.environ.get("CONPOT_LOG", "/var/log/conpot/conpot.log"),
        "--config", os.environ.get("CONPOT_CONFIG", "/etc/conpot/conpot.cfg"),
    ]
    os.execvp(argv[0], argv)


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------


def check(spec_path):
    from schema import SchemaError

    import conpot.protocols as protocols
    import conpot.protocols.schemas as protocol_schemas
    from conpot.templates import validate

    spec = load_spec(spec_path)
    deny = [d.lower() for d in spec.get("guard", {}).get("deny", [])]
    src_root = package_templates_dir()
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        for name in deployed(spec):
            values, _ = resolve_identity(spec["templates"][name], {})
            dst = Path(tmp) / name
            render_dir(src_root / name, dst, values)
            with open(dst / "template.toml", "rb") as f:
                template = tomllib.load(f)
            try:
                validate.validate_toml_template(template, validate.base_schema)
            except SchemaError as exc:
                errors.append(f"{name}/template.toml: {exc}")
            found = []
            for proto in protocols.name_mapping:
                pfile = dst / f"{proto}.toml"
                if not pfile.is_file():
                    continue
                found.append(proto)
                with open(pfile, "rb") as f:
                    data = tomllib.load(f)
                try:
                    validate.validate_toml_template(data, getattr(protocol_schemas, proto))
                except SchemaError as exc:
                    errors.append(f"{name}/{proto}.toml: {exc}")
            expected = sorted(template["core"]["template"]["protocols"])
            if sorted(found) != expected:
                errors.append(f"{name}: protocols {sorted(found)} != expected {expected}")
            for path in dst.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8").lower()
                except UnicodeDecodeError:
                    continue
                # emulator references (function = "conpot.emulators...") never reach the wire
                text = re.sub(r'"conpot(\.[a-z0-9_]+)+"', '""', text)
                for word in deny:
                    if word in text:
                        errors.append(f"{name}/{path.relative_to(dst)}: contains '{word}'")
            info(f"checked template {name}: {', '.join(found)}")
    if errors:
        die("template check failed:\n  " + "\n  ".join(errors))


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "build" and len(sys.argv) == 4:
        build(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 2 and sys.argv[1] == "check" and len(sys.argv) == 3:
        check(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "render":
        render(sys.argv[2])
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
