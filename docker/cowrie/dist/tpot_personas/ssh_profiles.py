"""SSH algorithms a persona may offer: only what Twisted 26.4.0 (Cowrie 3.1.3)
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
# the key exchanges of Twisted 26.4 (twisted.conch.ssh._kex); none of sntrup761x25519, mlkem768x25519,
#   diffie-hellman-group14-sha256 / group16 / group18 / group1-sha1, kex-strict or kexguess2
TWISTED_KEX = frozenset({"curve25519-sha256", "curve25519-sha256@libssh.org", "ecdh-sha2-nistp256",
                         "ecdh-sha2-nistp384", "ecdh-sha2-nistp521", "diffie-hellman-group-exchange-sha256",
                         "diffie-hellman-group-exchange-sha1", "diffie-hellman-group14-sha1"})


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
    # Dropbear 2019.78 of the TP-Link Archer C7 v5 images (the algorithm names in its binary): aes128-ctr,
    #   aes256-ctr, hmac-sha1, hmac-sha2-256, no zlib, an RSA host key only; no curve25519 or ECC (its KEX:
    #   diffie-hellman-group14-sha256, -group14-sha1, -group1-sha1, kexguess2)
    "dropbear-tplink": SshProfile("dropbear-tplink", ("aes128-ctr", "aes256-ctr"),
                                  ("hmac-sha1", "hmac-sha2-256"),
                                  ("none",),
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


_ECDH = ("ecdh-sha2-nistp256", "ecdh-sha2-nistp384", "ecdh-sha2-nistp521")
_CURVE = ("curve25519-sha256", "curve25519-sha256@libssh.org")
_GEX = "diffie-hellman-group-exchange-sha256"
_GROUP14 = "diffie-hellman-group14-sha1"


def kex_algorithms(profile, release):
    """The key exchanges the server of the release offers, in its order, as far as Twisted has them
    (TWISTED_KEX). Read from the server KEXINIT of the real servers (ssh -vv, round 4 references):
      OpenSSH 6.7 (Debian 8): curve25519-sha256@libssh.org, ecdh x3, gex-sha256, group14-sha1;
      OpenSSH 7.4 (Debian 9): curve25519-sha256 first, then as 6.7; CentOS 7 adds gex-sha1 after
        gex-sha256 (release.extra["ssh_vendor"] == "centos");
      OpenSSH 8.2 to 9.9 (Ubuntu 20.04, RHEL 9.4, Ubuntu 22.04, Debian 12, Ubuntu 24.04, Alpine 3.21):
        no group14-sha1 (unverified: the release between 7.4 and 8.2 that dropped it);
      OpenSSH 10.0 (Debian 13): no finite field DH;
      dropbear 2016.72 / 2016.74: curve25519-sha256@libssh.org, ecdh (521, 384, 256), group14-sha1;
        2020.81 / 2022.82 (OpenWrt): curve25519-sha256 before it (unverified: the release between
        2016.74 and 2020.81 that added it); ECDH only with an ECDSA host key in the profile (a firmware
        built without ECC has neither).
    The curve25519 exchange is always kept: dropbear 2012.55 had DH only (group1-sha1, group14-sha1),
    which the OpenSSH clients of today no longer offer."""
    product, version = server_version(release)
    ecc = "ecdsa-sha2-nistp256" in profile.host_keys
    if product == "openssh":
        names = list(_CURVE if version >= (7, 4) else _CURVE[1:]) + list(_ECDH)
        if version < (10, 0):
            names.append(_GEX)
        if release.extra.get("ssh_vendor") == "centos":
            names.append("diffie-hellman-group-exchange-sha1")
        if version < (8, 2):
            names.append(_GROUP14)
    elif product == "dropbear":
        names = list(_CURVE if version >= (2020, 79) else _CURVE[1:])
        if ecc:
            names += reversed(_ECDH)
        names.append(_GROUP14)
    else:
        raise ValueError(f"no SSH server product: {product!r}")
    return tuple(name for name in names if name in TWISTED_KEX)


_SIGS_OPENSSH = (
    # (from version, server-sig-algs): the PubkeyAcceptedAlgorithms the server names in EXT_INFO
    ((9, 5), "ssh-ed25519,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,sk-ssh-ed25519@openssh.com,"
             "sk-ecdsa-sha2-nistp256@openssh.com,rsa-sha2-512,rsa-sha2-256"),
    ((9, 2), "ssh-ed25519,sk-ssh-ed25519@openssh.com,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,"
             "sk-ecdsa-sha2-nistp256@openssh.com,webauthn-sk-ecdsa-sha2-nistp256@openssh.com,ssh-dss,ssh-rsa,"
             "rsa-sha2-256,rsa-sha2-512"),
    ((8, 7), "ssh-ed25519,sk-ssh-ed25519@openssh.com,ssh-rsa,rsa-sha2-256,rsa-sha2-512,ssh-dss,ecdsa-sha2-nistp256,"
             "ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,sk-ecdsa-sha2-nistp256@openssh.com,"
             "webauthn-sk-ecdsa-sha2-nistp256@openssh.com"),
    ((8, 2), "ssh-ed25519,sk-ssh-ed25519@openssh.com,ssh-rsa,rsa-sha2-256,rsa-sha2-512,ssh-dss,ecdsa-sha2-nistp256,"
             "ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,sk-ecdsa-sha2-nistp256@openssh.com"),
    ((7, 2), "ssh-ed25519,ssh-rsa,ssh-dss,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521"),
)


def server_sig_algs(profile, release):
    """The value of server-sig-algs in the server's EXT_INFO (RFC 8308), "" for a server that sends
    none: OpenSSH before 7.2, dropbear before 2020.79. Verbatim from the real servers (ssh -vv, round 4
    references): OpenSSH 7.4 of Debian 9 and of CentOS 7 (its own list), 8.2 (Ubuntu 20.04), 8.7
    (RHEL 9.4, the same as 8.9 of Ubuntu 22.04), 9.2 (Debian 12), 9.6 / 9.9 / 10.0; dropbear 2020.81 and
    2022.82 of OpenWrt (ECDSA left out without ECC). Unverified: the releases in between (the next
    lower list); 9.5 dropped ssh-dss, its list is taken as that of 9.6."""
    product, version = server_version(release)
    if product == "openssh":
        if release.extra.get("ssh_vendor") == "centos" and version[:2] == (7, 4):
            return "rsa-sha2-256,rsa-sha2-512"
        for since, value in _SIGS_OPENSSH:
            if version >= since:
                return value
        return ""
    if product == "dropbear":
        if version < (2020, 79):
            return ""
        ecc = "ecdsa-sha2-nistp256" in profile.host_keys
        names = ["ssh-ed25519"]
        if version >= (2022, 82):
            names.append("sk-ssh-ed25519@openssh.com")
        if ecc:
            names += ["ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521"]
            if version >= (2022, 82):
                names.append("sk-ecdsa-sha2-nistp256@openssh.com")
        return ",".join(names + ["rsa-sha2-256", "ssh-rsa"])
    raise ValueError(f"no SSH server product: {product!r}")
