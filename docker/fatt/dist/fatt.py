#!/usr/bin/env python3
# Copyright (c) 2019, Adel "0x4d31" Karimi.
# All rights reserved.
# Licensed under the BSD 3-Clause license.
# For full license text, see the LICENSE file in the repo root
# or https://opensource.org/licenses/BSD-3-Clause

# fatt. Fingerprint All The Things
# Supported protocols: SSL/TLS, SSH, RDP, HTTP, gQUIC

import argparse
import pyshark
import os
import json
import logging
import struct
from hashlib import md5
from collections import defaultdict

__author__ = "Adel '0x4D31' Karimi"
__version__ = "1.0"


CAP_BPF_FILTER = (
    'tcp port 22 or tcp port 2222 or tcp port 3389 or '
    'tcp port 443 or tcp port 993 or tcp port 995 or '
    'tcp port 636 or tcp port 990 or tcp port 992 or '
    'tcp port 989 or tcp port 563 or tcp port 614 or '
    'tcp port 3306 or tcp port 80 or udp port 80 or '
    'udp port 443')
DISPLAY_FILTER = (
    'tls.handshake.type == 1 || tls.handshake.type == 2 ||'
    'ssh.message_code == 20 || ssh.protocol || rdp ||'
    '(quic && tls.handshake.type == 1) || gquic.tag == "CHLO" ||'
    'http.request.method || data-text-lines'
)
DECODE_AS = {
    'tcp.port==2222': 'ssh', 'tcp.port==3389': 'tpkt',
     'tcp.port==993': 'tls', 'tcp.port==995': 'tls',
     'tcp.port==990': 'tls', 'tcp.port==992': 'tls',
     'tcp.port==989': 'tls', 'tcp.port==563': 'tls',
     'tcp.port==614': 'tls', 'tcp.port==636': 'tls'}
HASSH_VERSION = '1.0'
RDFP_VERSION = '0.3'


