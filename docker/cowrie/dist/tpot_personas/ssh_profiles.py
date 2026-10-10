"""SSH algorithms a persona may offer: only what Twisted 26.4.0 (Cowrie 3.1.1)
can negotiate, ordered as the emulated server orders them. The Docker build
checks these sets against the installed Twisted."""
from dataclasses import dataclass

TWISTED_CIPHERS = frozenset({"aes256-ctr", "aes256-cbc", "aes192-ctr", "aes192-cbc",
                             "aes128-ctr", "aes128-cbc", "3des-cbc"})
TWISTED_MACS = frozenset({"hmac-sha2-512", "hmac-sha2-384", "hmac-sha2-256", "hmac-sha1", "hmac-md5"})
COMPRESSIONS = frozenset({"none", "zlib", "zlib@openssh.com"})
HOST_KEYS = frozenset({"ssh-rsa", "ecdsa-sha2-nistp256", "ssh-ed25519"})
LEGACY = frozenset({"3des-cbc", "aes128-cbc", "aes192-cbc", "aes256-cbc", "hmac-md5"})


@dataclass(frozen=True)
class SshProfile:
    name: str
    ciphers: tuple
    macs: tuple
    compression: tuple
    host_keys: tuple


PROFILES = {
    # OpenSSH >= 7.4 server defaults minus chacha20/gcm/etm/umac (Twisted has none of them)
    "openssh": SshProfile("openssh", ("aes128-ctr", "aes192-ctr", "aes256-ctr"),
                          ("hmac-sha2-256", "hmac-sha2-512", "hmac-sha1"),
                          ("none", "zlib@openssh.com"),
                          ("ssh-rsa", "ecdsa-sha2-nistp256", "ssh-ed25519")),
    # Dropbear 2012-2017 as shipped in router / camera firmware
    "dropbear": SshProfile("dropbear", ("aes128-ctr", "aes256-ctr", "aes128-cbc", "aes256-cbc", "3des-cbc"),
                           ("hmac-sha1", "hmac-sha2-256", "hmac-md5"),
                           ("zlib@openssh.com", "none"),
                           ("ssh-rsa",)),
    # Dropbear 2020.81 / 2022.82 as OpenWrt 21.02 / 22.03 build it: default_options.h has CBC and 3DES off
    #   and no MD5 HMAC (hmac-sha1, hmac-sha2-256; sha2-512 off), OpenWrt's package builds --disable-zlib and
    #   adds ed25519 (DROPBEAR_ED25519 default y), DSS off, ECDSA off (DROPBEAR_ECC default n); in the order
    #   of common-algo.c (ssh-ed25519 before the RSA keys); chacha20-poly1305 dropped (not in Twisted)
    "dropbear-openwrt": SshProfile("dropbear-openwrt", ("aes128-ctr", "aes256-ctr"),
                                   ("hmac-sha1", "hmac-sha2-256"),
                                   ("none",),
                                   ("ssh-ed25519", "ssh-rsa")),
}
