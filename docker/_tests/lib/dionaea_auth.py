#!/usr/bin/env python3
"""SMB2 negotiation, rejected NTLM authentication and JSON/SQLite capture."""

import argparse
import json
import socket
import sqlite3
import struct
import time
from pathlib import Path

HEADER = struct.Struct('<4sHHIHHIIQIIQ16s')


def packet(command, body, message=0, session=0):
    payload = HEADER.pack(b'\xfeSMB', 64, 0, 0, command, 1, 0, 0,
                          message, 0, 0, session, b'\0' * 16) + body
    return b'\0' + len(payload).to_bytes(3, 'big') + payload


def receive(sock):
    def exact(count):
        result = b''
        while len(result) < count:
            chunk = sock.recv(count - len(result))
            if not chunk:
                raise RuntimeError('SMB response ended before its announced length')
            result += chunk
        return result
    header = exact(4)
    size = int.from_bytes(header[1:], 'big')
    if header[0] or not 64 <= size <= 128 * 1024:
        raise RuntimeError('Invalid SMB2 response frame')
    return exact(size)


def authenticate(username, version):
    response = b'N' * 24 if version == 'NTLMv1' else b'N' * 16 + b'\x01\x01\0\0' + b'\0' * 28
    values = [b'L' * 24, response, 'SMOKEDOMAIN'.encode('utf-16-le'),
              username.encode('utf-16-le'), 'SMOKECLIENT'.encode('utf-16-le'), b'']
    token = b'NTLMSSP\0' + struct.pack('<I', 3)
    offset = 64
    for value in values:
        token += struct.pack('<HHI', len(value), len(value), offset)
        offset += len(value)
    return token + struct.pack('<I', 1) + b''.join(values)


def session_setup(token, session=0, message=1):
    return packet(1, struct.pack('<HBBIIHHQ', 25, 0, 1, 0, 0, 88, len(token), 0) + token,
                  message, session)


def login(host, port, username, dialect, version, timeout):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        body = struct.pack('<HHHHI16sQH', 36, 1, 1, 0, 0, b'\0' * 16, 0, dialect)
        sock.sendall(packet(0, body))
        reply = receive(sock)
        if reply[:4] != b'\xfeSMB' or struct.unpack_from('<I', reply, 8)[0]:
            raise RuntimeError('SMB2 negotiation failed')
        if struct.unpack_from('<H', reply, 68)[0] != dialect:
            raise RuntimeError('SMB2 selected a different dialect')
        sock.sendall(session_setup(b'NTLMSSP\0' + struct.pack('<II', 1, 1)))
        reply = receive(sock)
        if struct.unpack_from('<I', reply, 8)[0] != 0xc0000016:
            raise RuntimeError('SMB2 did not request NTLM authentication')
        offset, size = struct.unpack_from('<HH', reply, 68)
        challenge = reply[offset:offset + size]
        if len(challenge) < 48 or challenge[:12] != b'NTLMSSP\0\x02\0\0\0':
            raise RuntimeError('SMB2 returned an invalid NTLM challenge')
        info_size, _, info_offset = struct.unpack_from('<HHI', challenge, 40)
        if not info_size or info_offset + info_size > len(challenge):
            raise RuntimeError('NTLM challenge omitted bounded TargetInfo')
        session = struct.unpack_from('<Q', reply, 40)[0]
        sock.sendall(session_setup(authenticate(username, version), session, 2))
        if struct.unpack_from('<I', receive(sock), 8)[0] != 0xc000006d:
            raise RuntimeError('SMB2 authentication was not rejected with STATUS_LOGON_FAILURE')


def check_capture(json_path, sqlite_path, username, dialect, version, timeout):
    deadline = time.monotonic() + timeout
    expected = dict(domain='SMOKEDOMAIN', workstation='SMOKECLIENT', dialect=dialect, ntlm_version=version)
    while time.monotonic() < deadline:
        for line in Path(json_path).read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            credentials = event.get('credentials', {})
            if username not in credentials.get('username', []):
                continue
            if credentials.get('password') != ['']:
                raise RuntimeError('NTLM response was recorded as a password')
            for name, value in expected.items():
                if event.get('smb', {}).get(name) != value or credentials.get(name) != [value]:
                    raise RuntimeError('SMB login metadata missing or has an incompatible type: ' + name)
            with sqlite3.connect('file:' + str(sqlite_path) + '?mode=ro', uri=True) as database:
                rows = database.execute('SELECT login_username, login_password FROM logins '
                                        'JOIN connections USING(connection) WHERE login_username = ? '
                                        'AND connection_protocol = ?',
                                        (username, 'smbd')).fetchall()
            if (username, '') in rows:
                return
        time.sleep(0.2)
    raise RuntimeError('SMB login missing from JSON or SQLite: ' + username)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=445)
    parser.add_argument('--token', required=True)
    parser.add_argument('--json', required=True)
    parser.add_argument('--sqlite', required=True)
    parser.add_argument('--timeout', type=int, default=30)
    args = parser.parse_args()
    for dialect, label in [(0x0202, '2.02'), (0x0210, '2.10')]:
        for version in ['NTLMv1', 'NTLMv2']:
            username = args.token + '-' + label + '-' + version
            login(args.host, args.port, username, dialect, version, args.timeout)
            check_capture(args.json, args.sqlite, username, label, version, args.timeout)
            print('SMB2 ' + label + ' ' + version + ': STATUS_LOGON_FAILURE, JSON and SQLite login captured')


if __name__ == '__main__':
    main()
