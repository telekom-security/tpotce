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

# Excerpt of Cowrie v3.1.1 (c17c9b73), the text patch_cowrie_persona_support.py touches.

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
