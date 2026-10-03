"""Check T-Pot's Heralding configuration and free-port policy without starting T-Pot."""

import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
config = yaml.safe_load((root / "docker/heralding/dist/heralding.yml").read_text())
original = yaml.safe_load(
    (Path(__file__).resolve().parent / "fixtures/tpot_heralding_legacy.yml").read_text()
)
for key, value in original["capabilities"].items():
    current = config["capabilities"][key]
    if key == "rdp":
        import copy

        current = copy.deepcopy(current)
        assert current["protocol_specific_data"].pop("tls_max_version") == "TLSv1_2"
    assert current == value, f"Existing settings changed for {key}"


def mapping_key(mapping):
    if isinstance(mapping, dict):
        return str(mapping["published"]), mapping.get("protocol", "tcp")
    value, _, protocol = str(mapping).partition("/")
    return value.split(":")[-2] if ":" in value else value, protocol or "tcp"


new = set(config["capabilities"]) - set(original["capabilities"])
paths = [
    root / "docker-compose.yml",
    *sorted((root / "compose").glob("*.yml")),
    root / "docker/heralding/docker-compose.yml",
]
for path in paths:
    services = yaml.safe_load(path.read_text()).get("services", {})
    if "heralding" not in services:
        continue
    occupied = {
        mapping_key(mapping)
        for name, service in services.items()
        if name != "heralding"
        for mapping in service.get("ports", [])
    }
    published = {mapping_key(mapping) for mapping in services["heralding"].get("ports", [])}
    for name in new:
        port = str(config["capabilities"][name]["port"])
        for protocol in ("tcp", "udp") if name == "sip" else ("tcp",):
            key = port, protocol
            assert (key in published) == (key not in occupied), (
                f"{path}: wrong mapping for {name} {key}"
            )
    print(f"{path.relative_to(root)}: free-port policy OK")
print("Existing capability settings preserved; all new services configured")
