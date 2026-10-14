"""SSH algorithms a persona may offer: only what Twisted 26.4.0 (Cowrie 3.1.1)
can negotiate, ordered as the emulated server orders them. The Docker build
checks these sets against the installed Twisted."""
import re
from dataclasses import dataclass

TWISTED_CIPHERS = frozenset({"aes256-ctr", "aes256-cbc", "aes192-ctr", "aes192-cbc",
                             "aes128-ctr", "aes128-cbc", "3des-cbc"})
TWISTED_MACS = frozenset({"hmac-sha2-512", "hmac-sha2-384", "hmac-sha2-256", "hmac-sha1", "hmac-md5"})
COMPRESSIONS = frozenset({"none", "zlib", "zlib@openssh.com"})
HOST_KEYS = frozenset({"ssh-rsa", "ecdsa-sha2-nistp256", "ssh-ed25519"})
# the host key algorithms a server offers for these keys ([ssh] host_key_algorithms of the patched
#   factory); an RSA key signs ssh-rsa (SHA-1), rsa-sha2-256 and rsa-sha2-512 (RFC 8332)
HOST_KEY_ALGORITHMS = frozenset({"rsa-sha2-512", "rsa-sha2-256", "ssh-rsa", "ecdsa-sha2-nistp256", "ssh-ed25519"})
KEY_TYPES = {"rsa-sha2-512": "ssh-rsa", "rsa-sha2-256": "ssh-rsa"}
LEGACY = frozenset({"3des-cbc", "aes128-cbc", "aes192-cbc", "aes256-cbc", "hmac-md5"})


@dataclass(frozen=True)
class SshProfile:
    name: str
    ciphers: tuple
    macs: tuple
    compression: tuple
    host_keys: tuple


PROFILES = {
    # OpenSSH >= 7.4 server defaults minus chacha20/gcm/etm/umac (Twisted has none of them). Host keys in
    #   the order sshd offers them: the order of its HostKey files (servconf.c defaults and the sshd_config
    #   of Debian, Ubuntu, RHEL: rsa, ecdsa, ed25519), not the client preference of HostKeyAlgorithms;
    #   ssh -vv against OpenSSH 9.6p1 (Ubuntu 24.04) and 9.2p1 (Debian 12): rsa-sha2-512,rsa-sha2-256,
    #   ecdsa-sha2-nistp256,ssh-ed25519; the algorithms of the RSA key per version: host_key_algorithms
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


def key_type(algorithm):
    """The host key an algorithm signs with: rsa-sha2-256 / rsa-sha2-512 -> ssh-rsa."""
    return KEY_TYPES.get(algorithm, algorithm)


def server_version(release):
    """(product, (major, minor)) of the SSH server: what the banner names, the version of ssh -V
    where the banner has none (OpenWrt builds dropbear without it). ("openssh", (9, 6)),
    ("dropbear", (2012, 55))."""
    product = release.ssh_banner[len("SSH-2.0-"):].split(" ", 1)[0]
    name, _, version = product.partition("_")
    if not version:
        match = re.search(r"\d+(?:\.\d+)+", release.ssh_version)
        version = match.group(0) if match else ""
    return name.lower(), tuple(int(n) for n in re.findall(r"\d+", version.split("p", 1)[0])[:2])


def rsa_algorithms(product, version, sha1=True):
    """What a server offers for its RSA key, in its order.
    OpenSSH (sshd.c list_hostkey_types): rsa-sha2-* from 7.2; up to 7.7 after ssh-rsa
      ("ssh-rsa,rsa-sha2-512,rsa-sha2-256", as 7.4 of CentOS 7 / Debian 9 sends it), then before
      it (8.2: "rsa-sha2-512,rsa-sha2-256,ssh-rsa"); 8.8 drops ssh-rsa from the default
      HostKeyAlgorithms. Unverified: the release between 7.4 and 8.2 that moved ssh-rsa last (7.8
      here; no persona has a version in between).
    Dropbear (common-algo.c sigalgs): rsa-sha2-256 from 2020.79, before ssh-rsa; no rsa-sha2-512.
    sha1=False: a policy without SHA-1 signatures (RHEL 9 crypto-policies DEFAULT)."""
    if product == "openssh":
        if version < (7, 2):
            names = ("ssh-rsa",)
        elif version < (7, 8):
            names = ("ssh-rsa", "rsa-sha2-512", "rsa-sha2-256")
        elif version < (8, 8):
            names = ("rsa-sha2-512", "rsa-sha2-256", "ssh-rsa")
        else:
            names = ("rsa-sha2-512", "rsa-sha2-256")
    elif product == "dropbear":
        names = ("rsa-sha2-256", "ssh-rsa") if version >= (2020, 79) else ("ssh-rsa",)
    else:
        raise ValueError(f"no SSH server product: {product!r}")
    return tuple(name for name in names if sha1 or name != "ssh-rsa")


def host_key_algorithms(profile, release):
    """The server host key algorithms of a release, in the order of the profile's keys (the order
    of the HostKey files): the RSA key as its version offers it. release.extra["ssh_rsa_sha1"]
    False: no ssh-rsa (a crypto policy without SHA-1)."""
    product, version = server_version(release)
    sha1 = release.extra.get("ssh_rsa_sha1", True)
    names = []
    for key in profile.host_keys:
        names += rsa_algorithms(product, version, sha1) if key == "ssh-rsa" else [key]
    return tuple(names)
