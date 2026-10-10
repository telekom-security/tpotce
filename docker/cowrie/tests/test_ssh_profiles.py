import unittest

from tests.helpers import DIST  # noqa: F401
from tpot_personas import ssh_profiles as sp


class ProfilesTest(unittest.TestCase):
    def test_profiles_only_use_twisted_algorithms(self):
        for profile in sp.PROFILES.values():
            self.assertTrue(set(profile.ciphers) <= sp.TWISTED_CIPHERS, profile.name)
            self.assertTrue(set(profile.macs) <= sp.TWISTED_MACS, profile.name)
            self.assertTrue(set(profile.compression) <= sp.COMPRESSIONS, profile.name)
            self.assertTrue(set(profile.host_keys) <= sp.HOST_KEYS, profile.name)
            self.assertTrue(profile.ciphers and profile.macs and profile.host_keys)

    def test_openssh_offers_nothing_legacy(self):
        self.assertFalse(set(sp.PROFILES["openssh"].ciphers + sp.PROFILES["openssh"].macs) & sp.LEGACY)

    def test_no_dsa(self):
        for profile in sp.PROFILES.values():
            self.assertNotIn("ssh-dss", profile.host_keys)

    def test_openwrt_dropbear_has_the_defaults_of_2020_and_later(self):
        profile = sp.PROFILES["dropbear-openwrt"]
        # no CBC, no 3DES, no MD5 (default_options.h), no zlib (OpenWrt --disable-zlib), ed25519 host key
        self.assertFalse(set(profile.ciphers + profile.macs) & sp.LEGACY)
        self.assertEqual(profile.compression, ("none",))
        self.assertEqual(profile.host_keys, ("ssh-ed25519", "ssh-rsa"))
