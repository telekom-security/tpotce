# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2016-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

"""
This module contains ...
"""

from __future__ import annotations

import time
from configparser import NoOptionError, NoSectionError
from typing import TYPE_CHECKING

from twisted.conch.openssh_compat import primes
from twisted.conch.ssh import factory, keys, transport
from twisted.logger import Logger

from cowrie.core.config import CowrieConfig
from cowrie.ssh import connection
from cowrie.ssh import keys as cowriekeys
from cowrie.ssh import transport as shellTransport
from cowrie.ssh.userauth import HoneyPotSSHUserAuthServer
from cowrie.ssh_proxy import server_transport as proxyTransport
from cowrie.ssh_proxy.userauth import ProxySSHAuthServer

if TYPE_CHECKING:
    from twisted.cred import portal as tp


class CowrieSSHFactory(factory.SSHFactory):
    """
    This factory creates HoneyPotSSHTransport instances
    They listen directly to the TCP port
    """

    _log = Logger()
    starttime: float | None = None
    privateKeys: dict[bytes, bytes]
    publicKeys: dict[bytes, bytes]
    primes: dict[int, list[tuple[int, int]]] | None = None
    portal: tp.Portal | None = None  # gets set by plugin
    ourVersionString: bytes

    def __init__(self, backend, pool_handler):
        self.pool_handler = pool_handler
        self.backend: str = backend
        self.privateKeys = {}
        self.publicKeys = {}
        self.services = {
            b"ssh-userauth": (
                ProxySSHAuthServer
                if self.backend == "proxy"
                else HoneyPotSSHUserAuthServer
            ),
            b"ssh-connection": connection.CowrieSSHConnection,
        }
        super().__init__()

    def startFactory(self) -> None:
        # For use by the uptime command
        self.starttime = time.time()

        # Load/create keys
        try:
            public_key_auth = [
                i.encode("utf-8")
                for i in CowrieConfig.get("ssh", "public_key_auth").split(",")
            ]
        except (NoOptionError, NoSectionError):
            # no keys defined, use the three most common pub keys of OpenSSH
            public_key_auth = [b"ssh-rsa", b"ecdsa-sha2-nistp256", b"ssh-ed25519"]
        for key in public_key_auth:
            if key == b"ssh-rsa":
                rsaPubKeyString, rsaPrivKeyString = cowriekeys.getRSAKeys()
                self.publicKeys[key] = keys.Key.fromString(data=rsaPubKeyString)
                self.privateKeys[key] = keys.Key.fromString(data=rsaPrivKeyString)
            elif key == b"ecdsa-sha2-nistp256":
                ecdsaPuKeyString, ecdsaPrivKeyString = cowriekeys.getECDSAKeys()
                self.publicKeys[key] = keys.Key.fromString(data=ecdsaPuKeyString)
                self.privateKeys[key] = keys.Key.fromString(data=ecdsaPrivKeyString)
            elif key == b"ssh-ed25519":
                ed25519PubKeyString, ed25519PrivKeyString = cowriekeys.geted25519Keys()
                self.publicKeys[key] = keys.Key.fromString(data=ed25519PubKeyString)
                self.privateKeys[key] = keys.Key.fromString(data=ed25519PrivKeyString)

        _modulis = "/etc/ssh/moduli", "/private/etc/moduli"
        for _moduli in _modulis:
            try:
                self.primes = primes.parseModuliFile(_moduli)
                break
            except OSError:
                pass

        # this can come from backend in the future, check HonSSH's slim client
        self.ourVersionString = CowrieConfig.get(
            "ssh", "version", fallback="SSH-2.0-OpenSSH_6.0p1 Debian-4+deb7u2"
        ).encode("ascii")

        factory.SSHFactory.startFactory(self)
        self._log.info("Ready to accept SSH connections")

    def stopFactory(self) -> None:
        factory.SSHFactory.stopFactory(self)

    def buildProtocol(self, addr):
        """
        Create an instance of the server side of the SSH protocol.

        @type addr: L{twisted.internet.interfaces.IAddress} provider
        @param addr: The address at which the server will listen.

        @rtype: L{cowrie.ssh.transport.HoneyPotSSHTransport}
        @return: The built transport.
        """
        t: transport.SSHServerTransport
        if self.backend == "proxy":
            t = proxyTransport.FrontendSSHTransport()
        else:
            t = shellTransport.HoneyPotSSHTransport()

        t.ourVersionString = self.ourVersionString
        t.supportedPublicKeys = list(self.privateKeys.keys())

        if not self.primes:
            ske = t.supportedKeyExchanges[:]
            if b"diffie-hellman-group-exchange-sha1" in ske:
                ske.remove(b"diffie-hellman-group-exchange-sha1")
                self._log.info("No moduli, no diffie-hellman-group-exchange-sha1")
            if b"diffie-hellman-group-exchange-sha256" in ske:
                ske.remove(b"diffie-hellman-group-exchange-sha256")
                self._log.info("No moduli, no diffie-hellman-group-exchange-sha256")
            t.supportedKeyExchanges = ske

        try:
            t.supportedCiphers = [
                i.encode("utf-8") for i in CowrieConfig.get("ssh", "ciphers").split(",")
            ]
        except (NoOptionError, NoSectionError):
            # Reorder supported ciphers to resemble current openssh more
            t.supportedCiphers = [
                b"aes128-ctr",
                b"aes192-ctr",
                b"aes256-ctr",
                b"aes256-cbc",
                b"aes192-cbc",
                b"aes128-cbc",
                b"3des-cbc",
                b"blowfish-cbc",
                b"cast128-cbc",
            ]

        try:
            t.supportedMACs = [
                i.encode("utf-8") for i in CowrieConfig.get("ssh", "macs").split(",")
            ]
        except (NoOptionError, NoSectionError):
            # SHA1 and MD5 are considered insecure now. Use better algos
            # like SHA-256 and SHA-384
            t.supportedMACs = [
                b"hmac-sha2-512",
                b"hmac-sha2-384",
                b"hmac-sha2-256",
                b"hmac-sha1",
                b"hmac-md5",
            ]

        try:
            t.supportedCompressions = [
                i.encode("utf-8")
                for i in CowrieConfig.get("ssh", "compression").split(",")
            ]
        except (NoOptionError, NoSectionError):
            t.supportedCompressions = [b"zlib@openssh.com", b"zlib", b"none"]

        t.factory = self

        return t
