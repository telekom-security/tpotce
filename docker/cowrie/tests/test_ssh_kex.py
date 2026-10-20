"""The key exchanges and the server-sig-algs of the patched Cowrie: [ssh] kex_algorithms orders and
narrows what Twisted offers (ssh/factory.py), [ssh] server_sig_algs is the value of the EXT_INFO the
transport sends, "" for a server that sends none (ssh/transport.py)."""
import ast
import shutil
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from tests.helpers import DIST, tmpdir
from tests.test_patch import FakeConfig, module_all
from tpot_personas import catalog, ssh_profiles
from tpot_personas.identity import draw
from tpot_personas.render import render_cfg

FIXTURE = Path(__file__).parent / "fixtures" / "v3.1.1"
PATCH = DIST / "patch_cowrie_persona_support.py"
# Twisted 26.4: _kex.getSupportedKeyExchanges() (by preference)
TWISTED_ORDER = [b"curve25519-sha256", b"curve25519-sha256@libssh.org", b"ecdh-sha2-nistp256", b"ecdh-sha2-nistp384",
                 b"ecdh-sha2-nistp521", b"diffie-hellman-group-exchange-sha256",
                 b"diffie-hellman-group-exchange-sha1", b"diffie-hellman-group14-sha1"]


class SshKexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True,
                       text=True)
        cls.factory = (root / "src/cowrie/ssh/factory.py").read_text()
        cls.transport = (root / "src/cowrie/ssh/transport.py").read_text()

    def key_exchanges(self, value, supported=TWISTED_ORDER):
        config = FakeConfig({} if value is None else {("ssh", "kex_algorithms"): value})

        class Factory:
            class SSHFactory:
                pass

        namespace = module_all(self.factory, {"CowrieConfig": config, "transport": object(), "factory": Factory,
                                              "Logger": lambda: None})
        return namespace["tpot_key_exchanges"](list(supported))

    def test_factory_offers_the_key_exchanges_of_the_persona(self):
        self.assertIn("        t.supportedKeyExchanges = tpot_key_exchanges(t.supportedKeyExchanges)\n"
                      "\n        if not self.primes:\n", self.factory)
        self.assertEqual(self.key_exchanges("curve25519-sha256@libssh.org,diffie-hellman-group14-sha1"),
                         [b"curve25519-sha256@libssh.org", b"diffie-hellman-group14-sha1"])
        # the order of the persona, not Twisted's
        self.assertEqual(self.key_exchanges("ecdh-sha2-nistp521, curve25519-sha256"),
                         [b"ecdh-sha2-nistp521", b"curve25519-sha256"])

    def test_factory_leaves_out_what_twisted_has_not(self):
        self.assertEqual(self.key_exchanges("sntrup761x25519-sha512@openssh.com,curve25519-sha256,curve25519-sha256"),
                         [b"curve25519-sha256"])
        # a Twisted without curve25519 (no X25519 in its cryptography): what is left
        self.assertEqual(self.key_exchanges("curve25519-sha256,ecdh-sha2-nistp256", TWISTED_ORDER[2:]),
                         [b"ecdh-sha2-nistp256"])
        # unset or nothing usable: Twisted's own list, as upstream
        self.assertEqual(self.key_exchanges(None), TWISTED_ORDER)
        self.assertEqual(self.key_exchanges("mlkem768x25519-sha256"), TWISTED_ORDER)

    def send_ext_info(self, value, peer=True):
        """sendExtInfo of the patched HoneyPotSSHTransport with [ssh] server_sig_algs = value (None:
        unset); returns the extensions Twisted's sendExtInfo got (None: not called)."""
        start = self.transport.index("    def sendExtInfo(self, extensions")
        end = self.transport.index("\n    def ", start + 10)
        body = textwrap.dedent(self.transport[start:end])
        sent = []

        class Base:
            @staticmethod
            def sendExtInfo(transport_self, extensions):
                sent.append(list(extensions))

        class TransportModule:
            SSHServerTransport = Base

        config = FakeConfig({} if value is None else {("ssh", "server_sig_algs"): value})
        namespace = {"CowrieConfig": config, "transport": TransportModule}
        exec(compile(ast.parse(body), "<patched transport>", "exec"), namespace)  # noqa: S102
        namespace["sendExtInfo"](object(), [(b"server-sig-algs", b"rsa-sha2-512,rsa-sha2-256,ssh-ed25519")])
        return sent[0] if sent else None

    def test_server_sig_algs_are_those_of_the_persona(self):
        self.assertEqual(self.send_ext_info("ssh-ed25519,sk-ssh-ed25519@openssh.com,rsa-sha2-256,ssh-rsa"),
                         [(b"server-sig-algs", b"ssh-ed25519,sk-ssh-ed25519@openssh.com,rsa-sha2-256,ssh-rsa")])

    def test_a_server_without_ext_info_sends_none(self):
        # OpenSSH before 7.2 and dropbear before 2020.79 send no EXT_INFO at all
        self.assertIsNone(self.send_ext_info(""))
        self.assertIsNone(self.send_ext_info("  "))

    def test_without_the_key_twisted_sends_its_own(self):
        self.assertEqual(self.send_ext_info(None), [(b"server-sig-algs", b"rsa-sha2-512,rsa-sha2-256,ssh-ed25519")])

    def test_render_names_both_per_release(self):
        import configparser
        for persona in catalog.PERSONAS:
            with self.subTest(persona=persona.id):
                ident = draw(persona, 1)
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render_cfg(persona, ident, Path("/tmp/r"), Path("/tmp/k")))
                profile = ssh_profiles.PROFILES[persona.ssh_profile]
                self.assertEqual(cfg.get("ssh", "kex_algorithms"),
                                 ",".join(ssh_profiles.kex_algorithms(profile, ident.release)))
                self.assertEqual(cfg.get("ssh", "server_sig_algs"),
                                 ssh_profiles.server_sig_algs(profile, ident.release))

    def test_fails_loudly_on_unknown_upstream(self):
        cases = (("src/cowrie/ssh/factory.py", "        if not self.primes:\n            ske = t.supportedKeyExchanges[:]\n",
                  "        if self.primes is None:\n            ske = t.supportedKeyExchanges[:]\n"),
                 ("src/cowrie/ssh/transport.py",
                  "    def _unsupportedVersionReceived(self, remoteVersion: bytes) -> None:\n",
                  "    def _unsupportedVersionReceived(self, remoteVersion) -> None:\n"))
        for rel, old, new in cases:
            with self.subTest(rel=rel):
                root = tmpdir(self) / "cowrie"
                shutil.copytree(FIXTURE, root)
                path = root / rel
                text = path.read_text()
                self.assertEqual(text.count(old), 1)
                path.write_text(text.replace(old, new))
                done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                                      capture_output=True, text=True)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("Could not find expected text", done.stderr)



