# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2015-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import stat

from twisted.conch.interfaces import ISession
from twisted.internet.protocol import connectionDone
from zope.interface import implementer

from cowrie.insults import insults
from cowrie.shell import protocol


class ProtocolTransport:
    """Adapt a terminal protocol so the SSH session can drive it as its
    process transport, firing the protocol's connectionLost exactly once.

    The session machinery calls loseConnection more than once per close
    (from both SSHSession.loseConnection and SSHSession.closed), so the close
    is guarded to deliver connectionLost a single time.
    """

    def __init__(self, proto):
        self.proto = proto
        self._lost = False

    def dataReceived(self, data: bytes) -> None:
        self.proto.transport.write(data)

    def write(self, data: bytes) -> None:
        self.proto.dataReceived(data)

    def writeSequence(self, seq: list[bytes]) -> None:
        self.write(b"".join(seq))

    def loseConnection(self) -> None:
        if self._lost:
            return
        self._lost = True
        self.proto.connectionLost(connectionDone)


@implementer(ISession)
class SSHSessionForCowrieUser:
    def __init__(self, avatar, reactor=None):
        """
        Construct an C{SSHSessionForCowrieUser}.

        @param avatar: The L{CowrieUser} for whom this is an SSH session.
        @param reactor: An L{IReactorProcess} used to handle shell and exec
            requests. Uses the default reactor if None.
        """
        self.protocol = None
        self.avatar = avatar
        self.server = avatar.server
        self.uid = avatar.uid
        self.gid = avatar.gid
        self.username = avatar.username
        self.environ = {
            "HOME": self.avatar.home,
            "LOGNAME": self.username,
            "SHELL": "/bin/bash",
            "SHLVL": "1",
            "TMOUT": "1800",
            "UID": str(self.uid),
            "USER": self.username,
        }
        if self.uid == 0:
            self.environ["PATH"] = (
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            )
        else:
            self.environ["PATH"] = (
                "/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games"
            )

        self.server.initFileSystem(self.avatar.home)

        if self.avatar.temporary:
            self.server.fs.mkdir(
                self.avatar.home, self.uid, self.gid, 4096, stat.S_IFDIR | 0o755
            )

    def openShell(self, processprotocol):
        self.protocol = insults.LoggingServerProtocol(
            protocol.HoneyPotInteractiveProtocol, self
        )
        self.protocol.makeConnection(processprotocol)
        processprotocol.makeConnection(ProtocolTransport(self.protocol))

    def getPty(self, terminal, windowSize, attrs):
        # The terminal type is attacker input from the pty request and need
        # not be valid UTF-8.
        self.environ["TERM"] = terminal.decode("utf-8", errors="replace")
        self.avatar.conn.transport.events.dispatch(
            "cowrie.client.size",
            "Terminal Size: %(width)s %(height)s",
            width=windowSize[1],
            height=windowSize[0],
        )
        self.windowSize = windowSize

    def execCommand(self, processprotocol, cmd):
        self.protocol = insults.LoggingServerProtocol(
            protocol.HoneyPotExecProtocol, self, cmd
        )
        self.protocol.makeConnection(processprotocol)
        processprotocol.makeConnection(ProtocolTransport(self.protocol))

    def closed(self) -> None:
        """
        Reliably called on both logout and disconnect. The protocol's
        connectionLost is delivered by the session transport, so here we only
        drop our reference to it.
        """
        self.protocol = None

    def eofReceived(self) -> None:
        if self.protocol:
            self.protocol.eofReceived()

    def windowChanged(self, windowSize):
        self.windowSize = windowSize
