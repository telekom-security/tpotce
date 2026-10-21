# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2015-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# Excerpt of Cowrie v3.1.3 (c7773f49), the text patch_cowrie_persona_support.py touches.

    def sendBanner(self):
        """
        This is the pre-login banner. The post-login banner is the MOTD file
        Display contents of <honeyfs>/etc/issue.net
        """
        if self.bannerSent:
            return
        self.bannerSent = True

        try:
            banner = read_honeyfs_bytes("etc/issue.net").decode(
                "utf-8", errors="replace"
            )
        except FileNotFoundError:
            self._log.failure("ERROR: Failed to load /etc/issue.net")
            return

        if not banner or not banner.strip():
            return

        self.transport.sendPacket(userauth.MSG_USERAUTH_BANNER, NS(banner) + NS(b"en"))

