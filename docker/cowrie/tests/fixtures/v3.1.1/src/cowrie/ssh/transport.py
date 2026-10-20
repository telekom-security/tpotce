# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2015-2025 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# Excerpt of Cowrie v3.1.1 (c17c9b73): the text check_ssh_support.py reads and the methods the
# server-sig-algs patch (patch_ssh_sig_algs) adds its method before.

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
