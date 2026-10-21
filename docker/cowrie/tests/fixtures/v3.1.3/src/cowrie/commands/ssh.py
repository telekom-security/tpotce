# SPDX-FileCopyrightText: 2009-2012 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2014-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause


from __future__ import annotations

import getopt
import hashlib
import re
import socket
import time
from typing import TYPE_CHECKING

from twisted.logger import Logger

from cowrie.core.config import CowrieConfig
from cowrie.shell.command import HoneyPotCommand

# Excerpt of Cowrie v3.1.3 (c7773f49), the text patch_cowrie_persona_support.py touches.

            host = "localhost"
        self.protocol.hostname = host
        # The fake remote login lands the running shell in root's home there.
        self.shell.cwd = "/root" if self.fs.exists("/root") else "/"
        self.protocol.password_input = False
        self.write(
            f"Linux {self.protocol.hostname} 2.6.26-2-686 #1 SMP Wed Nov 4 20:45:37 \
            UTC 2009 i686\n"
        )
        self.write(f"Last login: {time.ctime(time.time() - 123123)} from 192.168.9.4\n")
        self.exit()