class ProcessPackets:

    def __init__(self, fingerprint, jlog, pout):
        self.logger = logging.getLogger()
        self.fingerprint = fingerprint
        self.jlog = jlog
        self.pout = pout
        self.protocol_dict = {}
        self.rdp_dict = defaultdict(dict)

    def process(self, packet):
        record = None
        proto = packet.highest_layer
        sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
        destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst

        # Clear the dictionary used for extracting ssh protocol strings
        # and rdp cookies/negotiateRequests
        if len(self.protocol_dict) > 100 and proto != 'SSH':
            self.protocol_dict.clear()
        if len(self.rdp_dict) > 100 and proto != 'RDP':
            self.rdp_dict.clear()

        # [ SSH ]
        if proto == 'SSH' and ('ssh' in self.fingerprint or
                               self.fingerprint == 'all'):
            # Extract SSH identification string and correlate with KEXINIT msg
            if 'protocol' in packet.ssh.field_names:
                key = '{}:{}_{}:{}'.format(
                    sourceIp,
                    packet.tcp.srcport,
                    destinationIp,
                    packet.tcp.dstport)
                self.protocol_dict[key] = packet.ssh.protocol
            if 'message_code' not in packet.ssh.field_names:
                return
            if packet.ssh.message_code != '20':
                return
            # log the anomalous / retransmission packets
            if ("analysis_retransmission" in packet.tcp.field_names or
                    "analysis_spurious_retransmission" in packet.tcp.field_names):
                event = event_log(packet, event="retransmission")
                if record and self.jlog:
                    self.logger.info(json.dumps(event))
                return
            # Client HASSH
            if int(packet.tcp.srcport) > int(packet.tcp.dstport):
                record = self.client_hassh(packet)
                # Print the result
                if self.pout:
                    tmp = ('{sip}:{sp} -> {dip}:{dp} [SSH] hassh={hassh} client={client}')
                    tmp = tmp.format(
                        sip=record['sourceIp'],
                        sp=record['sourcePort'],
                        dip=record['destinationIp'],
                        dp=record['destinationPort'],
                        client=record['ssh']['client'],
                        hassh=record['ssh']['hassh'],
                    )
                    print(tmp)
            # Server HASSH
            elif int(packet.tcp.srcport) < int(packet.tcp.dstport):
                record = self.server_hassh(packet)
                # Print the result
                if self.pout:
                    tmp = ('{sip}:{sp} -> {dip}:{dp} [SSH] hasshS={hasshs} server={server}')
                    tmp = tmp.format(
                        sip=record['sourceIp'],
                        sp=record['sourcePort'],
                        dip=record['destinationIp'],
                        dp=record['destinationPort'],
                        server=record['ssh']['server'],
                        hasshs=record['ssh']['hasshServer'],
                    )
                    print(tmp)
            if record and self.jlog:
                self.logger.info(json.dumps(record))
            return

        # [ TLS ]
        # TODO: extract tls certificates
        elif proto == 'TLS' and ('tls' in self.fingerprint or
                                 self.fingerprint == 'all'):
            if 'record_content_type' not in packet.tls.field_names:
                return
            # Content Type: Handshake (22)
            if packet.tls.record_content_type != '22':
                return
            # Handshake Type: Client Hello (1) / Server Hello (2)
            if 'handshake_type' not in packet.tls.field_names:
                return
            htype = packet.tls.handshake_type
            if not (htype == '1' or htype == '2'):
                return
            # log the anomalous / retransmission packets
            if ("analysis_retransmission" in packet.tcp.field_names or
                    "analysis_spurious_retransmission" in packet.tcp.field_names):
                event = event_log(packet, event="retransmission")
            # JA3
            if htype == '1':
                record = self.client_ja3(packet)
                # Print the result
                if self.pout:
                    tmp = ('{sip}:{sp} -> {dip}:{dp} [TLS] ja3={ja3} serverName={sname}')
                    tmp = tmp.format(
                        sip=record['sourceIp'],
                        sp=record['sourcePort'],
                        dip=record['destinationIp'],
                        dp=record['destinationPort'],
                        sname=record['tls']['serverName'],
                        ja3=record['tls']['ja3'],
                    )
                    print(tmp)
            elif htype == '2':
                record = self.server_ja3(packet)
                # Print the result
                if self.pout:
                    tmp = (
                        '{sip}:{sp} -> {dip}:{dp} [TLS] ja3s={ja3s}')
                    tmp = tmp.format(
                        sip=record['sourceIp'],
                        sp=record['sourcePort'],
                        dip=record['destinationIp'],
                        dp=record['destinationPort'],
                        ja3s=record['tls']['ja3s'],
                    )
                    print(tmp)
            if record and self.jlog:
                self.logger.info(json.dumps(record))
            return

        # [ RDP ]
        elif proto == 'RDP' and ('rdp' in self.fingerprint or
                                 self.fingerprint == 'all'):
            # Extract RDP cookie & negotiate request and correlate with ClientData msg
            key = None
            if 'rt_cookie' or 'negreq_requestedprotocols':
                key = '{}:{}_{}:{}'.format(
                    sourceIp,
                    packet.tcp.srcport,
                    destinationIp,
                    packet.tcp.dstport)
                if 'rt_cookie' in packet.rdp.field_names:
                    cookie = packet.rdp.rt_cookie.replace('Cookie: ', '')
                    self.rdp_dict[key]["cookie"] = cookie
                if 'negreq_requestedprotocols' in packet.rdp.field_names:
                    req_protos = packet.rdp.negreq_requestedprotocols
                    self.rdp_dict[key]["req_protos"] = req_protos
                    # TLS/CredSSP (not standard RDP security protocols)
                    if req_protos != "0x00000000":
                        record = {
                            "timestamp": packet.sniff_time.isoformat(),
                            "sourceIp": sourceIp,
                            "destinationIp": destinationIp,
                            "sourcePort": packet.tcp.srcport,
                            "destinationPort": packet.tcp.dstport,
                            "protocol": "rdp",
                            "rdp": {
                                "requestedProtocols": req_protos
                            }
                        }
                        if self.pout:
                            tmp = (
                                '{sip}:{sp} -> {dip}:{dp} [RDP] req_protocols={proto}')
                            tmp = tmp.format(
                                sip=record['sourceIp'],
                                sp=record['sourcePort'],
                                dip=record['destinationIp'],
                                dp=record['destinationPort'],
                                proto=record['rdp']['requestedProtocols']
                            )
                            print(tmp)
                        if self.jlog:
                            self.logger.info(json.dumps(record))
            if 'clientdata' not in packet.rdp.field_names:
                return
            if ("analysis_retransmission" in packet.tcp.field_names or
                    "analysis_spurious_retransmission" in packet.tcp.field_names):
                event = event_log(packet, event="retransmission")
                if self.jlog:
                    self.logger.info(json.dumps(event))
                return
            # Client RDFP
            record = self.client_rdfp(packet)
            # Print the result
            if self.pout:
                tmp = ('{sip}:{sp} -> {dip}:{dp} [RDP] rdfp={rdfp} cookie="{cookie}" req_protocols={proto}')
                tmp = tmp.format(
                    sip=record['sourceIp'],
                    sp=record['sourcePort'],
                    dip=record['destinationIp'],
                    dp=record['destinationPort'],
                    rdfp=record['rdp']['rdfp'],
                    cookie=record['rdp']['cookie'],
                    proto=record['rdp']['requestedProtocols']
                )
                print(tmp)
            if record and self.jlog:
                self.logger.info(json.dumps(record))
            return

        # [ HTTP ]
        elif (proto == 'HTTP' or proto == 'DATA-TEXT-LINES') and \
                ('http' in self.fingerprint or self.fingerprint == 'all'):
            if 'request' in packet.http.field_names:
                record = self.client_http(packet)
                # Print the result
                if self.pout:
                    tmp = ('{sip}:{sp} -> {dip}:{dp} [HTTP] hash={hash} userAgent="{ua}"')
                    tmp = tmp.format(
                        sip=record['sourceIp'],
                        sp=record['sourcePort'],
                        dip=record['destinationIp'],
                        dp=record['destinationPort'],
                        hash=record['http']['clientHeaderHash'],
                        ua=record['http']['userAgent'],
                    )
                    print(tmp)
            elif 'response' in packet.http.field_names:
                record = self.server_http(packet)
                # Print the result
                if self.pout:
                    tmp = ('{sip}:{sp} -> {dip}:{dp} [HTTP] hash={hash} server={server}')
                    tmp = tmp.format(
                        sip=record['sourceIp'],
                        sp=record['sourcePort'],
                        dip=record['destinationIp'],
                        dp=record['destinationPort'],
                        hash=record['http']['serverHeaderHash'],
                        server=record['http']['server'],
                    )
                    print(tmp)
            if record and self.jlog:
                self.logger.info(json.dumps(record))
            return

        # [ GQUIC ]
        elif proto == 'GQUIC' and ('gquic' in self.fingerprint or 
                                    self.fingerprint == 'all'):
            if 'tag' in packet.gquic.field_names:
                if packet.gquic.tag == 'CHLO':
                    record = self.client_gquic(packet)
                    # Print the result
                    if self.pout:
                        tmp = ('{sip}:{sp} -> {dip}:{dp} [GQUIC] UAID="{ua}" SNI={sn} AEAD={ea} KEXS={kex}')
                        tmp = tmp.format(
                            sip=record['sourceIp'],
                            sp=record['sourcePort'],
                            dip=record['destinationIp'],
                            dp=record['destinationPort'],
                            ua=record['gquic']['uaid'],
                            sn=record['gquic']['sni'],
                            ea=record['gquic']['aead'],
                            kex=record['gquic']['kexs']
                        )
                        print(tmp)
                if record and self.jlog:
                    self.logger.info(json.dumps(record))
                return
            
        # [ QUIC ]
        elif proto == 'QUIC' and ('quic' in self.fingerprint or 
                                  self.fingerprint == 'all'):

            if packet.quic.tls_handshake_type == '1':
                record = self.client_quic(packet)

                if self.pout:
                    tmp = ('{sip}:{sp} -> {dip}:{dp} [QUIC] serverName="{sn}" VER={ver}')
                    tmp = tmp.format(
                        sip=record['sourceIp'], 
                        sp=record['sourcePort'],    
                        dip=record['destinationIp'],
                        dp=record['destinationPort'],
                        ver=record['quic']['ver'],
                        sn=record['quic']['sni']
                    )
                    print(tmp)
                if record and self.jlog:
                    self.logger.info(json.dumps(record))
                return
 
        return

    def client_hassh(self, packet):
        """returns HASSH (i.e. SSH Client Fingerprint)
        HASSH = md5(KEX;EACTS;MACTS;CACTS)
        """
        protocol = None
        sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
        destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst
        key = '{}:{}_{}:{}'.format(
            sourceIp,
            packet.tcp.srcport,
            destinationIp,
            packet.tcp.dstport)
        if key in self.protocol_dict:
            protocol = self.protocol_dict[key]
        # hassh fields
        ckex = ceacts = cmacts = ccacts = ""
        if 'kex_algorithms' in packet.ssh.field_names:
            ckex = packet.ssh.kex_algorithms
        if 'encryption_algorithms_client_to_server' in packet.ssh.field_names:
            ceacts = packet.ssh.encryption_algorithms_client_to_server
        if 'mac_algorithms_client_to_server' in packet.ssh.field_names:
            cmacts = packet.ssh.mac_algorithms_client_to_server
        if 'compression_algorithms_client_to_server' in packet.ssh.field_names:
            ccacts = packet.ssh.compression_algorithms_client_to_server
        # Log other kexinit fields (only in JSON)
        clcts = clstc = ceastc = cmastc = ccastc = cshka = ""
        if 'languages_client_to_server' in packet.ssh.field_names:
            clcts = packet.ssh.languages_client_to_server
        if 'languages_server_to_client' in packet.ssh.field_names:
            clstc = packet.ssh.languages_server_to_client
        if 'encryption_algorithms_server_to_client' in packet.ssh.field_names:
            ceastc = packet.ssh.encryption_algorithms_server_to_client
        if 'mac_algorithms_server_to_client' in packet.ssh.field_names:
            cmastc = packet.ssh.mac_algorithms_server_to_client
        if 'compression_algorithms_server_to_client' in packet.ssh.field_names:
            ccastc = packet.ssh.compression_algorithms_server_to_client
        if 'server_host_key_algorithms' in packet.ssh.field_names:
            cshka = packet.ssh.server_host_key_algorithms
        # Create hassh
        hassh_str = ';'.join([ckex, ceacts, cmacts, ccacts])
        hassh = md5(hassh_str.encode()).hexdigest()
        record = {
            "timestamp": packet.sniff_time.isoformat(),
            "sourceIp": sourceIp,
            "destinationIp": destinationIp,
            "sourcePort": packet.tcp.srcport,
            "destinationPort": packet.tcp.dstport,
            "protocol": 'ssh',
            "ssh": {
                "client": protocol,
                "hassh": hassh,
                "hasshAlgorithms": hassh_str,
                "hasshVersion": HASSH_VERSION,
                "ckex": ckex,
                "ceacts": ceacts,
                "cmacts": cmacts,
                "ccacts": ccacts,
                "clcts": clcts,
                "clstc": clstc,
                "ceastc": ceastc,
                "cmastc": cmastc,
                "ccastc": ccastc,
                "cshka": cshka
            }
        }
        return record

    def server_hassh(self, packet):
        """returns HASSHServer (i.e. SSH Server Fingerprint)
        HASSHServer = md5(KEX;EASTC;MASTC;CASTC)
        """
        protocol = None
        sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
        destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst
        key = '{}:{}_{}:{}'.format(
            sourceIp,
            packet.tcp.srcport,
            destinationIp,
            packet.tcp.dstport)
        if key in self.protocol_dict:
            protocol = self.protocol_dict[key]
        # hasshServer fields
        skex = seastc = smastc = scastc = ""
        if 'kex_algorithms' in packet.ssh.field_names:
            skex = packet.ssh.kex_algorithms
        if 'encryption_algorithms_server_to_client' in packet.ssh.field_names:
            seastc = packet.ssh.encryption_algorithms_server_to_client
        if 'mac_algorithms_server_to_client' in packet.ssh.field_names:
            smastc = packet.ssh.mac_algorithms_server_to_client
        if 'compression_algorithms_server_to_client' in packet.ssh.field_names:
            scastc = packet.ssh.compression_algorithms_server_to_client
        # Log other kexinit fields (only in JSON)
        slcts = slstc = seacts = smacts = scacts = sshka = ""
        if 'languages_client_to_server' in packet.ssh.field_names:
            slcts = packet.ssh.languages_client_to_server
        if 'languages_server_to_client' in packet.ssh.field_names:
            slstc = packet.ssh.languages_server_to_client
        if 'encryption_algorithms_client_to_server' in packet.ssh.field_names:
            seacts = packet.ssh.encryption_algorithms_client_to_server
        if 'mac_algorithms_client_to_server' in packet.ssh.field_names:
            smacts = packet.ssh.mac_algorithms_client_to_server
        if 'compression_algorithms_client_to_server' in packet.ssh.field_names:
            scacts = packet.ssh.compression_algorithms_client_to_server
        if 'server_host_key_algorithms' in packet.ssh.field_names:
            sshka = packet.ssh.server_host_key_algorithms
        # Create hasshServer
        hasshs_str = ';'.join([skex, seastc, smastc, scastc])
        hasshs = md5(hasshs_str.encode()).hexdigest()
        record = {
            "timestamp": packet.sniff_time.isoformat(),
            "sourceIp": sourceIp,
            "destinationIp": destinationIp,
            "sourcePort": packet.tcp.srcport,
            "destinationPort": packet.tcp.dstport,
            "protocol": 'ssh',
            "ssh": {
                "server": protocol,
                "hasshServer": hasshs,
                "hasshServerAlgorithms": hasshs_str,
                "hasshVersion": HASSH_VERSION,
                "skex": skex,
                "seastc": seastc,
                "smastc": smastc,
                "scastc": scastc,
                "slcts": slcts,
                "slstc": slstc,
                "seacts": seacts,
                "smacts": smacts,
                "scacts": scacts,
                "sshka": sshka
            }
        }
        return record

    def client_ja3(self, packet):
        # GREASE_TABLE Ref: https://tools.ietf.org/html/draft-davidben-tls-grease-00
        GREASE_TABLE = ['2570', '6682', '10794', '14906', '19018', '23130',
                        '27242', '31354', '35466', '39578', '43690', '47802',
                        '51914', '56026', '60138', '64250']
        # ja3 fields
        tls_version = ciphers = extensions = elliptic_curve = ec_pointformat = ""
        if 'handshake_version' in packet.tls.field_names:
            tls_version = int(packet.tls.handshake_version, 16)
            tls_version = str(tls_version)
        if 'handshake_ciphersuite' in packet.tls.field_names:
            cipher_list = [
                c.show for c in packet.tls.handshake_ciphersuite.fields
                if c.show not in GREASE_TABLE]
            ciphers = '-'.join(cipher_list)
        if 'handshake_extension_type' in packet.tls.field_names:
            extension_list = [
                e.show for e in packet.tls.handshake_extension_type.fields
                if e.show not in GREASE_TABLE]
            extensions = '-'.join(extension_list)
        if 'handshake_extensions_supported_group' in packet.tls.field_names:
            ec_list = [str(int(ec.show, 16)) for ec in
                       packet.tls.handshake_extensions_supported_group.fields
                       if str(int(ec.show, 16)) not in GREASE_TABLE]
            elliptic_curve = '-'.join(ec_list)
        if 'handshake_extensions_ec_point_format' in packet.tls.field_names:
            ecpf_list = [ecpf.show for ecpf in
                         packet.tls.handshake_extensions_ec_point_format.fields
                         if ecpf.show not in GREASE_TABLE]
            ec_pointformat = '-'.join(ecpf_list)
        # TODO: log other non-ja3 fields
        server_name = ""
        if 'handshake_extensions_server_name' in packet.tls.field_names:
            server_name = packet.tls.handshake_extensions_server_name
        # Create ja3
        ja3_string = ','.join([
            tls_version, ciphers, extensions, elliptic_curve, ec_pointformat])
        ja3 = md5(ja3_string.encode()).hexdigest()
        sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
        destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst
        record = {
            "timestamp": packet.sniff_time.isoformat(),
            "sourceIp": sourceIp,
            "destinationIp": destinationIp,
            "sourcePort": packet.tcp.srcport,
            "destinationPort": packet.tcp.dstport,
            "protocol": "tls",
            "tls": {
                "serverName": server_name,
                "ja3": ja3,
                "ja3Algorithms": ja3_string,
                "ja3Version": tls_version,
                "ja3Ciphers": ciphers,
                "ja3Extensions": extensions,
                "ja3Ec": elliptic_curve,
                "ja3EcFmt": ec_pointformat
            }
        }
        return record

    def server_ja3(self, packet):
        # GREASE_TABLE Ref: https://tools.ietf.org/html/draft-davidben-tls-grease-00
        GREASE_TABLE = ['2570', '6682', '10794', '14906', '19018', '23130',
                        '27242', '31354', '35466', '39578', '43690', '47802',
                        '51914', '56026', '60138', '64250']
        # ja3s fields
        tls_version = ciphers = extensions = ""
        if 'handshake_version' in packet.tls.field_names:
            tls_version = int(packet.tls.handshake_version, 16)
            tls_version = str(tls_version)
        if 'handshake_ciphersuite' in packet.tls.field_names:
            cipher_list = [
                c.show for c in packet.tls.handshake_ciphersuite.fields
                if c.show not in GREASE_TABLE]
            ciphers = '-'.join(cipher_list)
        if 'handshake_extension_type' in packet.tls.field_names:
            extension_list = [
                e.show for e in packet.tls.handshake_extension_type.fields
                if e.show not in GREASE_TABLE]
            extensions = '-'.join(extension_list)
        # TODO: log other non-ja3s fields
        # Create ja3s
        ja3s_string = ','.join([
            tls_version, ciphers, extensions])
        ja3s = md5(ja3s_string.encode()).hexdigest()
        sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
        destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst
        record = {
            "timestamp": packet.sniff_time.isoformat(),
            "sourceIp": sourceIp,
            "destinationIp": destinationIp,
            "sourcePort": packet.tcp.srcport,
            "destinationPort": packet.tcp.dstport,
            "protocol": "tls",
            "tls": {
                "ja3s": ja3s,
                "ja3sAlgorithms": ja3s_string,
                "ja3sVersion": tls_version,
                "ja3sCiphers": ciphers,
                "ja3sExtensions": extensions
            }
        }
        return record

    def client_rdfp(self, packet):
        """returns ClientData message fields and RDFP (experimental fingerprint)
        RDFP = md5(verMajor,verMinor,clusterFlags,encryptionMethods,extEncMethods,channelDef)
        """
        # RDP fields
        verMajor = verMinor = desktopWidth = desktopHeight = colorDepth = \
            sasSequence = keyboardLayout = clientBuild = clientName = \
            keyboardSubtype = keyboardType = keyboardFuncKey = postbeta2ColorDepth \
            = clientProductId = serialNumber = highColorDepth = \
            supportedColorDepths = earlyCapabilityFlags = clientDigProductId = \
            connectionType = pad1Octet = clusterFlags = encryptionMethods = \
            extEncMethods = channelCount = channelDef = cookie = req_protos = ""

        sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
        destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst
        key = '{}:{}_{}:{}'.format(
            sourceIp,
            packet.tcp.srcport,
            destinationIp,
            packet.tcp.dstport)
        if key in self.rdp_dict and "cookie" in self.rdp_dict[key]:
            cookie = self.rdp_dict[key]["cookie"]
        if key in self.rdp_dict and "req_protos" in self.rdp_dict[key]:
            req_protos = self.rdp_dict[key]["req_protos"]

        # Client Core Data
        # https://msdn.microsoft.com/en-us/library/cc240510.aspx
        if 'version_major' in packet.rdp.field_names:
            verMajor = packet.rdp.version_major
        if 'version_minor' in packet.rdp.field_names:
            verMinor = packet.rdp.version_minor
        if 'desktop_width' in packet.rdp.field_names:
            desktopWidth = packet.rdp.desktop_width
        if 'desktop_height' in packet.rdp.field_names:
            desktopHeight = packet.rdp.desktop_height
        if 'colordepth' in packet.rdp.field_names:
            colorDepth = packet.rdp.colordepth
        if 'sassequence' in packet.rdp.field_names:
            sasSequence = packet.rdp.sassequence
        if 'keyboardlayout' in packet.rdp.field_names:
            keyboardLayout = packet.rdp.keyboardlayout
        if 'client_build' in packet.rdp.field_names:
            clientBuild = packet.rdp.client_build
        if 'client_name' in packet.rdp.field_names:
            clientName = packet.rdp.client_name
        if 'keyboard_subtype' in packet.rdp.field_names:
            keyboardSubtype = packet.rdp.keyboard_subtype
        if 'keyboard_type' in packet.rdp.field_names:
            keyboardType = packet.rdp.keyboard_type
        if 'keyboard_functionkey' in packet.rdp.field_names:
            keyboardFuncKey = packet.rdp.keyboard_functionkey
        if 'postbeta2colordepth' in packet.rdp.field_names:
            postbeta2ColorDepth = packet.rdp.postbeta2colordepth
        if 'client_productid' in packet.rdp.field_names:
            clientProductId = packet.rdp.client_productid
        if 'serialnumber' in packet.rdp.field_names:
            serialNumber = packet.rdp.serialnumber
        if 'highcolordepth' in packet.rdp.field_names:
            highColorDepth = packet.rdp.highcolordepth
        if 'supportedcolordepths' in packet.rdp.field_names:
            supportedColorDepths = packet.rdp.supportedcolordepths
        if 'earlycapabilityflags' in packet.rdp.field_names:
            earlyCapabilityFlags = packet.rdp.earlycapabilityflags
        if 'client_digproductid' in packet.rdp.field_names:
            clientDigProductId = packet.rdp.client_digproductid
        if 'connectiontype' in packet.rdp.field_names:
            connectionType = packet.rdp.connectiontype
        if 'pad1octet' in packet.rdp.field_names:
            pad1Octet = packet.rdp.pad1octet.raw_value

        # Client Cluster Data
        # https://msdn.microsoft.com/en-us/library/cc240514.aspx
        if 'clusterflags' in packet.rdp.field_names:
            clusterFlags_raw = packet.rdp.clusterflags.raw_value
            # convert to little-endian
            clusterFlags = struct.pack('<L', int(clusterFlags_raw, base=16))
            clusterFlags = clusterFlags.hex()

        # Client Security Data
        # Only for "Standard RDP Security mechanisms"
        # https://msdn.microsoft.com/en-us/library/cc240511.aspx
        if 'encryptionmethods' in packet.rdp.field_names:
            encryptionMethods_raw = packet.rdp.encryptionmethods.raw_value
            # convert to little-endian
            encryptionMethods = struct.pack('<L', int(encryptionMethods_raw, base=16))
            encryptionMethods = encryptionMethods.hex()
        # In French locale clients, encryptionMethods MUST be set to zero and
        # extEncryptionMethods MUST be set to the value to which encryptionMethods
        # would have been set.
        if 'extencryptionmethods' in packet.rdp.field_names:
            extEncMethods_raw = packet.rdp.extencryptionmethods.raw_value
            # convert to little-endian
            extEncMethods = struct.pack('<L', int(extEncMethods_raw, base=16))
            extEncMethods = extEncMethods.hex()

        # Client Network Data
        # https://msdn.microsoft.com/en-us/library/cc240512.aspx
        channelDefArray = {}
        if 'channelcount' in packet.rdp.field_names:
            channelCount = packet.rdp.channelcount
            channelDef_temp = []
            for i in range(int(channelCount)):
                name = packet.rdp.name.all_fields[i].show
                options_raw = packet.rdp.options.all_fields[i].raw_value
                # convert to little-endian
                options = struct.pack('<L', int(options_raw, base=16))
                options = options.hex()

                channelDefArray[i] = {
                    "name": name,
                    "options": options
                }
                channelDef_temp.append("{}:{}".format(name, options))
            channelDef = '-'.join(channelDef_temp)

        # Create RDFP
        rdfp_str = ','.join(str(x) for x in [
            verMajor, verMinor, clusterFlags, encryptionMethods, extEncMethods,
            channelDef])

        rdfp = md5(rdfp_str.encode()).hexdigest()
        record = {
            "timestamp": packet.sniff_time.isoformat(),
            "sourceIp": sourceIp,
            "destinationIp": destinationIp,
            "sourcePort": packet.tcp.srcport,
            "destinationPort": packet.tcp.dstport,
            "protocol": "rdp",
            "rdp": {
                "cookie": cookie,
                "requestedProtocols": req_protos,
                "rdfp": rdfp,
                "rdfpAlgorithms": rdfp_str,
                "rdfpVersion": RDFP_VERSION,
                "verMajor": verMajor,
                "verMinor": verMinor,
                "desktopWidth": desktopWidth,
                "desktopHeight": desktopHeight,
                "colorDepth": colorDepth,
                "sasSequence": sasSequence,
                "keyboardLayout": keyboardLayout,
                "clientBuild": clientBuild,
                "clientName": clientName,
                "keyboardSubtype": keyboardSubtype,
                "keyboardType": keyboardType,
                "keyboardFuncKey": keyboardFuncKey,
                "postbeta2ColorDepth": postbeta2ColorDepth,
                "clientProductId": clientProductId,
                "serialNumber": serialNumber,
                "highColorDepth": highColorDepth,
                "supportedColorDepths": supportedColorDepths,
                "earlyCapabilityFlags": earlyCapabilityFlags,
                "clientDigProductId": clientDigProductId,
                "connectionType": connectionType,
                "pad1Octet": pad1Octet,
                "clusterFlags": clusterFlags,
                "encryptionMethods": encryptionMethods,
                "extEncMethods": extEncMethods,
                "channelDefArray": channelDefArray
            }
        }
        return record

    def client_http(self, packet):
        # TODO: log full http req header
        REQ_WL = ['', '_ws_expert', 'chat', '_ws_expert_message',
                  '_ws_expert_severity', '_ws_expert_group', 'request_method',
                  'request_uri', 'request_version', 'request_line',
                  'request_full_uri',
                  'request', 'request_number', 'prev_request_in']
        req_headers = [i for i in packet.http.field_names if i not in REQ_WL]
        ua = requestURI = requestFullURI = requestVersion = requestMethod = ""
        if 'user_agent' in packet.http.field_names:
            ua = packet.http.user_agent
        if 'request_uri' in packet.http.field_names:
            requestURI = packet.http.request_uri
        if 'request_full_uri' in packet.http.field_names:
            requestFullURI = packet.http.request_full_uri
        if 'request_version' in packet.http.field_names:
            requestVersion = packet.http.request_version
        if 'request_method' in packet.http.field_names:
            requestMethod = packet.http.request_method
        client_header_ordering = ','.join(req_headers)
        client_header_hash = md5(client_header_ordering.encode('utf-8')).hexdigest()
        sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
        destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst
        record = {
            "timestamp": packet.sniff_time.isoformat(),
            "sourceIp": sourceIp,
            "destinationIp": destinationIp,
            "sourcePort": packet.tcp.srcport,
            "destinationPort": packet.tcp.dstport,
            "protocol": "http",
            "http": {
                "requestURI": requestURI,
                "requestFullURI": requestFullURI,
                "requestVersion": requestVersion,
                "requestMethod": requestMethod,
                "userAgent": ua,
                "clientHeaderOrder": client_header_ordering,
                "clientHeaderHash": client_header_hash
            }
        }
        return record

    def server_http(self, packet):
        # TODO: log full http resp header
        RESP_WL = ['', '_ws_expert', 'chat', '_ws_expert_message',
                   '_ws_expert_severity', '_ws_expert_group',
                   'response_version',
                   'response_code', 'response_code_desc', 'response_phrase',
                   'response_line', 'content_length_header', 'response',
                   'response_number', 'time', 'request_in', 'response_for_uri',
                   'file_data', 'prev_request_in', 'prev_response_in']
        resp_headers = [i for i in packet.http.field_names if i not in RESP_WL]
        server_header_ordering = ','.join(resp_headers)
        server_header_hash = md5(
            server_header_ordering.encode('utf-8')).hexdigest()
        server = responseVersion = responseCode = contentLength = ""
        if 'server' in packet.http.field_names:
            server = packet.http.server
        if 'response_version' in packet.http.field_names:
            responseVersion = packet.http.response_version
        if 'response_code' in packet.http.field_names:
            responseCode = packet.http.response_code
        if 'content_length' in packet.http.field_names:
            contentLength = packet.http.content_length
        sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
        destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst
        record = {
            "timestamp": packet.sniff_time.isoformat(),
            "sourceIp": sourceIp,
            "destinationIp": destinationIp,
            "sourcePort": packet.tcp.srcport,
            "destinationPort": packet.tcp.dstport,
            "protocol": "http",
            "http": {
                "server": server,
                "responseVersion": responseVersion,
                "responseCode": responseCode,
                "contentLength": contentLength,
                "serverHeaderOrder": server_header_ordering,
                "serverHeaderHash": server_header_hash
            }
        }
        return record

    def client_quic(self, packet):

        ver = sni = None
        print(packet.quic.pretty_print())

        if 'version' in packet.quic.field_names:
            ver = packet.quic.version
        if 'tls_handshake_extensions_server_name' in packet.quic.field_names:
            sni = packet.quic.tls_handshake_extensions_server_name

        sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
        destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst
        record = {
            "timestamp": packet.sniff_time.isoformat(),
            "sourceIp": sourceIp,
            "destinationIp": destinationIp,
            "sourcePort": packet.udp.srcport,
            "destinationPort": packet.udp.dstport,
            "protocol": "quic",
            "quic": {
                "ver": ver,
                "sni": sni
            }
        }
        return record


    def client_gquic(self, packet):
        # https://tools.ietf.org/html/draft-ietf-quic-transport-20
        sni = uaid = ver = stk = pdmd = ccs = ccrt = aead = scid = smhl = mids \
            = kexs = xlct = copt = ccrt = None
        if 'tag_sni' in packet.gquic.field_names:
            sni = packet.gquic.tag_sni
        if 'tag_uaid' in packet.gquic.field_names:
            uaid = packet.gquic.tag_uaid
        if 'tag_version' in packet.gquic.field_names:
            ver = packet.gquic.tag_version
        if 'tag_stk' in packet.gquic.field_names:
            stk = packet.gquic.tag_stk.raw_value
        if 'tag_pdmd' in packet.gquic.field_names:
            pdmd = packet.gquic.tag_pdmd
        if 'tag_ccs' in packet.gquic.field_names:
            ccs = packet.gquic.tag_ccs.raw_value
        if 'tag_ccrt' in packet.gquic.field_names:
            ccrt = packet.gquic.tag_ccrt.raw_value
        if 'tag_aead' in packet.gquic.field_names:
            aead = packet.gquic.tag_aead
        if 'tag_scid' in packet.gquic.field_names:
            scid = packet.gquic.tag_scid.raw_value
        if 'tag_smhl' in packet.gquic.field_names:
            smhl = packet.gquic.tag_smhl
        if 'tag_mids' in packet.gquic.field_names:
            mids = packet.gquic.tag_mids
        if 'tag_kexs' in packet.gquic.field_names:
            kexs = packet.gquic.tag_kexs
        if 'tag_xlct' in packet.gquic.field_names:
            xlct = packet.gquic.tag_xlct.raw_value
        if 'tag_copt' in packet.gquic.field_names:
            copt = packet.gquic.tag_copt
        if 'tag_ccrt' in packet.gquic.field_names:
            ccrt = packet.gquic.tag_ccrt.raw_value
        sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
        destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst
        record = {
            "timestamp": packet.sniff_time.isoformat(),
            "sourceIp": sourceIp,
            "destinationIp": destinationIp,
            "sourcePort": packet.udp.srcport,
            "destinationPort": packet.udp.dstport,
            "protocol": "gquic",
            "gquic": {
                "tagNumber": packet.gquic.tag_number,
                "sni": sni,
                "uaid": uaid,
                "ver": ver,
                "aead": aead,
                "smhl": smhl,
                "mids": mids,
                "kexs": kexs,
                "xlct": xlct,
                "copt": copt,
                "ccrt": ccrt,
                # you can uncomment the following fields
                # TODO: cfcw, sfcw, irtt, csct, sclc, pubs, icsl, smhl, tcid, scid?!
                "stk": stk,
                "pdmd": pdmd,
                "ccs": ccs,
                "ccrt": ccrt,
                "scid": scid
            }
        }
        return record


