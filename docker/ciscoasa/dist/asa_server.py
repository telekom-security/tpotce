#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import time
import socket
import logging
logging.basicConfig(format='%(message)s')
import threading
from io import BytesIO
from xml.etree import ElementTree
from http.server import HTTPServer
from socketserver import ThreadingMixIn
from http.server import SimpleHTTPRequestHandler
import ike_server
import datetime


class NonBlockingHTTPServer(ThreadingMixIn, HTTPServer):
    pass

class hpflogger:
    def __init__(self, hpfserver, hpfport, hpfident, hpfsecret, hpfchannel, serverid, verbose):
        self.hpfserver=hpfserver
        self.hpfport=hpfport
        self.hpfident=hpfident
        self.hpfsecret=hpfsecret
        self.hpfchannel=hpfchannel
        self.serverid=serverid
        self.hpc=None
        self.verbose=verbose
        if (self.hpfserver and self.hpfport and self.hpfident and self.hpfport and self.hpfchannel and self.serverid):
            import hpfeeds
            try:
                self.hpc = hpfeeds.new(self.hpfserver, self.hpfport, self.hpfident, self.hpfsecret)
                logger.debug("Logging to hpfeeds using server: {0}, channel {1}.".format(self.hpfserver, self.hpfchannel))
            except (hpfeeds.FeedException, socket.error, hpfeeds.Disconnect):
                logger.critical("hpfeeds connection not successful")

    def log(self, level, message):
        if self.hpc:
            if level in ['debug', 'info'] and not self.verbose:
                return
            self.hpc.publish(self.hpfchannel, "["+self.serverid+"] ["+level+"] ["+datetime.datetime.now().isoformat() +"] "  + str(message))


def header_split(h):
    return [list(map(str.strip, l.split(': ', 1))) for l in h.strip().splitlines()]


class WebLogicHandler(SimpleHTTPRequestHandler):
    logger = None
    hpfl = None

    protocol_version = "HTTP/1.1"

    EXPLOIT_STRING = b"host-scan-reply"
    RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<config-auth client="vpn" type="complete">