class ValidateKexTest(unittest.TestCase):
    def problems(self, persona_id, **changes):
        import configparser

        from tpot_personas import validate
        persona = catalog.by_id(persona_id)
        ident = draw(persona, 2)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(render_cfg(persona, ident, Path("/tmp/r"), Path("/tmp/k")))
        for key, value in changes.items():
            cfg.set("ssh", key, value)
        out = []
        validate._check_kex(ssh_profiles.PROFILES[persona.ssh_profile], ident.release, cfg, out)
        return out

    def test_the_rendered_lists_validate(self):
        for persona in catalog.PERSONAS:
            with self.subTest(persona=persona.id):
                self.assertEqual(self.problems(persona.id), [])

    def test_a_newer_or_unknown_kex_is_found(self):
        out = self.problems("debian-trixie", kex_algorithms="curve25519-sha256,mlkem768x25519-sha256")
        self.assertEqual(out[0], "key exchange not supported: mlkem768x25519-sha256")
        self.assertTrue(out[1].startswith("key exchanges curve25519-sha256,mlkem768x25519-sha256 differ from openssh of "
                                          "SSH-2.0-OpenSSH_10.0p2 Debian-"), out)
        self.assertEqual(len(out), 2)

    def test_curve25519_is_required_and_sig_algs_checked(self):
        out = self.problems("centos-7", kex_algorithms="diffie-hellman-group14-sha1", server_sig_algs="ssh-rsa")
        self.assertIn("key exchanges offer no curve25519: modern clients cannot connect", out)
        self.assertIn("server-sig-algs ssh-rsa differ from openssh of SSH-2.0-OpenSSH_7.4", out)


if __name__ == "__main__":
    unittest.main()
