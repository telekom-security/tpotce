# SPDX-FileCopyrightText: 2016 Γιάννης Παπαϊωάννου <giannispapcod7@gmail.com>
# SPDX-FileCopyrightText: 2016-2023 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

"""
This module contains the service commnad
"""

from __future__ import annotations

import getopt

from cowrie.shell.command import HoneyPotCommand

commands = {}


class Command_service(HoneyPotCommand):
    """
    By Giannis Papaioannou <giannispapcod7@gmail.com>
    """

    def status_all(self) -> None:
        """
        more services can be added here.
        """
        output = (
            "[ + ]  acpid",
            "[ - ]  alsa-utils",
            "[ + ]  anacron",
            "[ + ]  apparmor",
            "[ + ]  apport",
            "[ + ]  avahi-daemon",
            "[ + ]  bluetooth",
            "[ - ]  bootmisc.sh",
            "[ - ]  brltty",
            "[ - ]  checkfs.sh",
            "[ - ]  checkroot-bootclean.sh",
            "[ - ]  checkroot.sh",
            "[ + ]  console-setup",
            "[ + ]  cron",
            "[ + ]  cups",
            "[ + ]  cups-browsed",
            "[ + ]  dbus",
            "[ - ]  dns-clean",
            "[ + ]  grub-common",
            "[ - ]  hostname.sh",
            "[ - ]  hwclock.sh",
            "[ + ]  irqbalance",
            "[ - ]  kerneloops",
            "[ - ]  killprocs",
            "[ + ]  kmod",
            "[ + ]  lightdm",
            "[ - ]  mountall-bootclean.sh",
            "[ - ]  mountall.sh",
            "[ - ]  mountdevsubfs.sh",
            "[ - ]  mountkernfs.sh",
            "[ - ]  mountnfs-bootclean.sh",
            "[ - ]  mountnfs.sh",
            "[ + ]  network-manager",
            "[ + ]  networking",
            "[ + ]  ondemand",
            "[ + ]  open-vm-tools",
            "[ - ]  plymouth",
            "[ - ]  plymouth-log",
            "[ - ]  pppd-dns",
            "[ + ]  procps",
            "[ - ]  rc.local",
            "[ + ]  resolvconf",
            "[ - ]  rsync",
            "[ + ]  rsyslog",
            "[ - ]  saned",
            "[ - ]  sendsigs",
            "[ + ]  speech-dispatcher",
            "[ + ]  thermald",
            "[ + ]  udev",
            "[ + ]  ufw",
            "[ - ]  umountfs",
            "[ - ]  umountnfs.sh",
            "[ - ]  umountroot",
            "[ - ]  unattended-upgrades",
            "[ + ]  urandom",
            "[ - ]  uuidd",
            "[ + ]  whoopsie",
            "[ - ]  x11-common",
        )
        for line in output:
            self.write(line + "\n")

    def help(self) -> None:
        output = "Usage: service < option > | --status-all | [ service_name [ command | --full-restart ] ]"
        self.write(output + "\n")

    def call(self) -> None:
        try:
            opts, args = getopt.gnu_getopt(
                self.args, "h", ["help", "status-all", "full-restart"]
            )
        except getopt.GetoptError:
            self.help()
            return

        if not opts and not args:
            self.help()
            return

        for o, _a in opts:
            if o in ("--help") or o in ("-h"):
                self.help()
                return
            elif o in ("--status-all"):
                self.status_all()
        """
        Ubuntu shows no response when stopping, starting
        leviathan@ubuntu:~$ sudo service ufw stop
        leviathan@ubuntu:~$ sudo service ufw start
        leviathan@ubuntu:~$
        """


commands["/usr/sbin/service"] = Command_service
commands["service"] = Command_service
