#!/usr/bin/env python3
"""Build check of the Cowrie image: every SSH algorithm a persona may offer
(tpot_personas.ssh_profiles) is one this Twisted negotiates and this Cowrie
handles. Cowrie's startFactory loads host keys with if/elif and silently drops
a type it does not know, so a bump that loses one would make a persona offer
fewer host keys than its profile; zlib@openssh.com is Cowrie's own (ssh/transport.py).
The host key algorithms (rsa-sha2-512, rsa-sha2-256 ...) must be Twisted's, sign
with the key ssh_profiles names (SSHServerTransport._getHostKeys) and be what the
patched factory advertises ([ssh] host_key_algorithms); run after the patch."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tpot_personas.ssh_profiles import (COMPRESSIONS, HOST_KEY_ALGORITHMS, HOST_KEYS, TWISTED_CIPHERS,  # noqa: E402
                                        TWISTED_MACS, key_type)


def twisted_support():
    import inspect

    from twisted.conch.ssh.keys import Key
    from twisted.conch.ssh.transport import SSHServerTransport, SSHTransportBase as T

    class Stub:
        """The factory's keys by key type, as Cowrie's startFactory stores them."""
        class factory:
            publicKeys = {k.encode(): k for k in HOST_KEYS}
            privateKeys = publicKeys

    key_types = {}
    for name in sorted(HOST_KEY_ALGORITHMS):
        try:
            key_types[name] = SSHServerTransport._getHostKeys(Stub(), name.encode())[1]
        except (KeyError, AttributeError):
            key_types[name] = None
    return {"ciphers": {c.decode() for c in T.supportedCiphers},
            "macs": {m.decode() for m in T.supportedMACs},
            "public_keys": {k.decode() for k in T.supportedPublicKeys},
            "compressions": {c.decode() for c in T.supportedCompressions},
            "key_types": key_types,
            "sign_types": "signatureType" in inspect.signature(Key.sign).parameters}


def problems(twisted, cowrie_root):
    ssh = Path(cowrie_root) / "src" / "cowrie" / "ssh"
    factory = (ssh / "factory.py").read_text(encoding="utf-8")
    transport = (ssh / "transport.py").read_text(encoding="utf-8")
    out = []
    for word, wanted, have in (("cipher", TWISTED_CIPHERS, twisted["ciphers"]),
                               ("mac", TWISTED_MACS, twisted["macs"]),
                               ("host key", HOST_KEYS, twisted["public_keys"])):
        out += [f"{word} not in Twisted: {name}" for name in sorted(wanted - have)]
    out += [f"host key not loaded by Cowrie (ssh/factory.py): {name}"
            for name in sorted(HOST_KEYS) if f'key == b"{name}"' not in factory]
    out += [f"host key algorithm not in Twisted: {name}"
            for name in sorted(HOST_KEY_ALGORITHMS - HOST_KEYS - twisted["public_keys"])]
    out += [f"host key algorithm {name} does not sign with {key_type(name)} in Twisted"
            for name in sorted(HOST_KEY_ALGORITHMS) if twisted["key_types"].get(name) != key_type(name)]
    if not twisted["sign_types"]:
        out.append("host key algorithms: Twisted's Key.sign takes no signatureType")
    if ('CowrieConfig.get("ssh", "host_key_algorithms"' not in factory
            or "t.supportedPublicKeys = tpot_host_key_algorithms(self.privateKeys)" not in factory):
        out.append("host key algorithms: Cowrie (ssh/factory.py) does not advertise [ssh] host_key_algorithms")
    if 'CowrieConfig.get("ssh", "compression")' not in factory:
        out.append("compression: Cowrie (ssh/factory.py) does not read [ssh] compression")
    out += [f"compression not in Twisted or Cowrie: {name}"
            for name in sorted(COMPRESSIONS - twisted["compressions"])
            if f'CompressionType == b"{name}"' not in transport]
    return out


def main(argv=None, twisted=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cowrie-root", required=True, type=Path)
    args = parser.parse_args(argv)
    found = problems(twisted if twisted is not None else twisted_support(), args.cowrie_root)
    for line in found:
        print(f"SSH support: {line}", file=sys.stderr)
    if found:
        return 1
    print("SSH support: ciphers, MACs, host keys, host key algorithms and compressions of every profile are handled",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
