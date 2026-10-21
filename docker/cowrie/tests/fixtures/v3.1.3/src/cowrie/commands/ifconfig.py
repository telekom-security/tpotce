# SPDX-FileCopyrightText: 2014 Peter Reuterås <peter@reuteras.com>
# SPDX-FileCopyrightText: 2015-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import time
from random import randint, randrange

from cowrie.shell.command import HoneyPotCommand

init_time = time.time()

HWaddr = f"{randint(0, 255):02x}:{randint(0, 255):02x}:{randint(0, 255):02x}:{randint(0, 255):02x}:{randint(0, 255):02x}:{randint(0, 255):02x}"
inet6 = f"fe{randint(0, 255):02x}::{randrange(111, 888):02x}:{randint(0, 255):02x}ff:fe{randint(0, 255):02x}:{randint(0, 255):02x}01/64"

base_rx_bytes = randrange(50000000, 150000000)
base_tx_bytes = randrange(5000000, 50000000)
base_lo_bytes = randrange(10000000, 80000000)

rx_rate = randrange(100, 300)
tx_rate = randrange(200, 800)
lo_rate = randrange(10, 50)
avg_packet_size = randrange(500, 1500)

commands = {}


class Command_ifconfig(HoneyPotCommand):
    @staticmethod
    def calculate_packets(byte_count: int) -> int:
        return int(byte_count / avg_packet_size)

    @staticmethod
    def convert_bytes_to_mx(bytes_eth0: int) -> str:
        mb = float(bytes_eth0) / 1000 / 1000
        return f"{mb:.1f}"

    def calculate_rx(self) -> tuple[int, str]:
        session_uptime = time.time() - self.protocol.logintime
        session_rx = int(session_uptime * rx_rate)

        rx_bytes = base_rx_bytes + session_rx
        return rx_bytes, self.convert_bytes_to_mx(rx_bytes)

    def calculate_tx(self) -> tuple[int, str]:
        session_uptime = time.time() - self.protocol.logintime
        session_tx = int(session_uptime * tx_rate)

        tx_bytes = base_tx_bytes + session_tx
        return tx_bytes, self.convert_bytes_to_mx(tx_bytes)

    def calculate_lo(self) -> tuple[int, str]:
        session_uptime = time.time() - self.protocol.logintime
        session_lo = int(session_uptime * lo_rate)

        lo_bytes = base_lo_bytes + session_lo
        return lo_bytes, self.convert_bytes_to_mx(lo_bytes)

    def call(self) -> None:
        rx_bytes_eth0, rx_mb_eth0 = self.calculate_rx()
        tx_bytes_eth0, tx_mb_eth0 = self.calculate_tx()
        lo_bytes, lo_mb = self.calculate_lo()

        rx_packets = self.calculate_packets(rx_bytes_eth0)
        tx_packets = self.calculate_packets(tx_bytes_eth0)
        lo_packets = self.calculate_packets(lo_bytes)

        inet6_gua = ""
        if self.protocol.kippoIPv6:
            inet6_gua = f"\n          inet6 addr: {self.protocol.kippoIPv6}/64 Scope:Global"

        result = f"""eth0      Link encap:Ethernet  HWaddr {HWaddr}
          inet addr:{self.protocol.kippoIP}  Bcast:{self.protocol.kippoIP.rsplit(".", 1)[0]}.255  Mask:255.255.255.0
          inet6 addr: {inet6} Scope:Link{inet6_gua}
          UP BROADCAST RUNNING MULTICAST  MTU:1500  Metric:1
          RX packets:{rx_packets} errors:0 dropped:0 overruns:0 frame:0
          TX packets:{tx_packets} errors:0 dropped:0 overruns:0 carrier:0
          collisions:0 txqueuelen:1000
          RX bytes:{rx_bytes_eth0} ({rx_mb_eth0} MB)  TX bytes:{tx_bytes_eth0} ({tx_mb_eth0} MB)


lo        Link encap:Local Loopback
          inet addr:127.0.0.1  Mask:255.0.0.0
          inet6 addr: ::1/128 Scope:Host
          UP LOOPBACK RUNNING  MTU:65536  Metric:1
          RX packets:{lo_packets} errors:0 dropped:0 overruns:0 frame:0
          TX packets:{lo_packets} errors:0 dropped:0 overruns:0 carrier:0
          collisions:0 txqueuelen:0
          RX bytes:{lo_bytes} ({lo_mb} MB)  TX bytes:{lo_bytes} ({lo_mb} MB)"""
        self.write(f"{result}\n")


commands["/sbin/ifconfig"] = Command_ifconfig
commands["ifconfig"] = Command_ifconfig
