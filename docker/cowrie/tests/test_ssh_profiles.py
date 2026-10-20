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

    def test_openssh_offers_its_host_keys_in_the_order_of_the_hostkey_files(self):
        # sshd lists its keys as loaded (rsa, ecdsa, ed25519 of servconf.c and the distributions'
        # sshd_config), checked with ssh -vv against OpenSSH 9.6p1 and 9.2p1; ed25519 first is the
        # client's HostKeyAlgorithms preference, not what a server sends
        self.assertEqual(sp.PROFILES["openssh"].host_keys, ("ssh-rsa", "ecdsa-sha2-nistp256", "ssh-ed25519"))

    def test_openwrt_dropbear_has_the_defaults_of_2020_and_later(self):
        profile = sp.PROFILES["dropbear-openwrt"]
        # no CBC, no 3DES, no MD5 (default_options.h), no zlib (OpenWrt --disable-zlib), ed25519 host key
        self.assertFalse(set(profile.ciphers + profile.macs) & sp.LEGACY)
        self.assertEqual(profile.compression, ("none",))
        self.assertEqual(profile.host_keys, ("ssh-ed25519", "ssh-rsa"))


def _release(banner, ssh_version, **extra):
    from tpot_personas.model import Release
    return Release(label="x", ssh_banner=banner, ssh_version=ssh_version, kernel_version="", kernel_build="",
                   os_version="", os_version_id="", extra=extra)


class HostKeyAlgorithmsTest(unittest.TestCase):
    def test_the_version_comes_from_the_banner_or_from_ssh_v(self):
        self.assertEqual(sp.server_version(_release("SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.14", "")),
                         ("openssh", (9, 6)))
        self.assertEqual(sp.server_version(_release("SSH-2.0-OpenSSH_8.7", "OpenSSH_8.7p1, OpenSSL 3.2.2")),
                         ("openssh", (8, 7)))
        self.assertEqual(sp.server_version(_release("SSH-2.0-OpenSSH_10.0p2 Debian-7", "")), ("openssh", (10, 0)))
        self.assertEqual(sp.server_version(_release("SSH-2.0-dropbear_2012.55", "Dropbear sshd 2012.55")),
                         ("dropbear", (2012, 55)))
        # OpenWrt's dropbear names no version in its banner
        self.assertEqual(sp.server_version(_release("SSH-2.0-dropbear", "Dropbear v2022.82")), ("dropbear", (2022, 82)))

    def test_rsa_per_openssh_release(self):
        self.assertEqual(sp.rsa_algorithms("openssh", (6, 7)), ("ssh-rsa",))
        self.assertEqual(sp.rsa_algorithms("openssh", (7, 4)), ("ssh-rsa", "rsa-sha2-512", "rsa-sha2-256"))
        self.assertEqual(sp.rsa_algorithms("openssh", (8, 2)), ("rsa-sha2-512", "rsa-sha2-256", "ssh-rsa"))
        self.assertEqual(sp.rsa_algorithms("openssh", (8, 7), sha1=False), ("rsa-sha2-512", "rsa-sha2-256"))
        for version in ((8, 8), (8, 9), (9, 6), (10, 0)):
            self.assertEqual(sp.rsa_algorithms("openssh", version), ("rsa-sha2-512", "rsa-sha2-256"), version)

    def test_rsa_per_dropbear_release(self):
        for version in ((2012, 55), (2015, 71), (2016, 74), (2019, 78)):
            self.assertEqual(sp.rsa_algorithms("dropbear", version), ("ssh-rsa",), version)
        for version in ((2020, 79), (2020, 81), (2022, 82)):
            self.assertEqual(sp.rsa_algorithms("dropbear", version), ("rsa-sha2-256", "ssh-rsa"), version)

    def test_every_persona_release(self):
        from tpot_personas import catalog
        noble = ("rsa-sha2-512", "rsa-sha2-256", "ecdsa-sha2-nistp256", "ssh-ed25519")
        wanted = {
            "ubuntu-noble": noble, "ubuntu-jammy": noble, "debian-trixie": noble, "debian-bookworm": noble,
            "raspios": noble, "gpu-ubuntu": noble, "qnap-qts": noble,
            # RHEL 9: OpenSSH 8.7, but its crypto policy has no SHA-1
            "rhel-9": noble,
            "centos-7": ("ssh-rsa", "rsa-sha2-512", "rsa-sha2-256", "ecdsa-sha2-nistp256", "ssh-ed25519"),
            "ubiquiti-edgerouter-x": ("ssh-rsa", "rsa-sha2-512", "rsa-sha2-256", "ecdsa-sha2-nistp256", "ssh-ed25519"),
            "synology-dsm": ("rsa-sha2-512", "rsa-sha2-256", "ssh-rsa", "ecdsa-sha2-nistp256", "ssh-ed25519"),
            "zyxel-nas326": ("ssh-rsa", "ecdsa-sha2-nistp256", "ssh-ed25519"),
            "openwrt": ("ssh-ed25519", "rsa-sha2-256", "ssh-rsa"),
            "dlink-dir859": ("ssh-rsa",), "tplink-archer-c7": ("ssh-rsa",), "ipcam": ("ssh-rsa",),
        }
        self.assertEqual(set(wanted), set(catalog.ids()))
        for persona in catalog.PERSONAS:
            for release in persona.releases:
                with self.subTest(persona=persona.id, release=release.label):
                    names = sp.host_key_algorithms(sp.PROFILES[persona.ssh_profile], release)
                    self.assertEqual(names, wanted[persona.id])
                    self.assertTrue(set(names) <= sp.HOST_KEY_ALGORITHMS)
                    self.assertEqual({sp.key_type(n) for n in names}, set(sp.PROFILES[persona.ssh_profile].host_keys))


