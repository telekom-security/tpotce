# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2014-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import getopt
import posixpath
import time
from typing import Any
from urllib import parse

import treq
from twisted.internet import defer, error, reactor
from twisted.internet.defer import CancelledError, inlineCallbacks
from twisted.internet.protocol import ClientCreator, Protocol
from twisted.logger import Logger
from twisted.protocols.ftp import CommandFailed, FTPClient
from twisted.python import failure
from twisted.web.iweb import UNKNOWN_LENGTH

from cowrie.core.artifact import Artifact
from cowrie.core.config import CowrieConfig
from cowrie.core.download import (
    BlockedAddress,
    UnreachableAddress,
    capture_download,
    fetch,
    outbound_rate_limiter,
    report_download_failure,
)
from cowrie.core.network import (
    DownloadLimitExceeded,
    abort_body,
    communication_allowed,
    is_ip_address,
    outbound_bind_address,
)
from cowrie.shell.command import HoneyPotCommand

commands = {}

wget_rate_limiter = outbound_rate_limiter("wget")


def tdiff(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)  # 86400 = 24*60*60
    hours, remainder = divmod(remainder, 3600)  # 3600 = 60*60
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days >= 1:
        parts.append(f"{days}d")
    if hours >= 1:
        parts.append(f"{hours}h")
    if minutes >= 1:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")

    return " ".join(parts)


def sizeof_fmt(num: float) -> str:
    for x in ["bytes", "K", "M", "G", "T"]:
        if num < 1024.0:
            return f"{num}{x}"
        num /= 1024.0
    raise ValueError


# Luciano Ramalho @ http://code.activestate.com/recipes/498181/
def splitthousands(s: str, sep: str = ",") -> str:
    if len(s) <= 3:
        return s
    return splitthousands(s[:-3], sep) + sep + s[-3:]


class _FTPWgetReceiver(Protocol):
    """
    Receive FTP data and forward to wget collector
    """

    def __init__(self, command: Command_wget) -> None:
        self.command = command

    def dataReceived(self, data: bytes) -> None:
        self.command.collect(data)

    def connectionLost(self, reason=None):
        # Download completion handled by command callbacks
        return None


