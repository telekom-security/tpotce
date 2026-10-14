# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2015-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

"""
This module contains ...
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from twisted.conch import error
from twisted.conch.ssh import keys
from twisted.cred.checkers import ICredentialsChecker
from twisted.cred.credentials import ISSHPrivateKey
from twisted.cred.error import UnauthorizedLogin, UnhandledCredentials
from twisted.internet import defer
from twisted.logger import Logger
from twisted.python import failure
from zope.interface import implementer

from cowrie.core import auth
from cowrie.core import credentials as conchcredentials
from cowrie.core.config import CowrieConfig
from cowrie.core.utils import escape_nonprintable

if TYPE_CHECKING:
    from cowrie.core.events import EventLog


@implementer(ICredentialsChecker)
class HoneypotPublicKeyChecker:
    """
    Checker that accepts, logs and denies public key authentication attempts
    """

    credentialInterfaces = (ISSHPrivateKey,)

    def requestAvatarId(self, credentials):
        _pubKey = keys.Key.fromString(credentials.blob)
        # Twisted constructs this credential; the userauth service attaches
        # the transport's emitter before portal.login.
        events = getattr(credentials, "events", None)
        # Every pubkey event carries the same attacker key material.
        keyfields = {
            "username": escape_nonprintable(credentials.username),
            "fingerprint": _pubKey.fingerprint(),
            "key": _pubKey.toString("OPENSSH"),
            "type": _pubKey.sshType(),
        }
        if events:
            events.dispatch(
                "cowrie.client.fingerprint",
                "public key attempt for user %(username)s of type %(type)s with fingerprint %(fingerprint)s",
                **keyfields,
            )

        if CowrieConfig.getboolean("ssh", "auth_publickey_allow_any", fallback=False):
            if events:
                events.dispatch(
                    "cowrie.login.success",
                    "public key login attempt for [%(username)s] succeeded",
                    **keyfields,
                )
            return defer.succeed(credentials.username)
        else:
            if events:
                events.dispatch(
                    "cowrie.login.failed",
                    "public key login attempt for [%(username)s] failed",
                    **keyfields,
                )
            return failure.Failure(error.ConchError("Incorrect signature"))


@implementer(ICredentialsChecker)
class HoneypotNoneChecker:
    """
    Checker that does no authentication check
    """

    credentialInterfaces = (conchcredentials.IUsername,)

    def requestAvatarId(self, credentials):
        if credentials.events:
            credentials.events.dispatch(
                "cowrie.login.success",
                "login attempt [%(username)s] succeeded",
                username=escape_nonprintable(credentials.username),
            )
        return defer.succeed(credentials.username)


@implementer(ICredentialsChecker)
class HoneypotPasswordChecker:
    """
    Checker that accepts "keyboard-interactive" and "password"
    """

    _log = Logger()

    credentialInterfaces = (
        conchcredentials.IUsernamePasswordIP,
        conchcredentials.IPluggableAuthenticationModulesIP,
    )

    def requestAvatarId(self, credentials):
        if hasattr(credentials, "password"):
            if self.checkUserPass(
                credentials.username,
                credentials.password,
                credentials.ip,
                credentials.events,
            ):
                return defer.succeed(credentials.username)
            return defer.fail(UnauthorizedLogin())
        if hasattr(credentials, "pamConversion"):
            return self.checkPamUser(
                credentials.username,
                credentials.pamConversion,
                credentials.ip,
                credentials.events,
            )
        return defer.fail(UnhandledCredentials())

    def checkPamUser(self, username, pamConversion, ip, events):
        r = pamConversion((("Password:", 1),))
        return r.addCallback(self.cbCheckPamUser, username, ip, events)

    def cbCheckPamUser(self, responses, username, ip, events):
        for response, _ in responses:
            if self.checkUserPass(username, response, ip, events):
                return defer.succeed(username)
        return defer.fail(UnauthorizedLogin())

    def checkUserPass(
        self,
        theusername: bytes,
        thepassword: bytes,
        ip: str,
        events: EventLog | None,
    ) -> bool:
        # Is the auth_class defined in the config file?
        authclass = CowrieConfig.get("honeypot", "auth_class", fallback="UserDB")

        # Check if authclass exists in the auth module, fall back to UserDB
        if hasattr(auth, authclass):
            authname = getattr(auth, authclass)
        else:
            self._log.info(
                "auth_class: {authclass} not found in cowrie.core.auth, using UserDB",
                authclass=authclass,
            )
            authname = auth.UserDB

        theauth = authname()

        if theauth.checklogin(theusername, thepassword, ip):
            if events:
                events.dispatch(
                    "cowrie.login.success",
                    "login attempt [%(username)s/%(password)s] succeeded",
                    username=escape_nonprintable(theusername),
                    password=escape_nonprintable(thepassword),
                )
            return True

        if events:
            events.dispatch(
                "cowrie.login.failed",
                "login attempt [%(username)s/%(password)s] failed",
                username=escape_nonprintable(theusername),
                password=escape_nonprintable(thepassword),
            )
        return False
