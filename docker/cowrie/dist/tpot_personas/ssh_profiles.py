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
}