<version who="sg">9.0(1)</version>
<error id="98" param1="" param2="">VPN Server could not parse request.</error>
</config-auth>"""

    basepath = os.path.dirname(os.path.abspath(__file__))

    alert_function = None

    def setup(self):
        SimpleHTTPRequestHandler.setup(self)
        self.request.settimeout(3)

    def send_header(self, keyword, value):
        if keyword.lower() == 'server':
            return
        SimpleHTTPRequestHandler.send_header(self, keyword, value)

    def send_head(self):
        # send_head will return a file object that do_HEAD/GET will use
        # do_GET/HEAD are already implemented by SimpleHTTPRequestHandler
        filename = os.path.basename(self.path.rstrip('/').split('?', 1)[0])

        if self.path == '/':
            self.send_response(200)
            for k, v in header_split("""
                Content-Type: text/html
                Cache-Control: no-cache
                Pragma: no-cache
                Set-Cookie: tg=; expires=Thu, 01 Jan 1970 22:00:00 GMT; path=/; secure
                Set-Cookie: webvpn=; expires=Thu, 01 Jan 1970 22:00:00 GMT; path=/; secure
                Set-Cookie: webvpnc=; expires=Thu, 01 Jan 1970 22:00:00 GMT; path=/; secure
                Set-Cookie: webvpn_portal=; expires=Thu, 01 Jan 1970 22:00:00 GMT; path=/; secure
                Set-Cookie: webvpnSharePoint=; expires=Thu, 01 Jan 1970 22:00:00 GMT; path=/; secure
                Set-Cookie: webvpnlogin=1; path=/; secure
                Set-Cookie: sdesktop=; expires=Thu, 01 Jan 1970 22:00:00 GMT; path=/; secure
            """):
                self.send_header(k, v)
            self.end_headers()
            return BytesIO(b'<html><script>document.location.replace("/+CSCOE+/logon.html")</script></html>\n')
        elif filename == 'asa':  # don't allow dir listing
            return self.send_file('wrong_url.html', 403)
        else:
            return self.send_file(filename)

    def redirect(self, loc):
        self.send_response(302)
        for k, v in header_split("""
            Content-Type: text/html
            Content-Length: 0
            Cache-Control: no-cache
            Pragma: no-cache
            Location: %s
            Set-Cookie: tg=; expires=Thu, 01 Jan 1970 22:00:00 GMT; path=/; secure
        """ % (loc,)):
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        if self.path == '/+CSCOE+/logon.html':
            self.redirect('/+CSCOE+/logon.html?fcadbadd=1')
            return
        elif self.path.startswith('/+CSCOE+/logon.html?') and 'reason=1' in self.path:
            self.wfile.write(self.send_file('logon_failure').getvalue())
            return
        SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        data_len = int(self.headers.get('Content-length', 0))
        data = self.rfile.read(data_len) if data_len else b''
        body = self.RESPONSE
        if self.EXPLOIT_STRING in data:
            xml = ElementTree.fromstring(data)
            payloads = []
            for x in xml.iter('host-scan-reply'):
                payloads.append(x.text)

            self.alert_function(self.client_address[0], self.client_address[1], payloads)

        elif self.path == '/':
            self.redirect('/+webvpn+/index.html')
            return
        elif self.path == '/+CSCOE+/logon.html':
            self.redirect('/+CSCOE+/logon.html?fcadbadd=1')
            return
        elif self.path.split('?', 1)[0] == '/+webvpn+/index.html':
            with open(os.path.join(self.basepath, 'asa', "logon_redir.html"), 'rb') as fh:
                body = fh.read()

        self.send_response(200)
        self.send_header('Content-Length', int(len(body)))
        self.send_header('Content-Type', 'text/html; charset=UTF-8')
        self.end_headers()
        self.wfile.write(body)
        return

    def send_file(self, filename, status_code=200, headers=[]):
        try:
            with open(os.path.join(self.basepath, 'asa', filename), 'rb') as fh:
                body = fh.read()
                self.send_response(status_code)
                for k, v in headers:
                    self.send_header(k, v)
                if status_code == 200:
                    for k, v in header_split("""
                        Cache-Control: max-age=0
                        Set-Cookie: webvpn=; expires=Thu, 01 Jan 1970 22:00:00 GMT; path=/; secure
                        Set-Cookie: webvpnc=; expires=Thu, 01 Jan 1970 22:00:00 GMT; path=/; secure
                        Set-Cookie: webvpnlogin=1; secure
                        X-Transcend-Version: 1
                    """):
                        self.send_header(k, v)
                self.send_header('Content-Length', int(len(body)))
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                return BytesIO(body)
        except IOError:
            return self.send_file('wrong_url.html', 404)

    def log_message(self, format, *args):
        self.logger.debug("{'timestamp': '%s', 'src_ip': '%s', 'payload_printable': '%s'}" %
                          (datetime.datetime.now().isoformat(),
                           self.client_address[0],
                           format % args))
        self.hpfl.log('debug', "%s - - [%s] %s" %
                          (self.client_address[0],
                           self.log_date_time_string(),
                           format % args))

    def handle_one_request(self):
        """Handle a single HTTP request.
        Overriden to not send 501 errors
        """
        self.close_connection = True
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ''
                self.request_version = ''
                self.command = ''
                self.close_connection = 1
                return
            if not self.raw_requestline:
                self.close_connection = 1
                return
            if not self.parse_request():
                # An error code has been sent, just exit
                return
            mname = 'do_' + self.command
            if not hasattr(self, mname):
                self.log_request()
                self.close_connection = True
                return
            method = getattr(self, mname)
            method()
            self.wfile.flush()  # actually send the response if not already done.
        except socket.timeout as e:
            # a read or a write timed out.  Discard this connection
            self.log_error("Request timed out: %r", e)
            self.close_connection = 1
            return


if __name__ == '__main__':
    import click

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger()
    logger.info('info')

    @click.command()
    @click.option('-h', '--host', default='0.0.0.0', help='Host to listen')
    @click.option('-p', '--port', default=8443, help='Port to listen', type=click.INT)
    @click.option('-i', '--ike-port', default=5000, help='Port to listen for IKE', type=click.INT)
    @click.option('-s', '--enable_ssl', default=False, help='Enable SSL', is_flag=True)
    @click.option('-c', '--cert', default=None, help='Certificate File Path (will generate self signed '
                                                     'cert if not supplied)')
    @click.option('-v', '--verbose', default=False, help='Verbose logging', is_flag=True)

    # hpfeeds options
    @click.option('--hpfserver', default=os.environ.get('HPFEEDS_SERVER'), help='HPFeeds Server')
    @click.option('--hpfport', default=os.environ.get('HPFEEDS_PORT'), help='HPFeeds Port', type=click.INT)
    @click.option('--hpfident', default=os.environ.get('HPFEEDS_IDENT'), help='HPFeeds Ident')
    @click.option('--hpfsecret', default=os.environ.get('HPFEEDS_SECRET'), help='HPFeeds Secret')
    @click.option('--hpfchannel', default=os.environ.get('HPFEEDS_CHANNEL'), help='HPFeeds Channel')
    @click.option('--serverid', default=os.environ.get('SERVERID'), help='Verbose logging')


    def start(host, port, ike_port, enable_ssl, cert, verbose, hpfserver, hpfport, hpfident, hpfsecret, hpfchannel, serverid):
        """
           A low interaction honeypot for the Cisco ASA component capable of detecting CVE-2018-0101,
           a DoS and remote code execution vulnerability
        """

        hpfl=hpflogger(hpfserver, hpfport, hpfident, hpfsecret, hpfchannel, serverid, verbose)

        def alert(cls, host, port, payloads):
            logger.critical({
                 'timestamp': datetime.datetime.utcnow().isoformat(),
                 'src_ip': host,
                 'src_port': port,
                 'payload_printable': payloads,
            })
            #log to hpfeeds
            hpfl.log("critical", {
                 'src': host,
                 'spt': port,
                 'data': payloads,
             })

        if verbose:
            logger.setLevel(logging.DEBUG)

        requestHandler = WebLogicHandler
        requestHandler.alert_function = alert
        requestHandler.logger = logger
        requestHandler.hpfl = hpfl

        def log_date_time_string():
            """Return the current time formatted for logging."""
            now = datetime.datetime.now().isoformat()
            return now

        def ike():
            ike_server.start(host, ike_port, alert, logger, hpfl)
        t = threading.Thread(target=ike)
        t.daemon = True
        t.start()

        httpd = HTTPServer((host, port), requestHandler)
        if enable_ssl:
            import ssl
            if not cert:
                import gencert
                cert = gencert.gencert()
            httpd.socket = ssl.wrap_socket(httpd.socket, certfile=cert, server_side=True)

        logger.info('Starting server on port {:d}/tcp, use <Ctrl-C> to stop'.format(port))
        hpfl.log('info', 'Starting server on port {:d}/tcp, use <Ctrl-C> to stop'.format(port))

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        logger.info('Stopping server.')
        hpfl.log('info', 'Stopping server.')

        httpd.server_close()

    start()
