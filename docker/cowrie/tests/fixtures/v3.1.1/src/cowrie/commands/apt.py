# SPDX-FileCopyrightText: 2009-2010 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2015-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause


from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING, Any

from twisted.internet import defer, reactor
from twisted.internet.defer import inlineCallbacks

from cowrie.shell.command import HoneyPotCommand

if TYPE_CHECKING:
    from collections.abc import Callable

commands = {}


class Command_faked_package_class_factory:
    @staticmethod
    def getCommand(name: str) -> Callable:
        class Command_faked_installation(HoneyPotCommand):
            def call(self) -> None:
                self.write(f"{name}: Segmentation fault\n")

        return Command_faked_installation


class Command_aptget(HoneyPotCommand):
    """
    apt-get fake
    suppports only the 'install PACKAGE' command & 'moo'.
    Any installed packages, places a 'Segfault' at /usr/bin/PACKAGE.'''
    """

    packages: dict[str, dict[str, Any]]

    @inlineCallbacks
    def start(self):
        self.packages = {}
        if len(self.args) == 0:
            self.do_help()
        elif self.args[0] == "-v":
            self.do_version()
        elif self.args[0] == "install":
            yield self.do_install()
        elif self.args[0] == "moo":
            self.do_moo()
        else:
            self.do_locked()

    def sleep(self, time: float, time2: float | None = None) -> defer.Deferred:
        d: defer.Deferred = defer.Deferred()
        if time2:
            time = random.randint(int(time * 100), int(time2 * 100.0)) / 100.0
        reactor.callLater(time, d.callback, None)
        return d

    def do_version(self) -> None:
        self.write(
            """apt 1.0.9.8.1 for amd64 compiled on Jun 10 2015 09:42:06
Supported modules:
*Ver: Standard .deb
*Pkg:  Debian dpkg interface (Priority 30)
 Pkg:  Debian APT solver interface (Priority -1000)
 S.L: 'deb' Standard Debian binary tree
 S.L: 'deb-src' Standard Debian source tree
 Idx: Debian Source Index
 Idx: Debian Package Index
 Idx: Debian Translation Index
 Idx: Debian dpkg status file
 Idx: EDSP scenario file\n"""
        )
        self.exit()

    def do_help(self) -> None:
        self.write(
            """apt 1.0.9.8.1 for amd64 compiled on Jun 10 2015 09:42:06
Usage: apt-get [options] command
       apt-get [options] install|remove pkg1 [pkg2 ...]
       apt-get [options] source pkg1 [pkg2 ...]

apt-get is a simple command line interface for downloading and
installing packages. The most frequently used commands are update
and install.

Commands:
   update - Retrieve new lists of packages
   upgrade - Perform an upgrade
   install - Install new packages (pkg is libc6 not libc6.deb)
   remove - Remove packages
   autoremove - Remove automatically all unused packages
   purge - Remove packages and config files
   source - Download source archives
   build-dep - Configure build-dependencies for source packages
   dist-upgrade - Distribution upgrade, see apt-get(8)
   dselect-upgrade - Follow dselect selections
   clean - Erase downloaded archive files
   autoclean - Erase old downloaded archive files
   check - Verify that there are no broken dependencies
   changelog - Download and display the changelog for the given package
   download - Download the binary package into the current directory

Options:
  -h  This help text.
  -q  Loggable output - no progress indicator
  -qq No output except for errors
  -d  Download only - do NOT install or unpack archives
  -s  No-act. Perform ordering simulation
  -y  Assume Yes to all queries and do not prompt
  -f  Attempt to correct a system with broken dependencies in place
  -m  Attempt to continue if archives are unlocatable
  -u  Show a list of upgraded packages as well
  -b  Build the source package after fetching it
  -V  Show verbose version numbers
  -c=? Read this configuration file
  -o=? Set an arbitrary configuration option, eg -o dir::cache=/tmp
See the apt-get(8), sources.list(5) and apt.conf(5) manual
pages for more information and options.
                       This APT has Super Cow Powers.\n"""
        )
        self.exit()

    @inlineCallbacks
    def do_install(self, *args):
        if len(self.args) <= 1:
            self.write(
                f"0 upgraded, 0 newly installed, 0 to remove and {random.randint(200, 300)} not upgraded.\n"
            )
            self.exit()
            return

        for y in [re.sub("[^A-Za-z0-9]", "", x) for x in self.args[1:]]:
            self.packages[y] = {
                "version": f"{random.choice([0, 1])}.{random.randint(1, 40)}-{random.randint(1, 10)}",
                "size": random.randint(100, 900),
            }
        totalsize: int = sum(self.packages[x]["size"] for x in self.packages)

        self.write("Reading package lists... Done\n")
        self.write("Building dependency tree\n")
        self.write("Reading state information... Done\n")
        self.write("The following NEW packages will be installed:\n")
        self.write(f"  {' '.join(self.packages)} \n")
        self.write(
            f"0 upgraded, {len(self.packages)} newly installed, 0 to remove and 259 not upgraded.\n"
        )
        self.write(f"Need to get {totalsize}.2kB of archives.\n")
        self.write(
            f"After this operation, {totalsize * 2.2:.1f}kB of additional disk space will be used.\n"
        )
        i = 1
        for p in self.packages:
            self.write(
                f"Get:{i} http://ftp.debian.org stable/main {p} {self.packages[p]['version']} [{self.packages[p]['size']}.2kB]\n"
            )
            i += 1
            yield self.sleep(1, 2)
        self.write(f"Fetched {totalsize}.2kB in 1s (4493B/s)\n")
        self.write("Reading package fields... Done\n")
        yield self.sleep(1, 2)
        self.write("Reading package status... Done\n")
        self.write(
            "(Reading database ... 177887 files and directories currently installed.)\n"
        )
        yield self.sleep(1, 2)
        for p in self.packages:
            self.write(
                f"Unpacking {p} (from .../archives/{p}_{self.packages[p]['version']}_i386.deb) ...\n"
            )
            yield self.sleep(1, 2)
        self.write("Processing triggers for man-db ...\n")
        yield self.sleep(2)
        for p in self.packages:
            self.write(f"Setting up {p} ({self.packages[p]['version']}) ...\n")
            self.fs.mkfile(
                f"/usr/bin/{p}",
                self.user["uid"],
                self.user["gid"],
                random.randint(10000, 90000),
                33188,
            )
            self.protocol.commands[f"/usr/bin/{p}"] = (
                Command_faked_package_class_factory.getCommand(p)
            )
            yield self.sleep(2)
        self.exit()

    def do_moo(self) -> None:
        self.write("         (__)\n")
        self.write("         (oo)\n")
        self.write("   /------\\/\n")
        self.write("  / |    ||\n")
        self.write(" *  /\\---/\\ \n")
        self.write("    ~~   ~~\n")
        self.write('...."Have you mooed today?"...\n')
        self.exit()

    def do_locked(self) -> None:
        self.errorWrite(
            "E: Could not open lock file /var/lib/apt/lists/lock - open (13: Permission denied)\n"
        )
        self.errorWrite("E: Unable to lock the list directory\n")
        self.exit()


commands["/usr/bin/apt-get"] = Command_aptget
commands["/bin/apt-get"] = Command_aptget
commands["apt-get"] = Command_aptget
commands["/usr/bin/apt"] = Command_aptget
commands["/bin/apt"] = Command_aptget
commands["apt"] = Command_aptget
