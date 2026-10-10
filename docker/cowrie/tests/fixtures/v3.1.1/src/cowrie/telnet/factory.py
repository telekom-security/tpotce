# SPDX-FileCopyrightText: 2019 Guilherme Borges <guilhermerosasborges@gmail.com>
# SPDX-FileCopyrightText: 2015, 2016 GoSecure Inc.
# SPDX-FileCopyrightText: 2019-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# Excerpt of Cowrie v3.1.1 (c17c9b73), the text patch_cowrie_persona_support.py touches.

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

