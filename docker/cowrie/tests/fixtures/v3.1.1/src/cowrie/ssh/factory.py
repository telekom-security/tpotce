# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2016-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# Excerpt of Cowrie v3.1.1 (c17c9b73), the text check_ssh_support.py reads.

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

        try:
            t.supportedCompressions = [
                i.encode("utf-8")
                for i in CowrieConfig.get("ssh", "compression").split(",")
            ]
        except (NoOptionError, NoSectionError):
            t.supportedCompressions = [b"zlib@openssh.com", b"zlib", b"none"]
