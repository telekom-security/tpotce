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