# The server KEXINIT and the server-sig-algs of the real servers (ssh -vv of an OpenSSH 10.0 client in a
#   container: refs/ssh-devices.json and ssh-devices-e.json of round 4), less what Twisted 26.4 has not
#   (sntrup761x25519, mlkem768x25519, diffie-hellman-group14-sha256 / group16 / group18, group1-sha1,
#   kex-strict, kexguess2); ext-info-s is Twisted's own
_KEX_MODERN = ("curve25519-sha256", "curve25519-sha256@libssh.org", "ecdh-sha2-nistp256", "ecdh-sha2-nistp384",
               "ecdh-sha2-nistp521")
_KEX_GEX = _KEX_MODERN + ("diffie-hellman-group-exchange-sha256",)
_SIG_96 = ("ssh-ed25519,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,sk-ssh-ed25519@openssh.com,"
           "sk-ecdsa-sha2-nistp256@openssh.com,rsa-sha2-512,rsa-sha2-256")
_SIG_92 = ("ssh-ed25519,sk-ssh-ed25519@openssh.com,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,"
           "sk-ecdsa-sha2-nistp256@openssh.com,webauthn-sk-ecdsa-sha2-nistp256@openssh.com,ssh-dss,ssh-rsa,"
           "rsa-sha2-256,rsa-sha2-512")
_SIG_87 = ("ssh-ed25519,sk-ssh-ed25519@openssh.com,ssh-rsa,rsa-sha2-256,rsa-sha2-512,ssh-dss,ecdsa-sha2-nistp256,"
           "ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,sk-ecdsa-sha2-nistp256@openssh.com,"
           "webauthn-sk-ecdsa-sha2-nistp256@openssh.com")
_SIG_82 = ("ssh-ed25519,sk-ssh-ed25519@openssh.com,ssh-rsa,rsa-sha2-256,rsa-sha2-512,ssh-dss,ecdsa-sha2-nistp256,"
           "ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,sk-ecdsa-sha2-nistp256@openssh.com")
