import importlib.util
import shutil
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

from tests.helpers import DIST, tmpdir
from tpot_personas.ssh_profiles import (COMPRESSIONS, HOST_KEY_ALGORITHMS, HOST_KEYS, TWISTED_CIPHERS, TWISTED_KEX,
                                        TWISTED_MACS)

FIXTURE = Path(__file__).parent / "fixtures" / "v3.1.3"
# what Twisted 26.4.0 offers (SSHTransportBase.supported*), from the image build
TWISTED = {
    "ciphers": set(TWISTED_CIPHERS) | {"aes256-gcm@openssh.com"},
    "macs": set(TWISTED_MACS),
    "public_keys": {"ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "rsa-sha2-256",
                    "rsa-sha2-512", "ssh-dss", "ssh-ed25519", "ssh-rsa"},
    "compressions": {"none", "zlib"},
    # SSHServerTransport._getHostKeys: the key each host key algorithm signs with
    "key_types": {"ecdsa-sha2-nistp256": "ecdsa-sha2-nistp256", "rsa-sha2-256": "ssh-rsa", "rsa-sha2-512": "ssh-rsa",
                  "ssh-ed25519": "ssh-ed25519", "ssh-rsa": "ssh-rsa"},
    "sign_types": True,
    "kex": set(TWISTED_KEX),
    # SSHServerTransport.sendExtInfo, called by its _keySetup
    "ext_info": True,
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
        # the image runs the check on the patched Cowrie
        subprocess.run([sys.executable, str(DIST / "patch_cowrie_persona_support.py"), "--cowrie-root", str(self.root)],
                       check=True, capture_output=True, text=True)

    def problems(self, twisted=None):
        return self.check.problems(twisted or TWISTED, self.root)

    def edit(self, rel, old, new):
        path = self.root / rel
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def test_v3_1_3_with_twisted_26_4_handles_every_algorithm(self):
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

    def test_twisted_without_a_host_key_algorithm(self):
        twisted = dict(TWISTED, public_keys=TWISTED["public_keys"] - {"rsa-sha2-512"})
        self.assertEqual(self.problems(twisted), ["host key algorithm not in Twisted: rsa-sha2-512"])

    def test_twisted_signing_an_algorithm_with_another_key(self):
        self.assertEqual(sorted(TWISTED["key_types"]), sorted(HOST_KEY_ALGORITHMS))
        twisted = dict(TWISTED, key_types=dict(TWISTED["key_types"], **{"rsa-sha2-256": None}))
        self.assertEqual(self.problems(twisted), ["host key algorithm rsa-sha2-256 does not sign with ssh-rsa in Twisted"])

    def test_twisted_signing_without_a_signature_type(self):
        self.assertEqual(self.problems(dict(TWISTED, sign_types=False)),
                         ["host key algorithms: Twisted's Key.sign takes no signatureType"])

    def test_cowrie_factory_without_the_patch_offers_one_algorithm_per_key(self):
        shutil.rmtree(self.root)
        shutil.copytree(FIXTURE, self.root)
        self.assertEqual(self.problems(),
                         ["host key algorithms: Cowrie (ssh/factory.py) does not advertise [ssh] host_key_algorithms",
                          "key exchanges: Cowrie (ssh/factory.py) does not offer [ssh] kex_algorithms",
                          "server-sig-algs: Cowrie (ssh/transport.py) does not send [ssh] server_sig_algs"])

    def test_twisted_without_a_key_exchange(self):
        twisted = dict(TWISTED, kex=TWISTED["kex"] - {"curve25519-sha256"})
        self.assertEqual(self.problems(twisted), ["key exchange not in Twisted: curve25519-sha256"])

    def test_twisted_without_send_ext_info(self):
        self.assertEqual(self.problems(dict(TWISTED, ext_info=False)),
                         ["server-sig-algs: Twisted's server sends no EXT_INFO through sendExtInfo in _keySetup"])

    def test_cowrie_without_the_kex_and_sig_algs_patch(self):
        self.edit("src/cowrie/ssh/factory.py", 'CowrieConfig.get("ssh", "kex_algorithms"',
                  'CowrieConfig.get("ssh", "kex"')
        self.edit("src/cowrie/ssh/transport.py", 'CowrieConfig.get("ssh", "server_sig_algs"',
                  'CowrieConfig.get("ssh", "sig_algs"')
        self.assertEqual(self.problems(), [
            "key exchanges: Cowrie (ssh/factory.py) does not offer [ssh] kex_algorithms",
            "server-sig-algs: Cowrie (ssh/transport.py) does not send [ssh] server_sig_algs"])

    def test_main_fails_and_names_the_problem(self):
        twisted = dict(TWISTED, public_keys={"ssh-rsa"})
        with unittest.mock.patch("sys.stderr") as err:
            self.assertEqual(self.check.main(["--cowrie-root", str(self.root)], twisted=twisted), 1)
        written = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("host key not in Twisted: ecdsa-sha2-nistp256", written)


if __name__ == "__main__":
    unittest.main()
