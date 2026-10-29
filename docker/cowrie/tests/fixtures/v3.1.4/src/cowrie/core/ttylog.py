# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2014-2021 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause


"""
Should be compatible with user mode linux
"""

from __future__ import annotations

import hashlib
import struct

OP_OPEN, OP_CLOSE, OP_WRITE, OP_EXEC = 1, 2, 3, 4
TYPE_INPUT, TYPE_OUTPUT, TYPE_INTERACT = 1, 2, 3
TTYSTRUCT = "<iLiiLL"


def ttylog_open(logfile: str, stamp: float) -> None:
    """
    Initialize new tty log

    @param logfile: logfile name
    @param stamp: timestamp
    """
    with open(logfile, "ab") as f:
        sec, usec = int(stamp), int(1000000 * (stamp - int(stamp)))
        f.write(struct.pack(TTYSTRUCT, OP_OPEN, 0, 0, 0, sec, usec))


def ttylog_write(
    logfile: str, length: int, direction: int, stamp: float, data: bytes
) -> None:
    """
    Write to tty log

    @param logfile: timestamp
    @param length: length
    @param direction: 0 or 1
    @param stamp: timestamp
    @param data: data
    """
    with open(logfile, "ab") as f:
        sec, usec = int(stamp), int(1000000 * (stamp - int(stamp)))
        f.write(struct.pack(TTYSTRUCT, OP_WRITE, 0, length, direction, sec, usec))
        f.write(data)


def ttylog_close(logfile: str, stamp: float) -> None:
    """
    Close tty log

    @param logfile: logfile name
    @param stamp: timestamp
    """
    with open(logfile, "ab") as f:
        sec, usec = int(stamp), int(1000000 * (stamp - int(stamp)))
        f.write(struct.pack(TTYSTRUCT, OP_CLOSE, 0, 0, 0, sec, usec))


def ttylog_inputhash(logfile: str) -> str:
    """
    Create unique hash of the input parts of tty log

    @param logfile: logfile name
    """
    ssize: int = struct.calcsize(TTYSTRUCT)
    inputbytes: bytes = b""

    with open(logfile, "rb") as fd:
        while 1:
            try:
                op: int
                _tty: int
                length: int
                direction: int
                _sec: int
                _usec: int
                op, _tty, length, direction, _sec, _usec = struct.unpack(
                    TTYSTRUCT, fd.read(ssize)
                )
                data: bytes = fd.read(length)
            except struct.error:
                break

            if op is OP_WRITE and direction is TYPE_OUTPUT:
                continue
            inputbytes = inputbytes + data

        shasum: str = hashlib.sha256(inputbytes).hexdigest()
        return shasum
