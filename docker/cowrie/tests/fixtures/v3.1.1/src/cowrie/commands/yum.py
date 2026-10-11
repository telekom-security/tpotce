# SPDX-FileCopyrightText: 2018 FabiolaBusch
# SPDX-FileCopyrightText: 2009 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2020-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause


# Modified by Fabiola Buschendorf, https://github.com/FabiolaBusch


from __future__ import annotations

import hashlib
import random
import re
from typing import TYPE_CHECKING, Any

from twisted.internet import defer, reactor
from twisted.internet.defer import inlineCallbacks
from twisted.logger import Logger

from cowrie.shell.command import HoneyPotCommand

if TYPE_CHECKING:
    from collections.abc import Callable

arch = "x86_64"
commands = {}


class Command_faked_package_class_factory:
    @staticmethod
    def getCommand(name: str) -> Callable:
        class Command_faked_installation(HoneyPotCommand):
            def call(self) -> None:
                self.write(f"{name}: Segmentation fault\n")

        return Command_faked_installation


class Command_yum(HoneyPotCommand):
    """
    yum fake
    suppports only the 'install PACKAGE' command & 'moo'.
    Any installed packages, places a 'Segfault' at /usr/bin/PACKAGE.'''
    """

    _log = Logger()

    packages: dict[str, dict[str, Any]]

    @inlineCallbacks
    def start(self):
        self.packages = {}
        match self.args:
            case []:
                yield self.do_help()
            case ["version", *_]:
                yield self.do_version()
            case ["install", *_]:
                yield self.do_install()
            case _:
                self.do_locked()

    def sleep(self, time: float, time2: float | None = None) -> defer.Deferred:
        d: defer.Deferred = defer.Deferred()
        if time2:
            time = random.randint(int(time * 100), int(time2 * 100)) / 100.0
        reactor.callLater(time, d.callback, None)
        return d

    @inlineCallbacks
    def do_version(self):
        self.write(
            "Loaded plugins: changelog, kernel-module, ovl, priorities, tsflags, versionlock\n"
        )
        randnum = random.randint(100, 900)
        randnum2 = random.randint(100, 900)
        randhash = hashlib.sha1(f"{randnum}".encode()).hexdigest()
        randhash2 = hashlib.sha1(f"{randnum2}".encode()).hexdigest()
        yield self.sleep(1, 2)
        self.write(f"Installed: 7/{arch}  {random.randint(500, 800)}:{randhash}\n")
        self.write(f"Group-Installed: yum 13:{randhash2}\n")
        self.write("version\n")
        self.exit()

    @inlineCallbacks
    def do_help(self):
        yield self.sleep(1, 2)
        self.write(
            """Loaded plugins: changelog, kernel-module, ovl, priorities, tsflags, versionlock
You need to give some command
Usage: yum [options] COMMAND

List of Commands:

changelog      Display changelog data, since a specified time, on a group of packages
check          Check for problems in the rpmdb
check-update   Check for available package updates
clean          Remove cached data
deplist        List a package's dependencies
distribution-synchronization Synchronize installed packages to the latest available versions
downgrade      downgrade a package
erase          Remove a package or packages from your system
fs             Acts on the filesystem data of the host, mainly for removing docs/lanuages for minimal hosts.
fssnapshot     Creates filesystem snapshots, or lists/deletes current snapshots.
groups         Display, or use, the groups information
help           Display a helpful usage message
history        Display, or use, the transaction history
info           Display details about a package or group of packages
install        Install a package or packages on your system
list           List a package or groups of packages
load-transaction load a saved transaction from filename
makecache      Generate the metadata cache
provides       Find what package provides the given value
reinstall      reinstall a package
repo-pkgs      Treat a repo. as a group of packages, so we can install/remove all of them
repolist       Display the configured software repositories
search         Search package details for the given string
shell          Run an interactive yum shell
swap           Simple way to swap packages, instead of using shell
update         Update a package or packages on your system
update-minimal Works like upgrade, but goes to the 'newest' package match which fixes a problem that affects your system
updateinfo     Acts on repository update information
upgrade        Update packages taking obsoletes into account
version        Display a version for the machine and/or available repos.
versionlock    Control package version locks.


Options:
  -h, --help            show this help message and exit
  -t, --tolerant        be tolerant of errors
  -C, --cacheonly       run entirely from system cache, don't update cache
  -c [config file], --config=[config file]
                        config file location
  -R [minutes], --randomwait=[minutes]
                        maximum command wait time
  -d [debug level], --debuglevel=[debug level]
                        debugging output level
  --showduplicates      show duplicates, in repos, in list/search commands
  -e [error level], --errorlevel=[error level]
                        error output level
  --rpmverbosity=[debug level name]
                        debugging output level for rpm
  -q, --quiet           quiet operation
  -v, --verbose         verbose operation
  -y, --assumeyes       answer yes for all questions
  --assumeno            answer no for all questions
  --version             show Yum version and exit
  --installroot=[path]  set install root
  --enablerepo=[repo]   enable one or more repositories (wildcards allowed)
  --disablerepo=[repo]  disable one or more repositories (wildcards allowed)
  -x [package], --exclude=[package]
                        exclude package(s) by name or glob
  --disableexcludes=[repo]
                        disable exclude from main, for a repo or for
                        everything
  --disableincludes=[repo]
                        disable includepkgs for a repo or for everything
  --obsoletes           enable obsoletes processing during updates
  --noplugins           disable Yum plugins
  --nogpgcheck          disable gpg signature checking
  --disableplugin=[plugin]
                        disable plugins by name
  --enableplugin=[plugin]
                        enable plugins by name
  --skip-broken         skip packages with depsolving problems
  --color=COLOR         control whether color is used
  --releasever=RELEASEVER
                        set value of $releasever in yum config and repo files
  --downloadonly        don't update, just download
  --downloaddir=DLDIR   specifies an alternate directory to store packages
  --setopt=SETOPTS      set arbitrary config and repo options
  --bugfix              Include bugfix relevant packages, in updates
  --security            Include security relevant packages, in updates
  --advisory=ADVS, --advisories=ADVS
                        Include packages needed to fix the given advisory, in
                        updates
  --bzs=BZS             Include packages needed to fix the given BZ, in
                        updates
  --cves=CVES           Include packages needed to fix the given CVE, in
                        updates
  --sec-severity=SEVS, --secseverity=SEVS
                        Include security relevant packages matching the
                        severity, in updates
  --tsflags=TSFLAGS

  Plugin Options:
    --changelog         Show changelog delta of updated packages
    --samearch-priorities
                        Priority-exclude packages based on name + arch\n"""
        )
        self.exit()

    @inlineCallbacks
    def do_install(self, *args):
        if len(self.args) <= 1:
            yield self.sleep(1, 2)
            self.write(
                "Loaded plugins: changelog, kernel-module, ovl, priorities, tsflags, versionlock\n"
            )
            yield self.sleep(1, 2)
            self.write("Error: Need to pass a list of pkgs to install\n")
            self.write(" Mini usage:\n")
            self.write("install PACKAGE...\n")
            self.write("Install a package or packages on your system\n")
            self.write("aliases: install-n, install-na, install-nevra\n")
            self.exit()
            return

        for y in [re.sub("[^A-Za-z0-9]", "", x) for x in self.args[1:]]:
            self.packages[y] = {
                "version": f"{random.choice([0, 1])}.{random.randint(1, 40)}-{random.randint(1, 10)}",
                "size": random.randint(100, 900),
                "release": f"{random.randint(1, 15)}.el7",
            }
        totalsize: int = sum(self.packages[x]["size"] for x in self.packages)
        repository = "base"

        yield self.sleep(1)
        self.write(
            "Loaded plugins: changelog, kernel-module, ovl, priorities, tsflags, versionlock\n"
        )
        yield self.sleep(2.2)
        self.write(
            f"{random.randint(200, 300)} packages excluded due to repository priority protections\n"
        )
        yield self.sleep(0.9)
        self.write("Resolving Dependencies\n")
        self.write("--> Running transaction check\n")
        for p in self.packages:
            self.write(
                f"---> Package {p}.{self.packages[p]['version']} {arch}.{self.packages[p]['release']} will be installed\n"
            )
        self.write("--> Finished Dependency Resolution\n")
        self.write("Beginning Kernel Module Plugin\n")
        self.write("Finished Kernel Module Plugin\n\n")

        self.write("Dependencies Resolved\n\n")

        # TODO: Is this working on all screens?
        self.write(f"{'=' * 176}\n")
        # 195 characters
        self.write(" Package\t\t\tArch\t\t\tVersion\t\t\t\tRepository\t\t\tSize\n")
        self.write(f"{'=' * 176}\n")
        self.write("Installing:\n")
        for p in self.packages:
            self.write(
                f" {p}\t\t\t\t{arch}\t\t\t{self.packages[p]['version']}-{self.packages[p]['release']}\t\t\t{repository}\t\t\t\t{self.packages[p]['size']} k\n"
            )
        self.write("\n")
        self.write("Transaction Summary\n")
        self.write(f"{'=' * 176}\n")
        self.write(f"Install  {len(self.packages)} Packages\n\n")

        self.write(f"Total download size: {totalsize} k\n")
        self.write(f"Installed size: {0.0032 * totalsize:.1f} M\n")
        self.write("Is this ok [y/d/N]: ")
        # Assume 'yes'

    @inlineCallbacks
    def lineReceived(self, line):
        self._log.info("INPUT (yum): {line}", line=line)

        self.write("Downloading packages:\n")
        yield self.sleep(0.5, 1)
        self.write("Running transaction check\n")
        yield self.sleep(0.5, 1)
        self.write("Running transaction test\n")
        self.write("Transaction test succeeded\n")
        self.write("Running transaction\n")
        i = 1
        for p in self.packages:
            self.write(
                f"  Installing : {p}-{self.packages[p]['version']}-{self.packages[p]['release']}.{arch} \t\t\t\t {i}/{len(self.packages)} \n"
            )
            yield self.sleep(0.5, 1)
            i += 1
        i = 1
        for p in self.packages:
            self.write(
                f"  Verifying : {p}-{self.packages[p]['version']}-{self.packages[p]['release']}.{arch} \t\t\t\t {i}/{len(self.packages)} \n"
            )
            yield self.sleep(0.5, 1)
            i += 1
        self.write("\n")
        self.write("Installed:\n")
        for p in self.packages:
            self.write(
                f"  {p}.{arch} {random.randint(0, 2)}:{self.packages[p]['version']}-{self.packages[p]['release']} \t\t"
            )
        self.write("\n")
        self.write("Complete!\n")
        self.exit()

    def do_locked(self) -> None:
        self.errorWrite(
            "Loaded plugins: changelog, kernel-module, ovl, priorities, tsflags, versionlock\n"
        )
        self.errorWrite("ovl: Error while doing RPMdb copy-up:\n")
        self.errorWrite("[Errno 13] Permission denied: '/var/lib/rpm/.dbenv.lock' \n")
        self.errorWrite("You need to be root to perform this command.\n")
        self.exit()


commands["/usr/bin/yum"] = Command_yum
commands["yum"] = Command_yum
