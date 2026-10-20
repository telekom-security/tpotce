#!/usr/bin/env python3
"""Exercise ewsposter's real Dionaea reader with a generated database and no network sends."""

import argparse
import base64
import configparser
import hashlib
import importlib
import sqlite3
import tempfile
from pathlib import Path

from modules.ealert import EAlert
reader = importlib.import_module("honeypots.dionaea")


class Logger:
    def warning(self, *args):
        pass

    def error(self, *args):
        raise RuntimeError(str(args))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite', required=True)
    parser.add_argument('--binaries', required=True)
    args = parser.parse_args()
    alerts = []
    with tempfile.TemporaryDirectory() as temporary:
        config_file = Path(temporary) / 'ews.cfg'
        config = configparser.ConfigParser()
        config['DIONAEA'] = dict(dionaea='true', nodeid='compatibility-test',
                                sqlitedb=args.sqlite, malwaredir=args.binaries)
        with config_file.open('w') as handle:
            config.write(handle)

        class StubAlert(EAlert):
            def __init__(self, module, settings):
                self.MODUL = module.upper()
                self.ECFG = settings
                self.DATA, self.REQUEST, self.ADATA = {}, {}, {}
                self.sqlite3connect = False
                self.maxid = 0
                self.index = 1
                self.malwareindex = []
                self.logger = Logger()

            def alertCount(self, section, counting, item='index', setto=1):
                if counting == 'get_counter':
                    return self.index
                self.index += 1

            def data(self, key, value):
                self.DATA[key] = value

            def request(self, key, value):
                self.REQUEST[key] = value

            def adata(self, key, value):
                self.ADATA[key] = value

            def buildAlert(self):
                alerts.append((self.DATA.copy(), self.REQUEST.copy(), self.ADATA.copy()))
                self.DATA, self.REQUEST, self.ADATA = {}, {}, {}

            def finAlert(self):
                if self.sqlite3connect:
                    self.con.close()

        settings = dict(cfgfile=str(config_file), homedir=temporary,
                        send_malware=True, del_malware_after_send=False, hostname='test.local',
                        ip_ext='203.0.113.42', ip_int='192.0.2.12', uuid='compatibility-test')
        reader.EAlert = StubAlert
        reader.dionaea(settings)
        if not alerts:
            raise RuntimeError('No alerts were generated from the Dionaea database')
        payloads = set()
        for data, request, metadata in alerts:
            if not isinstance(data.get('timestamp'), str):
                raise RuntimeError('Timestamp format changed')
            payload = request.get('binary') or request.get('largepayload')
            if payload:
                md5 = hashlib.md5(base64.b64decode(payload)).hexdigest()
                if md5 != metadata.get('payload_md5'):
                    raise RuntimeError('ewsposter payload hash does not match the database')
                payloads.add(md5)
        if not payloads:
            raise RuntimeError('No stored upload/download payload was read')
        with sqlite3.connect('file:' + args.sqlite + '?mode=ro', uri=True) as database:
            captured = database.execute('SELECT DISTINCT download_md5_hash, download_url FROM downloads').fetchall()
        protocols = {url.split(':', 1)[0] for md5, url in captured if md5 in payloads}
        if not {'ftp', 'smb'}.issubset(protocols):
            raise RuntimeError('FTP and SMB upload payloads were not both read: ' + repr(protocols))
        print('ewsposter: %d alerts, %d verified payloads; FTP/SMB captures, unchanged timestamps, no network sends'
              % (len(alerts), len(payloads)))


if __name__ == '__main__':
    main()
