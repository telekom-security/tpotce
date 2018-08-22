# Copyright (C) 2013  Lukas Rist <glaslos@gmail.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

import logging
import tempfile
import shutil
import os

from lxml import etree
from conpot.core.protocol_wrapper import conpot_protocol
from conpot.protocols.snmp.command_responder import CommandResponder
from conpot.protocols.snmp.build_pysnmp_mib_wrapper import find_mibs, compile_mib
import conpot.core as conpot_core


logger = logging.getLogger()


@conpot_protocol
class SNMPServer(object):
    def __init__(self, template, template_directory, args):
        """
        :param host:        hostname or ip address on which to server the snmp service (string).
        :param port:        listen port (integer).
        :param template:    path to the protocol specific xml configuration file (string).
        """

        self.dom = etree.parse(template)
        self.cmd_responder = None

        if args.mibpaths:
            self.compiled_mibs = args.mibpaths
        else:
            self.compiled_mibs = [os.path.join(template_directory, 'snmp', 'mibs_compiled')]

        if args.raw_mib:
            self.raw_mibs = args.raw_mib
        else:
            self.raw_mibs = [os.path.join(template_directory, 'snmp', 'mibs_raw')]

    def xml_general_config(self, dom):
        snmp_config = dom.xpath('//snmp/config/*')
        if snmp_config:
            for entity in snmp_config:

                # TARPIT: individual response delays
                if entity.attrib['name'].lower() == 'tarpit':

                    if entity.attrib['command'].lower() == 'get':
                        self.cmd_responder.resp_app_get.tarpit = self.config_sanitize_tarpit(entity.text)
                    elif entity.attrib['command'].lower() == 'set':
                        self.cmd_responder.resp_app_set.tarpit = self.config_sanitize_tarpit(entity.text)
                    elif entity.attrib['command'].lower() == 'next':
                        self.cmd_responder.resp_app_next.tarpit = self.config_sanitize_tarpit(entity.text)
                    elif entity.attrib['command'].lower() == 'bulk':
                        self.cmd_responder.resp_app_bulk.tarpit = self.config_sanitize_tarpit(entity.text)

                # EVASION: response thresholds
                if entity.attrib['name'].lower() == 'evasion':

                    if entity.attrib['command'].lower() == 'get':
                        self.cmd_responder.resp_app_get.threshold = self.config_sanitize_threshold(entity.text)
                    elif entity.attrib['command'].lower() == 'set':
                        self.cmd_responder.resp_app_set.threshold = self.config_sanitize_threshold(entity.text)
                    elif entity.attrib['command'].lower() == 'next':
                        self.cmd_responder.resp_app_next.threshold = self.config_sanitize_threshold(entity.text)
                    elif entity.attrib['command'].lower() == 'bulk':
                        self.cmd_responder.resp_app_bulk.threshold = self.config_sanitize_threshold(entity.text)

    def xml_mib_config(self, dom, mibpaths, rawmibs_dirs):
        try:
            mibs = dom.xpath('//snmp/mibs/*')
            tmp_mib_dir = tempfile.mkdtemp(dir=os.environ['CONPOT_TMP'])
            mibpaths.append(tmp_mib_dir)
            available_mibs = find_mibs(rawmibs_dirs)

            databus = conpot_core.get_databus()
            # parse mibs and oid tables
            for mib in mibs:
                mib_name = mib.attrib['name']
                # compile the mib file if it is found and not already loaded.
                if mib_name in available_mibs and not self.cmd_responder.has_mib(mib_name):
                    compile_mib(mib_name, tmp_mib_dir)
                for symbol in mib:
                    symbol_name = symbol.attrib['name']

                    # retrieve instance from template
                    if 'instance' in symbol.attrib:
                        # convert instance to (int-)tuple
                        symbol_instance = symbol.attrib['instance'].split('.')
                        symbol_instance = tuple(map(int, symbol_instance))
                    else:
                        # use default instance (0)
                        symbol_instance = (0,)


                    # retrieve value from databus
                    value = databus.get_value(symbol.xpath('./value/text()')[0])
                    profile_map_name = symbol.xpath('./value/text()')[0]

                    # register this MIB instance to the command responder
                    self.cmd_responder.register(mib_name,
                                                symbol_name,
                                                symbol_instance,
                                                value,
                                                profile_map_name)
        finally:
            # cleanup compiled mib files
            shutil.rmtree(tmp_mib_dir)

    def config_sanitize_tarpit(self, value):

        # checks tarpit value for being either a single int or float,
        # or a series of two concatenated integers and/or floats separated by semicolon and returns
        # either the (sanitized) value or zero.

        if value is not None:

            x, _, y = value.partition(';')

            try:
                _ = float(x)
            except ValueError:
                logger.error("SNMP invalid tarpit value: '%s'. Assuming no latency.", value)
                # first value is invalid, ignore the whole setting.
                return '0;0'

            try:
                _ = float(y)
                # both values are fine.
                return value
            except ValueError:
                # second value is invalid, use the first one.
                return x

        else:
            return '0;0'

    def config_sanitize_threshold(self, value):

        # checks DoS thresholds for being either a single int or a series of two concatenated integers
        # separated by semicolon and returns either the (sanitized) value or zero.

        if value is not None:

            x, _, y = value.partition(';')

            try:
                _ = int(x)
            except ValueError:
                logger.error("SNMP invalid evasion threshold: '%s'. Assuming no DoS evasion.", value)
                # first value is invalid, ignore the whole setting.
                return '0;0'

            try:
                _ = int(y)
                # both values are fine.
                return value
            except ValueError:
                # second value is invalid, use the first and ignore the second.
                return str(x) + ';0'

        else:
            return '0;0'

    def start(self, host, port):
        self.cmd_responder = CommandResponder(host, port, self.compiled_mibs)
        self.xml_general_config(self.dom)
        self.xml_mib_config(self.dom, self.compiled_mibs, self.raw_mibs)

        logger.info('SNMP server started on: %s', (host, self.get_port()))
        self.cmd_responder.serve_forever()

    def stop(self):
        if self.cmd_responder:
            self.cmd_responder.stop()

    def get_port(self):
        if self.cmd_responder:
            return self.cmd_responder.server_port
        else:
            return None