class Command_wget(HoneyPotCommand):
    """
    wget command
    """

    _log = Logger()

    limit_size: int = CowrieConfig.getint("honeypot", "download_limit_size", fallback=0)
    quiet: bool = False

    outfile: str | None = None  # outfile is the file saved inside the honeypot
    artifact: (
        Artifact  # artifact is the file saved for forensics in the real file system
    )
    currentlength: int = 0  # partial size during download
    totallength: int = 0  # total length
    proglen: int = 0
    wget_version: str = (
        "1.25.0"  # GNU wget version used in --help and User-Agent header
    )
    url: bytes
    host: str
    scheme: str
    port: int = 80
    ftp_client: FTPClient | None = None
    ftp_remote_dir: str | None = None
    ftp_remote_file: str | None = None
    started: float

    def print_usage_error(self, error_msg: str = "") -> None:
        """Print usage error message"""
        self.exit_code = 1
        if error_msg:
            self.errorWrite(f"wget: {error_msg}\n")
        self.errorWrite("Usage: wget [OPTION]... [URL]...\n\n")
        self.errorWrite("Try `wget --help' for more options.\n")

    def print_help_message(self) -> None:
        """
        Print GNU-style wget help message.

        Lists both implemented options (that actually work) and recognized options
        (that are silently ignored for compatibility).
        """

        self.write(
            f"GNU Wget {self.wget_version}, a non-interactive network retriever.\n"
        )
        self.write("Usage: wget [OPTION]... [URL]...\n\n")
        self.write("Startup:\n")
        self.write("  -h,  --help              print this help\n\n")
        self.write("Download:\n")
        self.write(
            "  -c,  --continue          resume getting a partially-downloaded file\n"
        )
        self.write("  -O                       write documents to FILE\n")
        self.write("  -q,  --quiet             quiet (no output)\n")
        self.write("  -T,  --timeout=SECONDS   set all timeout values to SECONDS\n")
        self.write("       --tries=NUMBER      set number of retries to NUMBER\n\n")
        self.write("HTTP options:\n")
        self.write("       --header=STRING     insert STRING among the headers\n")
        self.write(
            "       --post-data=STRING  use the POST method; send STRING as the data\n"
        )
        self.write(
            "       --max-redirect=NUM  maximum redirections allowed per page\n\n"
        )
        self.write("Directories:\n")
        self.write("  -P,  --directory-prefix=PREFIX  save files to PREFIX/..\n\n")
        self.write("Email bug reports, questions, discussions to <bug-wget@gnu.org>\n")
        self.write(
            "and/or open issues at https://savannah.gnu.org/bugs/?func=additem&group=wget.\n"
        )

    @inlineCallbacks
    def start(self):
        url: str
        try:
            optlist, args = getopt.getopt(
                self.args,
                "hcqO:TP:",
                [
                    "help",
                    "quiet",
                    "header=",
                    "max-redirect=",
                    "post-data",
                    "timeout=",
                    "tries=",
                ],
            )
        except getopt.GetoptError as err:
            self.print_usage_error(f"invalid option -- '{err.opt}'")
            self.exit()
            return

        # Handle help option first - print help and exit immediately
        for opt in optlist:
            if opt[0] in ["-h", "--help"]:
                self.print_help_message()
                self.exit()
                return

        if len(args):
            url = args[0].strip()
        else:
            self.print_usage_error("missing URL")
            self.exit()
            return

        self.outfile = None
        self.quiet = False
        for opt in optlist:
            if opt[0] == "-O":
                self.outfile = opt[1]
            elif opt[0] in ["-q", "--quiet"]:
                self.quiet = True

        # for some reason getopt doesn't recognize "-O -"
        # use try..except for the case if passed command is malformed
        try:
            if not self.outfile:
                if "-O" in args:
                    self.outfile = args[args.index("-O") + 1]
        except Exception:
            pass

        if "://" not in url:
            url = f"http://{url}"

        # urlparse() raises on malformed IPv6 brackets, and .port and .hostname
        # are parsed lazily and raise rather than returning None for a port
        # that is not a decimal 0-65535. The URL is attacker input.
        try:
            urldata = parse.urlparse(url)
            hostname = urldata.hostname
            port = urldata.port
        except ValueError:
            self.print_usage_error("invalid URL")
            self.exit()
            return

        self.scheme = (urldata.scheme or "http").lower()

        # self.host is required as it will be used as key in rate limiter from this point forward
        if hostname:
            self.host = hostname
        else:
            self.print_usage_error("invalid URL")
            self.exit()
            return

        # Determine self.port: use explicit port from URL, otherwise use scheme default
        if port is not None:
            self.port = port
        elif self.scheme == "https":
            self.port = 443
        elif self.scheme == "ftp":
            self.port = 21
        else:
            self.port = 80

        # Check rate limit before proceeding
        if not wget_rate_limiter.check(self.host):
            self._log.info(
                "wget: rate limit exceeded for host: {host}. Simulating connection timeout",
                host=self.host,
            )

            # Simulate connection timeout
            self.errorWrite(f"Connecting to {self.host}:{self.port}... ")
            self.errorWrite("failed: Connection timed out.\n")
            self.errorWrite("Retrying.\n\n")

            self.exit(1)
            return

        allowed = yield communication_allowed(self.host)
        if not allowed:
            self._log.info("Attempt to access blocked network address")
            if not self.quiet:
                tm = time.strftime("%Y-%m-%d %H:%M:%S")
                self.errorWrite(f"--{tm}--  {url}\n")
                self.errorWrite(
                    f"Resolving {self.host} ({self.host})... failed: Temporary failure in name resolution.\n"
                )
            self.errorWrite(f"wget: unable to resolve host address ‘{self.host}’\n")
            self.exit(1)
            return None

        self.url = url.encode("utf8")

        if self.outfile is None:
            self.outfile = urldata.path.split("/")[-1]
            if not len(self.outfile.strip()) or not urldata.path.count("/"):
                self.outfile = "index.html"
            self.outfile = self.fs.resolve_path(self.outfile, self.cwd)

        elif self.outfile != "-":
            self.outfile = self.fs.resolve_path(self.outfile, self.cwd)
            path = posixpath.dirname(self.outfile)
            if not path or not self.fs.exists(path) or not self.fs.isdir(path):
                self.errorWrite(
                    f"wget: {self.outfile}: Cannot open: No such file or directory\n"
                )
                self.exit(1)
                return

        self.artifact = Artifact("wget-download")

        if not self.quiet:
            tm = time.strftime("%Y-%m-%d %H:%M:%S")
            self.errorWrite(f"--{tm}--  {url}\n")
            self.errorWrite(f"Connecting to {self.host}:{self.port}... connected.\n")
            proto_label = (
                "HTTP" if self.scheme in ("http", "https") else self.scheme.upper()
            )
            self.errorWrite(f"{proto_label} request sent, awaiting response... ")

        # Starting the transfer can raise synchronously before any Deferred
        # exists: connectTCP rejects an FTP host it cannot build a connection
        # for. Route that raise through error() so the command exits instead of
        # being orphaned on the cmdstack until session timeout.
        try:
            if self.scheme == "ftp":
                self.deferred = self.ftpDownload(urldata)
                if self.deferred:
                    self.deferred.addErrback(self.error)
            else:
                self.deferred = self.httpDownload(url)
                if self.deferred:
                    self.deferred.addCallback(self.success)
                    self.deferred.addErrback(self.error)
        except Exception:
            self.error(failure.Failure())
            return

    def httpDownload(self, url: str) -> Any:
        """
        Download `url`
        """
        headers: dict[bytes | str, list[bytes | str]] = {
            "User-Agent": [f"Wget/{self.wget_version} (linux-gnu)"]
        }

        return fetch(reactor, url, headers=headers, timeout=10)

    def ftpDownload(self, urldata: parse.ParseResult) -> Any:
        """
        Download `url` via FTP
        """
        username = parse.unquote(urldata.username) if urldata.username else "anonymous"
        password = parse.unquote(urldata.password) if urldata.password else "busybox@"

        raw_path = urldata.path or ""
        if urldata.params:
            raw_path = f"{raw_path};{urldata.params}"
        remote_path = parse.unquote(raw_path)

        if not remote_path or remote_path.endswith("/"):
            self.errorWrite("wget: unsupported directory target in FTP URL\n")
            self.exit(1)
            return None

        self.ftp_remote_dir = posixpath.dirname(remote_path)
        self.ftp_remote_file = posixpath.basename(remote_path)

        if not self.ftp_remote_file:
            self.errorWrite("wget: missing remote filename in FTP URL\n")
            self.exit(1)
            return None

        ip = is_ip_address(self.host)
        if ip is not None and ip.version == 6:
            # The outbound source address is an IPv4 one, so an IPv6 server is
            # as unreachable here as it is on the HTTP path.
            self.errorWrite("failed: Network is unreachable.\n")
            self.exit(1)
            return None

        self.ftp_client = None

        creator = ClientCreator(reactor, FTPClient, username, password, passive=True)
        deferred = creator.connectTCP(
            self.host,
            self.port,
            timeout=30,
            bindAddress=(outbound_bind_address(), 0),
        )
        deferred.addCallback(self._ftp_connected)
        return deferred

    def _ftp_connected(self, client: FTPClient) -> defer.Deferred[Any]:
        """
        Handle established FTP connection
        """
        self.ftp_client = client

        if self.ftp_remote_dir and self.ftp_remote_dir not in (".", "./"):
            d: defer.Deferred[Any] = client.cwd(self.ftp_remote_dir)
        else:
            d = defer.succeed(None)

        d.addCallback(lambda _: self._ftp_start_retrieve())
        return d

    def _ftp_start_retrieve(self) -> defer.Deferred[Any]:
        """
        Start retrieving file over FTP
        """
        if not self._begin_download(None, "application/octet-stream", "200 OK"):
            if self.ftp_client:
                try:
                    quit_deferred = self.ftp_client.quit()
                    quit_deferred.addErrback(
                        lambda f: self._log.info(
                            "FTP quit failed during abort: {error}",
                            error=f.getErrorMessage(),
                        )
                    )
                except Exception as e:  # pragma: no cover - defensive
                    self._log.info(
                        "FTP quit raised exception during abort: {error}", error=e
                    )
                finally:
                    self.ftp_client = None
            return defer.succeed(None)

        if not self.ftp_client or not self.ftp_remote_file:
            return defer.fail(Exception("FTP client not connected"))

        receiver = _FTPWgetReceiver(self)
        deferred = self.ftp_client.retrieveFile(self.ftp_remote_file, receiver)
        deferred.addCallback(self._ftp_after_retrieve)
        return deferred  # type: ignore[no-any-return]

    def _ftp_after_retrieve(self, result: Any) -> Any:
        """
        Cleanup after FTP retrieval completes
        """
        if self.ftp_client:
            try:
                quit_deferred = self.ftp_client.quit()
                quit_deferred.addErrback(
                    lambda f: self._log.info(
                        "FTP quit failed: {error}", error=f.getErrorMessage()
                    )
                )
            except Exception as e:  # pragma: no cover - defensive
                self._log.info("FTP quit raised exception: {error}", error=e)
            finally:
                self.ftp_client = None

        self.collectioncomplete(None)
        return result

    def _begin_download(
        self, total_length: int | None, contenttype: str, status_line: str = "200 OK"
    ) -> bool:
        """
        Prepare transfer bookkeeping and display status
        """
        if total_length is not None:
            self.totallength = total_length
        else:
            self.totallength = 0

        if (
            total_length is not None
            and self.limit_size > 0
            and self.totallength > self.limit_size
        ):
            self._log.info(
                "Not saving URL ({url}) (size: {size}) exceeds file size limit ({limit})",
                url=self.url.decode(),
                size=self.totallength,
                limit=self.limit_size,
            )
            self.exit()
            return False

        self.currentlength = 0
        self.proglen = 0
        self.contenttype = contenttype
        self.speed = 0.0
        self.started = time.time()

        if not self.quiet:
            self.errorWrite(f"{status_line}\n")
            if total_length is not None:
                self.errorWrite(
                    f"Length: {self.totallength} ({sizeof_fmt(self.totallength)}) [{self.contenttype}]\n"
                )
            else:
                self.errorWrite(f"Length: unspecified [{self.contenttype}]\n")

            dest = (
                "STDOUT"
                if self.outfile is None or self.outfile == "-"
                else self.outfile
            )
            self.errorWrite(f"Saving to: `{dest}'\n\n")

        return True

    def handle_CTRL_C(self) -> None:
        self.write("^C\n")
        self.exit(130)  # 128 + SIGINT

    def exit(self, code: int | None = None) -> None:
        # Close the download artifact on every exit path so an aborted download
        # (CTRL-C, size limit, ...) does not leave its empty temp file behind in
        # the download directory. close() is idempotent and removes an empty
        # artifact, so the normal success/error paths are unaffected.
        artifact = getattr(self, "artifact", None)
        if artifact is not None:
            artifact.close()
        HoneyPotCommand.exit(self, code)

    def success(self, response):
        """
        successful treq get
        """
        # Response metadata comes from an attacker-directed server and need
        # not be valid UTF-8.
        if response.headers.hasHeader(b"content-type"):
            contenttype = response.headers.getRawHeaders(b"content-type")[0].decode(
                errors="replace"
            )
        else:
            contenttype = "text/whatever"

        status_line = "200 OK"
        code = getattr(response, "code", None)
        phrase = getattr(response, "phrase", None)
        if code is not None:
            if phrase:
                if isinstance(phrase, bytes):
                    phrase = phrase.decode(errors="replace")
                status_line = f"{code} {phrase}"
            else:
                status_line = str(code)

        total_length: int | None
        if response.length != UNKNOWN_LENGTH:
            total_length = response.length
        else:
            total_length = None

        if not self._begin_download(total_length, contenttype, status_line):
            # Stop the transfer; an undelivered body would otherwise keep
            # downloading and buffering in memory.
            abort_body(response)
            return None

        deferred = treq.collect(response, self.collect)
        deferred.addCallback(self.collectioncomplete)
        return deferred

    def collect(self, data: bytes) -> None:
        """
        partial collect
        """
        if self.exited:
            # The command exited while the transfer was still in flight (size
            # limit, CTRL-C); drop late chunks instead of writing to the
            # closed artifact.
            return
        eta: float
        self.currentlength += len(data)
        if self.limit_size > 0 and self.currentlength > self.limit_size:
            self._log.info(
                "Not saving URL ({url}) (size: {size}) exceeds file size limit ({limit})",
                url=self.url.decode(),
                size=self.currentlength,
                limit=self.limit_size,
            )
            self.exit()
            # treq closes the connection when the collector raises, aborting
            # the transfer instead of draining the rest of the body. The
            # errback this triggers is inert because the command has exited.
            raise DownloadLimitExceeded

        self.artifact.write(data)

        # Clamp elapsed to 1 microsecond: on a localhost transfer the first
        # chunk can arrive within the same time.time() tick as self.started,
        # which would divide by zero. 1e-6 is below any real network RTT, so it
        # does not distort the reported speed on genuine connections.
        elapsed = max(time.time() - self.started, 1e-6)
        self.speed = self.currentlength / elapsed
        if self.totallength != 0:
            percent = int(self.currentlength / self.totallength * 100)
            spercent = f"{percent}%"
            eta = (self.totallength - self.currentlength) / self.speed
        else:
            spercent = f"{self.currentlength / 1000:3.0f}K"
            percent = 0
            eta = 0.0

        s = "\r{} [{}] {} {:3.1f}K/s  eta {}".format(
            spercent.rjust(3),
            ("%s>" % (int(39.0 / 100.0 * percent) * "=")).ljust(39),
            splitthousands(str(int(self.currentlength))).ljust(12),
            self.speed / 1000,
            tdiff(int(eta)),
        )
        if not self.quiet:
            self.errorWrite(s.ljust(self.proglen))
        self.proglen = len(s)
        self.lastupdate = time.time()

        if not self.outfile or self.outfile == "-":
            self.writeBytes(data)

    def collectioncomplete(self, data: None) -> None:
        """
        this gets called once collection is complete
        """
        if self.exited:
            # The command exited while the transfer was still in flight; the
            # partial artifact was already handled by exit(), so don't report
            # a download for a dead command or exit again.
            return
        self.artifact.close()

        self.totallength = self.currentlength

        if not self.quiet:
            self.errorWrite(
                "\r100% [{}] {} {:3.1f}K/s".format(
                    "%s>" % (38 * "="),
                    splitthousands(str(int(self.totallength))).ljust(12),
                    self.speed / 1000,
                )
            )
            self.errorWrite("\n\n")
            dest = (
                "stdout"
                if self.outfile is None or self.outfile == "-"
                else self.outfile
            )
            self.errorWrite(
                "{} ({:3.2f} KB/s) - `{}' saved [{}/{}]\n\n".format(
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    self.speed / 1000,
                    dest,
                    self.currentlength,
                    self.totallength,
                )
            )

        capture_download(
            self,
            self.artifact,
            self.url.decode(),
            outfile=None if self.outfile == "-" else self.outfile,
            size=self.currentlength,
        )
        self.exit()

    def error(self, response):
        """
        handle errors
        """
        if self.exited:
            # The command exited while the transfer was still in flight; a
            # late failure of that transfer is not this command's outcome.
            return
        self.exit_code = 1
        report_download_failure(self, self.url.decode())

        if response.check(BlockedAddress) is not None:
            # A redirect reached a host the honeypot must not contact.
            self.errorWrite(
                f"wget: unable to resolve host address ‘{response.value.host}’\n"
            )
            self.exit()
            return

        if response.check(UnreachableAddress) is not None:
            self.errorWrite("failed: Network is unreachable.\n")
            self.exit()
            return

        if response.check(error.DNSLookupError) is not None:
            self.errorWrite(
                f"Resolving no.such ({self.host})... failed: Temporary failure in name resolution.\n"
            )
            self.errorWrite(f"wget: unable to resolve host address ‘{self.host}’\n")
            self.exit()
            return

        if response.check(CancelledError) is not None:
            self.errorWrite("failed: Operation timed out.\n")
            self.exit()
            return

        if response.check(CommandFailed) is not None:
            details = ""
            value = getattr(response, "value", None)
            if value and getattr(value, "args", None):
                details = value.args[0]
            self.errorWrite(f"wget: FTP error: {details}\n")
            self.exit()
            return

        if response.check(error.ConnectingCancelledError) is not None:
            self.errorWrite("cancel failed: Operation timed out.\n")
            self.exit()
            return

        if response.check(error.ConnectionDone) is not None:
            self.errorWrite("No data received.\n")
            self.exit()
            return

        if response.check(error.TCPTimedOutError) is not None:
            self.errorWrite("failed: Connection timed out.\n")
            self.exit()
            return

        self._log.failure("Unhandled wget error", failure=response)
        self.errorWrite("\n")
        self.exit()


commands["/usr/bin/wget"] = Command_wget
commands["wget"] = Command_wget
commands["/usr/bin/dget"] = Command_wget
commands["dget"] = Command_wget
