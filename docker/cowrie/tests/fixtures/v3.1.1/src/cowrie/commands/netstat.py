# SPDX-FileCopyrightText: 2015-2022 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# Based on work by Peter Reuteras (https://bitbucket.org/reuteras/kippo/)

from __future__ import annotations

import ipaddress
import socket

from cowrie.shell.command import HoneyPotCommand

commands = {}


class Command_netstat(HoneyPotCommand):
    def show_version(self) -> None:
        self.write("net-tools 1.60\n")
        self.write("netstat 1.42 (2001-04-15)\n")
        self.write(
            "Fred Baumgarten, Alan Cox, Bernd Eckenfels, Phil Blundell, Tuan Hoang and others\n"
        )
        self.write("+NEW_ADDRT +RTF_IRTT +RTF_REJECT +FW_MASQUERADE +I18N\n")
        self.write(
            "AF: (inet) +UNIX +INET +INET6 +IPX +AX25 +NETROM +X25 +ATALK +ECONET +ROSE\n"
        )
        self.write(
            "HW:  +ETHER +ARC +SLIP +PPP +TUNNEL +TR +AX25 +NETROM +X25"
            + "+FR +ROSE +ASH +SIT +FDDI +HIPPI +HDLC/LAPB +EUI64\n"
        )

    def show_help(self) -> None:
        self.write(
            """
usage: netstat [-vWeenNcCF] [<Af>] -r         netstat {-V|--version|-h|--help}
       netstat [-vWnNcaeol] [<Socket> ...]
       netstat { [-vWeenNac] -i | [-cWnNe] -M | -s }

        -r, --route              display routing table
        -i, --interfaces         display interface table
        -g, --groups             display multicast group memberships
        -s, --statistics         display networking statistics (like SNMP)
        -M, --masquerade         display masqueraded connections

        -v, --verbose            be verbose
        -W, --wide               don\'t truncate IP addresses
        -n, --numeric            don\'t resolve names
        --numeric-hosts          don\'t resolve host names
        --numeric-ports          don\'t resolve port names
        --numeric-users          don\'t resolve user names
        -N, --symbolic           resolve hardware names
        -e, --extend             display other/more information
        -p, --programs           display PID/Program name for sockets
        -c, --continuous         continuous listing

        -l, --listening          display listening server sockets

        -o, --timers             display timers
        -F, --fib                display Forwarding Information Base (default)
        -C, --cache              display routing cache instead of FIB

  <Socket>={-t|--tcp} {-u|--udp} {-w|--raw} {-x|--unix} --ax25 --ipx --netrom
  <AF>=Use \'-6|-4\' or \'-A <af>\' or \'--<af>\'; default: inet
  List of possible address families (which support routing):
    inet (DARPA Internet) inet6 (IPv6) ax25 (AMPR AX.25)
    netrom (AMPR NET/ROM) ipx (Novell IPX) ddp (Appletalk DDP)
    x25 (CCITT X.25)
"""
        )

    def do_netstat_route(self) -> None:
        if not self.show_ipv6 or self.show_ipv4:
            self._do_netstat_route4()
        if self.show_ipv6:
            self._do_netstat_route6()

    def _do_netstat_route4(self) -> None:
        self.write(
            "Kernel IP routing table\n"
            "Destination     Gateway         Genmask         Flags   MSS Window  irtt Iface\n"
        )
        if self.show_numeric:
            default = "default"
            lgateway = "0.0.0.0"
        else:
            default = "0.0.0.0"
            lgateway = "*"
        destination = self.protocol.kippoIP.rsplit(".", 1)[0] + ".0"
        gateway = self.protocol.kippoIP.rsplit(".", 1)[0] + ".1"
        l1 = "{}{}0.0.0.0         UG        0 0          0 eth0".format(
            f"{default:<16}",
            f"{gateway:<16}",
        )
        l2 = "{}{}255.255.255.0   U         0 0          0 eth0".format(
            f"{destination:<16}",
            f"{lgateway:<16}",
        )
        self.write(f"{l1}\n")
        self.write(f"{l2}\n")

    def _do_netstat_route6(self) -> None:
        self.write(
            "Kernel IPv6 routing table\n"
            "Destination                    Next Hop                   Flag Met Ref  Use If\n"
        )
        rows = []
        if self.protocol.kippoIPv6:
            net = ipaddress.IPv6Network(
                f"{self.protocol.kippoIPv6}/64", strict=False
            )
            rows += [
                (str(net), "::", "U", "100", "0", "0", "eth0"),
                ("fe80::/64", "::", "U", "100", "0", "0", "eth0"),
            ]
        rows += [
            ("::1/128", "::", "U", "256", "2", "0", "lo"),
        ]
        if self.protocol.kippoIPv6:
            rows += [
                ("::/0", "fe80::1", "UG", "100", "0", "0", "eth0"),  # NOSONAR - simulated honeypot routing table entry
                ("ff00::/8", "::", "U", "256", "0", "0", "eth0"),
            ]
        for dest, nh, flag, met, ref, use, iface in rows:
            self.write(
                f"{dest:<31}{nh:<27}{flag:<5}{met:<4}{ref:<5}{use:<4}{iface}\n"
            )

    def do_netstat_normal(self) -> None:
        self.write(
            """Active Internet connections (w/o servers)
Proto Recv-Q Send-Q Local Address           Foreign Address         State\n"""
        )
        s_name = self.protocol.hostname
        c_port = str(self.protocol.realClientPort)
        if self.show_numeric:
            s_port = "22"
            c_name = str(self.protocol.clientIP)
            s_name = str(self.protocol.kippoIP)
        else:
            s_port = "ssh"
            try:
                c_name = socket.gethostbyaddr(self.protocol.clientIP)[0][:17]
            except Exception:
                c_name = self.protocol.clientIP
        if self.show_listen or self.show_all:
            self.write(
                "tcp        0      0 *:ssh                   *:*                     LISTEN\n"
            )
        if not self.show_listen or self.show_all:
            line = "tcp        0    308 {}:{}{}{}:{}{}{}".format(
                s_name,
                s_port,
                " " * (24 - len(s_name + s_port) - 1),
                c_name,
                c_port,
                " " * (24 - len(c_name + c_port) - 1),
                "ESTABLISHED",
            )
            self.write(f"{line}\n")
        if self.show_listen or self.show_all:
            self.write(
                "tcp6       0      0 [::]:ssh                [::]:*                  LISTEN\n"
            )
        self.write(
            """Active UNIX domain sockets (only servers)
Proto RefCnt Flags       Type       State         I-Node   Path\n"""
        )
        if self.show_listen:
            self.write(
                """unix  2      [ ACC ]     STREAM     LISTENING     8969     /var/run/acpid.socket
unix  2      [ ACC ]     STREAM     LISTENING     6807     @/com/ubuntu/upstart
unix  2      [ ACC ]     STREAM     LISTENING     7299     /var/run/dbus/system_bus_socket
unix  2      [ ACC ]     SEQPACKET  LISTENING     7159     /run/udev/control\n"""
            )
        elif self.show_all:
            self.write(
                """unix  2      [ ACC ]     STREAM     LISTENING     8969     /var/run/acpid.socket
unix  4      [ ]         DGRAM                    7445     /dev/log
unix  2      [ ACC ]     STREAM     LISTENING     6807     @/com/ubuntu/upstart
unix  2      [ ACC ]     STREAM     LISTENING     7299     /var/run/dbus/system_bus_socket
unix  2      [ ACC ]     SEQPACKET  LISTENING     7159     /run/udev/control
unix  3      [ ]         STREAM     CONNECTED     7323
unix  3      [ ]         STREAM     CONNECTED     7348     /var/run/dbus/system_bus_socket
unix  3      [ ]         STREAM     CONNECTED     7330
unix  2      [ ]         DGRAM                    8966
unix  3      [ ]         STREAM     CONNECTED     7424     /var/run/dbus/system_bus_socket
unix  3      [ ]         STREAM     CONNECTED     7140
unix  3      [ ]         STREAM     CONNECTED     7145     @/com/ubuntu/upstart
unix  3      [ ]         DGRAM                    7199
unix  3      [ ]         STREAM     CONNECTED     7347
unix  3      [ ]         STREAM     CONNECTED     8594
unix  3      [ ]         STREAM     CONNECTED     7331
unix  3      [ ]         STREAM     CONNECTED     7364     @/com/ubuntu/upstart
unix  3      [ ]         STREAM     CONNECTED     7423
unix  3      [ ]         DGRAM                    7198
unix  2      [ ]         DGRAM                    9570
unix  3      [ ]         STREAM     CONNECTED     8619     @/com/ubuntu/upstart\n"""
            )
        else:
            self.write(
                """unix  4      [ ]         DGRAM                    7445     /dev/log
unix  3      [ ]         STREAM     CONNECTED     7323
unix  3      [ ]         STREAM     CONNECTED     7348     /var/run/dbus/system_bus_socket
unix  3      [ ]         STREAM     CONNECTED     7330
unix  2      [ ]         DGRAM                    8966
unix  3      [ ]         STREAM     CONNECTED     7424     /var/run/dbus/system_bus_socket
unix  3      [ ]         STREAM     CONNECTED     7140
unix  3      [ ]         STREAM     CONNECTED     7145     @/com/ubuntu/upstart
unix  3      [ ]         DGRAM                    7199
unix  3      [ ]         STREAM     CONNECTED     7347
unix  3      [ ]         STREAM     CONNECTED     8594
unix  3      [ ]         STREAM     CONNECTED     7331
unix  3      [ ]         STREAM     CONNECTED     7364     @/com/ubuntu/upstart
unix  3      [ ]         STREAM     CONNECTED     7423
unix  3      [ ]         DGRAM                    7198
unix  2      [ ]         DGRAM                    9570
unix  3      [ ]         STREAM     CONNECTED     8619     @/com/ubuntu/upstart\n"""
            )

    def call(self) -> None:
        self.show_all = False
        self.show_numeric = False
        self.show_listen = False
        self.show_ipv4 = False
        self.show_ipv6 = False
        func = self.do_netstat_normal
        for x in self.args:
            if x.startswith("-") and x.count("a"):
                self.show_all = True
            if x.startswith("-") and x.count("n"):
                self.show_numeric = True
            if x.startswith("-") and x.count("l"):
                self.show_listen = True
            if x.startswith("-") and x.count("r"):
                func = self.do_netstat_route
            if x.startswith("-") and x.count("h"):
                func = self.show_help
            if x.startswith("-") and x.count("V"):
                func = self.show_version
            if x == "-4":
                self.show_ipv4 = True
            if x == "-6":
                self.show_ipv6 = True
        func()


commands["/bin/netstat"] = Command_netstat
commands["netstat"] = Command_netstat
