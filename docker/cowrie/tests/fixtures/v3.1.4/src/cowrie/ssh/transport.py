# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2015-2025 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# Excerpt of Cowrie v3.1.4 (085ed091): the text check_ssh_support.py reads, the methods the
# server-sig-algs patch (patch_ssh_sig_algs) adds its method before, sendPacket (Cowrie's own, which the
# ext-info-s cut of the same patch starts) and connectionMade, which the patch for a persona without an
# SSH server (patch_ssh_drop) changes.

    def connectionMade(self) -> None:
        """
        Called when the connection is made from the other side.
        We send our version, but wait with sending KEXINIT
        """
        self.buf = b""

        self.transportId = uuid.uuid4().hex[:12]

        if isinstance(self.transport, ProtocolWrapper):
            # A protocol wrapper in front of us (the haproxy: endpoint's PROXY
            # parser) only resolves the real client address once it has read
            # the header, which happens on the first dataReceived(). Defer
            # cowrie.session.connect until then so it carries the real IP
            # rather than the proxy's.
            self._emit_connect_pending = True
        else:
            self._emit_connect()

        self.transport.write(self.ourVersionString + b"\r\n")
        self.currentEncryptions = transport.SSHCiphers(
            b"none", b"none", b"none", b"none"
        )
        self.currentEncryptions.setKeys(b"", b"", b"", b"", b"", b"")

        self.startTime = time.time()
        self.setTimeout(self.auth_timeout)

    def setService(self, service):
        """
        Remove login grace timeout, set zlib compression after auth
        """
        # Reset timeout. Not everyone opens shell so need timeout at transport level
        if service.name == b"ssh-connection":
            self.setTimeout(self.interactive_timeout)

        # when auth is successful we enable compression
        # this is called right after MSG_USERAUTH_SUCCESS
        if service.name == b"ssh-connection":
            if self.outgoingCompressionType == b"zlib@openssh.com":
                self.outgoingCompression = zlib.compressobj(6)
            if self.incomingCompressionType == b"zlib@openssh.com":
                self.incomingCompression = zlib.decompressobj()

        transport.SSHServerTransport.setService(self, service)


    def sendKexInit(self) -> None:
        """
        Don't send key exchange prematurely
        """
        if not self.gotVersion:
            return
        # A client that sends a second KEXINIT before the first key exchange
        # completes drives the base sendKexInit into raising a RuntimeError.
        # Disconnect such a protocol violation cleanly instead.
        if self._keyExchangeState != self._KEY_EXCHANGE_NONE:
            self._log.info("Duplicate KEXINIT during key exchange, disconnecting")
            self.transport.loseConnection()
            return
        transport.SSHServerTransport.sendKexInit(self)

    def _unsupportedVersionReceived(self, remoteVersion: bytes) -> None:
        """
        Change message to be like OpenSSH
        """
        self.transport.write(b"Protocol major versions differ.\n")
        self.transport.loseConnection()

    def sendPacket(self, messageType: int, payload: bytes) -> None:
        """
        Override because OpenSSH pads with 0 on KEXINIT
        """
        if self.transport is None:
            return
        if self._keyExchangeState != self._KEY_EXCHANGE_NONE:
            if not self._allowedKeyExchangeMessageType(messageType):
                self._blockedByKeyExchange.append((messageType, payload))
                return

        payload = bytes((messageType,)) + payload
        if self.outgoingCompression:
            payload = self.outgoingCompression.compress(
                payload
            ) + self.outgoingCompression.flush(2)
        bs = self.currentEncryptions.encBlockSize
        # 4 for the packet length and 1 for the padding length
        totalSize = 5 + len(payload)
        lenPad = bs - (totalSize % bs)
        if lenPad < 4:
            lenPad = lenPad + bs
        padding: bytes
        if messageType == transport.MSG_KEXINIT:
            padding = b"\0" * lenPad
        else:
            padding = randbytes.secureRandom(lenPad)

        packet = struct.pack(b"!LB", totalSize + lenPad - 4, lenPad) + payload + padding
        encPacket = self.currentEncryptions.encrypt(
            packet
        ) + self.currentEncryptions.makeMAC(self.outgoingPacketSequence, packet)
        self.transport.write(encPacket)
        self.outgoingPacketSequence += 1
