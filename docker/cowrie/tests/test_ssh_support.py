import importlib.util
import shutil
import unittest
import unittest.mock
from pathlib import Path

from tests.helpers import DIST, tmpdir
from tpot_personas.ssh_profiles import COMPRESSIONS, HOST_KEYS, TWISTED_CIPHERS, TWISTED_MACS

FIXTURE = Path(__file__).parent / "fixtures" / "v3.1.1"
# what Twisted 26.4.0 offers (SSHTransportBase.supported*), from the image build
TWISTED = {
    "ciphers": set(TWISTED_CIPHERS) | {"aes256-gcm@openssh.com"},
    "macs": set(TWISTED_MACS),
    "public_keys": {"ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "rsa-sha2-256",
                    "rsa-sha2-512", "ssh-dss", "ssh-ed25519", "ssh-rsa"},
    "compressions": {"none", "zlib"},
}


def load():
    spec = importlib.util.spec_from_file_location("check_ssh_support", DIST / "check_ssh_support.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SshSupportTest(unittest.TestCase):
    def setUp(self):
        self.check = load()
        self.root = tmpdir(self) / "cowrie"
        shutil.copytree(FIXTURE, self.root)

    def problems(self, twisted=None):
        return self.check.problems(twisted or TWISTED, self.root)

    def edit(self, rel, old, new):
        path = self.root / rel
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def test_v3_1_1_with_twisted_26_4_handles_every_algorithm(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.check.main(["--cowrie-root", str(self.root)], twisted=TWISTED), 0)

    def test_twisted_without_a_host_key_type(self):
        for name in sorted(HOST_KEYS):
            with self.subTest(name):
                twisted = dict(TWISTED, public_keys=TWISTED["public_keys"] - {name})
                self.assertEqual(self.problems(twisted), [f"host key not in Twisted: {name}"])

    def test_cowrie_factory_skipping_a_host_key_type(self):
        # startFactory loads keys with if/elif and drops an unknown type without a word
        self.edit("src/cowrie/ssh/factory.py", 'elif key == b"ssh-ed25519":', 'elif key == b"ssh-ed448":')
        self.assertEqual(self.problems(), ["host key not loaded by Cowrie (ssh/factory.py): ssh-ed25519"])

    def test_cipher_mac_and_compression_not_in_twisted(self):
        twisted = dict(TWISTED, ciphers=TWISTED["ciphers"] - {"aes128-ctr"}, macs=TWISTED["macs"] - {"hmac-md5"},
                       compressions={"none"})
        self.assertEqual(self.problems(twisted), ["cipher not in Twisted: aes128-ctr", "mac not in Twisted: hmac-md5",
                                                  "compression not in Twisted or Cowrie: zlib"])

    def test_zlib_openssh_needs_cowries_transport(self):
        self.assertIn("zlib@openssh.com", COMPRESSIONS)
        self.edit("src/cowrie/ssh/transport.py", 'CompressionType == b"zlib@openssh.com"', 'CompressionType == b"zlib"')
        self.assertEqual(self.problems(), ["compression not in Twisted or Cowrie: zlib@openssh.com"])

    def test_cowrie_factory_ignoring_the_configured_compressions(self):
        self.edit("src/cowrie/ssh/factory.py", 'CowrieConfig.get("ssh", "compression")',
                  'CowrieConfig.get("ssh", "compressions")')
        self.assertEqual(self.problems(), ["compression: Cowrie (ssh/factory.py) does not read [ssh] compression"])

    def test_main_fails_and_names_the_problem(self):
        twisted = dict(TWISTED, public_keys={"ssh-rsa"})
        with unittest.mock.patch("sys.stderr") as err:
            self.assertEqual(self.check.main(["--cowrie-root", str(self.root)], twisted=twisted), 1)
        written = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("host key not in Twisted: ecdsa-sha2-nistp256", written)


if __name__ == "__main__":
    unittest.main()