KEX_WANTED = {
    "ubuntu-noble": (_KEX_GEX, _SIG_96), "gpu-ubuntu": (_KEX_GEX, _SIG_96), "qnap-qts": (_KEX_GEX, _SIG_96),
    # OpenSSH 10.0 has no finite field DH in its server default
    "debian-trixie": (_KEX_MODERN, _SIG_96),
    "debian-bookworm": (_KEX_GEX, _SIG_92), "raspios": (_KEX_GEX, _SIG_92),
    "ubuntu-jammy": (_KEX_GEX, _SIG_87), "rhel-9": (_KEX_GEX, _SIG_87),
    "synology-dsm": (_KEX_GEX, _SIG_82),
    # CentOS 7 adds diffie-hellman-group-exchange-sha1 (and group1-sha1) to OpenSSH 7.4, Debian 9 does not
    "centos-7": (_KEX_GEX + ("diffie-hellman-group-exchange-sha1", "diffie-hellman-group14-sha1"),
                 "rsa-sha2-256,rsa-sha2-512"),
    "ubiquiti-edgerouter-x": (_KEX_GEX + ("diffie-hellman-group14-sha1",),
                              "ssh-ed25519,ssh-rsa,ssh-dss,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521"),
    # OpenSSH 6.7 (Debian 8): no curve25519-sha256 yet, no EXT_INFO (RFC 8308 came with 7.2)
    "zyxel-nas326": (("curve25519-sha256@libssh.org", "ecdh-sha2-nistp256", "ecdh-sha2-nistp384",
                      "ecdh-sha2-nistp521", "diffie-hellman-group-exchange-sha256", "diffie-hellman-group14-sha1"),
                     ""),
    # dropbear without ECC (no ECDSA host key): curve25519 and group14-sha1, no EXT_INFO before 2020.79;
    #   2012.55 had DH only, its curve25519-sha256@libssh.org is kept for the clients of today
    "dlink-dir859": (("curve25519-sha256@libssh.org", "diffie-hellman-group14-sha1"), ""),
    # dropbear 2019.78 of the Archer C7 (group14-sha256, group14-sha1, group1-sha1 in its binary): the
    #   Twisted subset and the kept curve25519-sha256@libssh.org, no EXT_INFO, ssh-rsa only
    "tplink-archer-c7": (("curve25519-sha256@libssh.org", "diffie-hellman-group14-sha1"), ""),
    "ipcam": (("curve25519-sha256@libssh.org", "diffie-hellman-group14-sha1"), ""),
}
OPENWRT_SIGS = {"21.02.7": "ssh-ed25519,rsa-sha2-256,ssh-rsa",
                "22.03.7": "ssh-ed25519,sk-ssh-ed25519@openssh.com,rsa-sha2-256,ssh-rsa"}


class KeyExchangeTest(unittest.TestCase):
    def test_every_persona_release(self):
        from tpot_personas import catalog
        self.assertEqual(set(KEX_WANTED) | {"openwrt"}, set(catalog.ids()))
        for persona in catalog.PERSONAS:
            profile = sp.PROFILES[persona.ssh_profile]
            for release in persona.releases:
                with self.subTest(persona=persona.id, release=release.label):
                    if persona.id == "openwrt":
                        kex = ("curve25519-sha256", "curve25519-sha256@libssh.org", "diffie-hellman-group14-sha1")
                        sigs = OPENWRT_SIGS[release.label]
                    elif persona.id == "gpu-ubuntu" and "OpenSSH_8.9p1" in release.ssh_banner:
                        kex, sigs = KEX_WANTED["ubuntu-jammy"]   # the 22.04 releases of the GPU server
                    else:
                        kex, sigs = KEX_WANTED[persona.id]
                    self.assertEqual(sp.kex_algorithms(profile, release), kex)
                    self.assertEqual(sp.server_sig_algs(profile, release), sigs)

    def test_only_twisted_kex_and_curve25519_always(self):
        from tpot_personas import catalog
        for persona in catalog.PERSONAS:
            for release in persona.releases:
                with self.subTest(persona=persona.id, release=release.label):
                    names = sp.kex_algorithms(sp.PROFILES[persona.ssh_profile], release)
                    self.assertTrue(set(names) <= sp.TWISTED_KEX)
                    self.assertTrue({"curve25519-sha256", "curve25519-sha256@libssh.org"} & set(names))
                    self.assertEqual(len(names), len(set(names)))

    def test_twisted_kex_is_twisted_26_4(self):
        self.assertEqual(sp.TWISTED_KEX, frozenset({
            "curve25519-sha256", "curve25519-sha256@libssh.org", "diffie-hellman-group-exchange-sha256",
            "diffie-hellman-group-exchange-sha1", "diffie-hellman-group14-sha1", "ecdh-sha2-nistp256",
            "ecdh-sha2-nistp384", "ecdh-sha2-nistp521"}))
