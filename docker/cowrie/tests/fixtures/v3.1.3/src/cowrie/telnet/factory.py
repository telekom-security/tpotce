# SPDX-FileCopyrightText: 2019 Guilherme Borges <guilhermerosasborges@gmail.com>
# SPDX-FileCopyrightText: 2015, 2016 GoSecure Inc.
# SPDX-FileCopyrightText: 2019-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# Excerpt of Cowrie v3.1.3 (c7773f49), the text patch_cowrie_persona_support.py touches.

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from twisted.internet import protocol
from twisted.logger import Logger

from cowrie.core.honeyfs import read_honeyfs_bytes
from cowrie.telnet.transport import CowrieTelnetTransport
from cowrie.telnet.userauth import HoneyPotTelnetAuthProtocol
from cowrie.telnet_proxy.server_transport import FrontendTelnetTransport

if TYPE_CHECKING:
    from twisted.cred import portal as tp
    from twisted.plugin import IPlugin


class HoneyPotTelnetFactory(protocol.ServerFactory):
    """
    This factory creates HoneyPotTelnetAuthProtocol instances
    They listen directly to the TCP port
    """

    _log = Logger()

    tac: IPlugin
    banner: bytes
    starttime: float

    def __init__(self, backend, pool_handler):
        self.portal: tp.Portal | None = None  # gets set by Twisted plugin
        self.backend: str = backend
        self.pool_handler = pool_handler
        super().__init__()

    def startFactory(self) -> None:
        """ """
        try:
            self.banner = (
                read_honeyfs_bytes("etc/issue.net")
                .decode("utf-8", errors="replace")
                .encode("utf-8")
            )
        except FileNotFoundError:
            self._log.failure("ERROR: Failed to load /etc/issue.net")
            self.banner = b""