def event_log(packet, event):
    """log the anomalous packets"""
    if event == "retransmission":
        event_message = "This packet is a (suspected) retransmission"
    # Report the event (only for JSON output)
    sourceIp = packet.ipv6.src if 'ipv6' in packet else packet.ip.src
    destinationIp = packet.ipv6.dst if 'ipv6' in packet else packet.ip.dst
    msg = {"timestamp": packet.sniff_time.isoformat(),
           "eventType": event,
           "eventMessage": event_message,
           "sourceIp": sourceIp,
           "destinationIp": destinationIp,
           "sourcePort": packet.tcp.srcport,
           "destinationPort": packet.tcp.dstport}
    return msg


def parse_cmd_args():
    """parse command line arguments"""
    desc = """A python script for extracting network fingerprints"""
    parser = argparse.ArgumentParser(description=(desc))
    helptxt = "pcap file to process"
    parser.add_argument('-r', '--read_file', type=str, help=helptxt)
    helptxt = "directory of pcap files to process"
    parser.add_argument('-d', '--read_directory', type=str, help=helptxt)
    helptxt = "listen on interface"
    parser.add_argument('-i', '--interface', type=str, help=helptxt)
    helptxt = "protocols to fingerprint. Default: all"
    parser.add_argument(
        '-fp',
        '--fingerprint',
        nargs='*',
        default='all',
        choices=['tls', 'ssh', 'rdp', 'http', 'gquic', 'quic'],
        help=helptxt)
    helptxt = "a dictionary of {decode_criterion_string: decode_as_protocol} \
        that is used to tell tshark to decode protocols in situations it \
        wouldn't usually."
    parser.add_argument(
        '-da', '--decode_as', type=json.loads, default=DECODE_AS, help=helptxt)
    helptxt = "BPF capture filter to use (for live capture only).'"
    parser.add_argument(
        '-f', '--bpf_filter', type=str, default=CAP_BPF_FILTER, help=helptxt)
    helptxt = "log the output in json format"
    parser.add_argument(
        '-j', '--json_logging', action="store_true", help=helptxt)
    helptxt = "specify the output log file. Default: fatt.log"
    parser.add_argument(
        '-o', '--output_file', default='fatt.log', type=str, help=helptxt)
    helptxt = "save the live captured packets to this file"
    parser.add_argument(
        '-w', '--write_pcap', default=None, type=str, help=helptxt)
    helptxt = "print the output"
    parser.add_argument(
        '-p', '--print_output', action="store_true", help=helptxt)
    return parser.parse_args()


def setup_logging(logfile):
    """setup logging"""
    logger = logging.getLogger()
    handler = logging.FileHandler(logfile)
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def main():
    """intake arguments from the user and extract RDP client fingerprints."""
    global DISPLAY_FILTER
    args = parse_cmd_args()
    setup_logging(args.output_file)
    fingerprint = args.fingerprint
    jlog = args.json_logging
    pout = args.print_output

    pp = ProcessPackets(fingerprint, jlog, pout)

    # Process PCAP file
    if args.read_file:
        cap = pyshark.FileCapture(
            args.read_file,
            display_filter=DISPLAY_FILTER,
            keep_packets=False,
            decode_as=args.decode_as)
        try:
            for packet in cap:
                pp.process(packet)
            cap.close()
            cap.eventloop.stop()
        except Exception as e:
            print('Error: {}'.format(e))
            pass
    # Process directory of PCAP files
    elif args.read_directory:
        files = [f.path for f in os.scandir(args.read_directory)
                 if not f.name.startswith('.') and not f.is_dir() and
                 (f.name.endswith(".pcap") or f.name.endswith(".pcapng") or
                 f.name.endswith(".cap"))]
        for file in files:
            cap = pyshark.FileCapture(
                file,
                display_filter=DISPLAY_FILTER,
                keep_packets=False,
                decode_as=args.decode_as)
            try:
                for packet in cap:
                    pp.process(packet)
                cap.close()
                cap.eventloop.stop()
            except Exception as e:
                print('Error: {}'.format(e))
                pass

    # Capture live network traffic
    elif args.interface:
        if args.write_pcap:
            DISPLAY_FILTER = None
        # TODO: Use a Ring Buffer (LiveRingCapture), when the issue is fixed:
        # https://github.com/KimiNewt/pyshark/issues/299
        cap = pyshark.LiveCapture(
            interface=args.interface,
            decode_as=args.decode_as,
            display_filter=DISPLAY_FILTER,
            bpf_filter=args.bpf_filter,
            output_file=args.write_pcap)
        try:
            cap.apply_on_packets(pp.process)
        except (KeyboardInterrupt, SystemExit):
            print("Exiting..\nBYE o/\n")


if __name__ == '__main__':
    main()
