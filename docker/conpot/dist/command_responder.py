# Copyright (C) 2013  Daniel creo Haslinger <creo-conpot@blackmesa.at>
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
import time
import random

from datetime import datetime

from html.parser import HTMLParser
from socketserver import ThreadingMixIn

import http.server
import http.client
import os
from lxml import etree
from conpot.helpers import str_to_bytes
import conpot.core as conpot_core
import gevent


logger = logging.getLogger(__name__)


class HTTPServer(http.server.BaseHTTPRequestHandler):

    def log(self, version, request_type, addr, request, response=None):

        session = conpot_core.get_session('http', addr[0], addr[1], self.connection._sock.getsockname()[0], self.connection._sock.getsockname()[1])

        log_dict = {'remote': addr,
                    'timestamp': datetime.utcnow(),
                    'data_type': 'http',
                    'dst_port': self.server.server_port,
                    'data': {0: {'request': '{0} {1}: {2}'.format(version, request_type, request)}}}

        logger.info('%s %s request from %s: %s. %s', version, request_type, addr, request, session.id)

        if response:
            logger.info('%s response to %s: %s. %s', version, addr, response, session.id)
            log_dict['data'][0]['response'] = '{0} response: {1}'.format(version, response)
            session.add_event({'request': str(request), 'response': str(response)})
        else:
            session.add_event({'request': str(request)})

        # FIXME: Proper logging

    def get_entity_headers(self, rqfilename, headers, configuration):

        xml_headers = configuration.xpath(
            '//http/htdocs/node[@name="' + rqfilename + '"]/headers/*'
        )

        if xml_headers:

            # retrieve all headers assigned to this entity
            for header in xml_headers:
                headers.append((header.attrib['name'], header.text))

        return headers

    def get_trigger_appendix(self, rqfilename, rqparams, configuration):

        xml_triggers = configuration.xpath(
            '//http/htdocs/node[@name="' + rqfilename + '"]/triggers/*'
        )

        if xml_triggers:
            paramlist = rqparams.split('&')

            # retrieve all subselect triggers assigned to this entity
            for triggers in xml_triggers:

                triggerlist = triggers.text.split(';')
                trigger_missed = False

                for trigger in triggerlist:
                    if not trigger in paramlist:
                        trigger_missed = True

                if not trigger_missed:
                    return triggers.attrib['appendix']

        return None

    def get_entity_trailers(self, rqfilename, configuration):

        trailers = []
        xml_trailers = configuration.xpath(
            '//http/htdocs/node[@name="' + rqfilename + '"]/trailers/*'
        )

        if xml_trailers:

            # retrieve all headers assigned to this entity
            for trailer in xml_trailers:
                trailers.append((trailer.attrib['name'], trailer.text))

        return trailers

    def get_status_headers(self, status, headers, configuration):

        xml_headers = configuration.xpath('//http/statuscodes/status[@name="' +
                                          str(status) + '"]/headers/*')

        if xml_headers:

            # retrieve all headers assigned to this status
            for header in xml_headers:
                headers.append((header.attrib['name'], header.text))

        return headers

    def get_status_trailers(self, status, configuration):

        trailers = []
        xml_trailers = configuration.xpath(
            '//http/statuscodes/status[@name="' + str(status) + '"]/trailers/*'
        )

        if xml_trailers:

            # retrieve all trailers assigned to this status
            for trailer in xml_trailers:
                trailers.append((trailer.attrib['name'], trailer.text))

        return trailers

    def send_response(self, code, message=None):
        """Send the response header and log the response code.
        This function is overloaded to change the behaviour when
        loggers and sending default headers.
        """

        # replace integrated loggers with conpot logger..
        # self.log_request(code)

        if message is None:
            if code in self.responses:
                message = self.responses[code][0]
            else:
                message = ''

        if self.request_version != 'HTTP/0.9':
            msg = str_to_bytes("{} {} {}\r\n".format(self.protocol_version, code, message))
            self.wfile.write(msg)

        # the following two headers are omitted, which is why we override
        # send_response() at all. We do this one on our own...

        # - self.send_header('Server', self.version_string())
        # - self.send_header('Date', self.date_time_string())

    def substitute_template_fields(self, payload):

        # initialize parser with our payload
        parser = TemplateParser(payload)

        # triggers the parser, just in case of open / incomplete tags..
        parser.close()

        # retrieve and return (substituted) payload
        return parser.payload

    def load_status(self, status, requeststring, requestheaders, headers, configuration, docpath, method='GET', body=None):
        """Retrieves headers and payload for a given status code.
           Certain status codes can be configured to forward the
           request to a remote system. If not available, generate
           a minimal response"""

        # handle PROXY tag
        entity_proxy = configuration.xpath('//http/statuscodes/status[@name="' +
                                           str(status) +
                                           '"]/proxy')

        if entity_proxy:
            source = 'proxy'
            target = entity_proxy[0].xpath('./text()')[0]
        else:
            source = 'filesystem'

        # handle TARPIT tag
        entity_tarpit = configuration.xpath(
            '//http/statuscodes/status[@name="' + str(status) + '"]/tarpit'
        )

        if entity_tarpit:
            tarpit = self.server.config_sanitize_tarpit(entity_tarpit[0].xpath('./text()')[0])
        else:
            tarpit = None

        # check if we have to delay further actions due to global or local TARPIT configuration
        if tarpit is not None:
            # this node has its own delay configuration
            self.server.do_tarpit(tarpit)
        else:
            # no delay configuration for this node. check for global latency
            if self.server.tarpit is not None:
                # fall back to the globally configured latency
                self.server.do_tarpit(self.server.tarpit)

        # If the requested resource resides on our filesystem,
        # we try retrieve all metadata and the resource itself from there.
        if source == 'filesystem':

            # retrieve headers from entities configuration block
            headers = self.get_status_headers(status, headers, configuration)

            # retrieve headers from entities configuration block
            trailers = self.get_status_trailers(status, configuration)

            # retrieve payload directly from filesystem, if possible.
            # If this is not possible, return an empty, zero sized string.
            try:
                if not isinstance(status, int):
                    status = status.value
                with open(os.path.join(docpath, 'statuscodes', str(int(status)) + '.status'), 'rb') as f:
                    payload = f.read()

            except IOError as e:
                logger.exception('%s', e)
                payload = ''

            # there might be template data that can be substituted within the
            # payload. We only substitute data that is going to be displayed
            # by the browser:

            # perform template substitution on payload
            payload = self.substitute_template_fields(payload)

            # How do we transport the content?
            chunked_transfer = configuration.xpath('//http/htdocs/node[@name="' +
                                                   str(status) + '"]/chunks')

            if chunked_transfer:
                # Append a chunked transfer encoding header
                headers.append(('Transfer-Encoding', 'chunked'))
                chunks = str(chunked_transfer[0].xpath('./text()')[0])
            else:
                # Calculate and append a content length header
                headers.append(('Content-Length', payload.__len__()))
                chunks = '0'

            return status, headers, trailers, payload, chunks

        # the requested status code is configured to forward the
        # originally targeted resource to a remote system.

        elif source == 'proxy':

            # open a connection to the remote system.
            # If something goes wrong, fall back to 503.

            # NOTE: we use try:except here because there is no perfect
            # platform independent way to check file accessibility.

            trailers = []
            chunks = '0'

            try:
                # Modify a few headers to fit our new destination and the fact
                # that we're proxying while being unaware of any session foo..
                requestheaders['Host'] = target
                requestheaders['Connection'] = 'close'

                remotestatus = 0
                conn = http.client.HTTPConnection(target)
                conn.request(method, requeststring, body, dict(requestheaders))
                response = conn.getresponse()

                remotestatus = int(response.status)
                headers = response.getheaders()   # We REPLACE the headers to avoid duplicates!
                payload = response.read()

                # WORKAROUND: to get around a strange httplib-behaviour when it comes
                # to chunked transfer encoding, we replace the chunked-header with a
                # valid Content-Length header:

                for i, header in enumerate(headers):

                    if header[0].lower() == 'transfer-encoding' and header[1].lower() == 'chunked':
                        del headers[i]
                        break

                status = remotestatus

            except:

                # before falling back to 503, we check if we are ALREADY dealing with a 503
                # to prevent an infinite request handling loop...

                if status != 503:

                    # we're handling another error here.
                    # generate a 503 response from configuration.
                    (status, headers, trailers, payload, chunks) = self.load_status(503,
                                                                                    requeststring,
                                                                                    self.headers,
                                                                                    headers,
                                                                                    configuration,
                                                                                    docpath)

                else:

                    # oops, we're heading towards an infinite loop here,
                    # generate a minimal 503 response regardless of the configuration.
                    status = 503
                    payload = ''
                    chunks = '0'
                    headers.append(('Content-Length', 0))

            return status, headers, trailers, payload, chunks

    def load_entity(self, requeststring, headers, configuration, docpath):
        """
        Retrieves status, headers and payload for a given entity, that
        can be stored either local or on a remote system
        """

        # extract filename and GET parameters from request string
        rqfilename = requeststring.partition('?')[0]
        rqparams = requeststring.partition('?')[2]

        # handle ALIAS tag
        entity_alias = configuration.xpath(
            '//http/htdocs/node[@name="' + rqfilename + '"]/alias'
        )
        if entity_alias:
            rqfilename = entity_alias[0].xpath('./text()')[0]

        # handle SUBSELECT tag
        rqfilename_appendix = self.get_trigger_appendix(rqfilename, rqparams, configuration)
        if rqfilename_appendix:
            rqfilename += '_' + rqfilename_appendix

        # handle PROXY tag
        entity_proxy = configuration.xpath(
            '//http/htdocs/node[@name="' + rqfilename + '"]/proxy'
        )
        if entity_proxy:
            source = 'proxy'
            target = entity_proxy[0].xpath('./text()')[0]
        else:
            source = 'filesystem'

        # handle TARPIT tag
        entity_tarpit = configuration.xpath(
            '//http/htdocs/node[@name="' + rqfilename + '"]/tarpit'
        )
        if entity_tarpit:
            tarpit = self.server.config_sanitize_tarpit(entity_tarpit[0].xpath('./text()')[0])
        else:
            tarpit = None

        # check if we have to delay further actions due to global or local TARPIT configuration
        if tarpit is not None:
            # this node has its own delay configuration
            self.server.do_tarpit(tarpit)
        else:
            # no delay configuration for this node. check for global latency
            if self.server.tarpit is not None:
                # fall back to the globally configured latency
                self.server.do_tarpit(self.server.tarpit)

        # If the requested resource resides on our filesystem,
        # we try retrieve all metadata and the resource itself from there.
        if source == 'filesystem':

            # handle STATUS tag
            # ( filesystem only, since proxied requests come with their own status )
            entity_status = configuration.xpath(
                '//http/htdocs/node[@name="' + rqfilename + '"]/status'
            )
            if entity_status:
                status = int(entity_status[0].xpath('./text()')[0])
            else:
                status = 200

            # retrieve headers from entities configuration block
            headers = self.get_entity_headers(rqfilename, headers, configuration)

            # retrieve trailers from entities configuration block
            trailers = self.get_entity_trailers(rqfilename, configuration)

            # retrieve payload directly from filesystem, if possible.
            # If this is not possible, return an empty, zero sized string.
            if os.path.isabs(rqfilename):
                relrqfilename = rqfilename[1:]
            else:
                relrqfilename = rqfilename

            try:
                with open(os.path.join(docpath, 'htdocs', relrqfilename), 'rb') as f:
                    payload = f.read()

            except IOError as e:
                if not os.path.isdir(os.path.join(docpath, 'htdocs', relrqfilename)):
                    logger.error('Failed to get template content: %s', e)
                payload = ''

            # there might be template data that can be substituted within the
            # payload. We only substitute data that is going to be displayed
            # by the browser:

            templated = False
            for header in headers:
                if header[0].lower() == 'content-type' and header[1].lower() == 'text/html':
                    templated = True

            if templated:
                # perform template substitution on payload
                payload = self.substitute_template_fields(payload)

            # How do we transport the content?
            chunked_transfer = configuration.xpath(
                '//http/htdocs/node[@name="' + rqfilename + '"]/chunks'
            )

            if chunked_transfer:
                # Calculate and append a chunked transfer encoding header
                headers.append(('Transfer-Encoding', 'chunked'))
                chunks = str(chunked_transfer[0].xpath('./text()')[0])
            else:
                # Calculate and append a content length header
                headers.append(('Content-Length', payload.__len__()))
                chunks = '0'

            return status, headers, trailers, payload, chunks

        # the requested resource resides on another server,
        # so we act as a proxy between client and target system

        elif source == 'proxy':

            # open a connection to the remote system.
            # If something goes wrong, fall back to 503

            trailers = []

            try:
                conn = http.client.HTTPConnection(target)
                conn.request("GET", requeststring)
                response = conn.getresponse()

                status = int(response.status)
                headers = response.getheaders()    # We REPLACE the headers to avoid duplicates!
                payload = response.read()
                chunks = '0'

            except:
                status = 503
                (status, headers, trailers, payload, chunks) = self.load_status(status,
                                                                                requeststring,
                                                                                self.headers,
                                                                                headers,
                                                                                configuration,
                                                                                docpath)

            return status, headers, trailers, payload, chunks

    def send_chunked(self, chunks, payload, trailers):
        """Send payload via chunked transfer encoding to the
        client, followed by eventual trailers."""

        chunk_list = chunks.split(',')
        pointer = 0
        for cwidth in chunk_list:
            cwidth = int(cwidth)
            # send chunk length indicator
            self.wfile.write(format(cwidth, 'x').upper() + "\r\n")
            # send chunk payload
            self.wfile.write(payload[pointer:pointer + cwidth] + "\r\n")
            pointer += cwidth

        # is there another chunk that has not been configured? Send it anyway for the sake of completeness..
        if len(payload) > pointer:
            # send chunk length indicator
            self.wfile.write(format(len(payload) - pointer, 'x').upper() + "\r\n")
            # send chunk payload
            self.wfile.write(payload[pointer:] + "\r\n")

        # we're done with the payload. Send a zero chunk as EOF indicator
        self.wfile.write('0'+"\r\n")

        # if there are trailing headers :-) we send them now..
        for trailer in trailers:
            self.wfile.write("%s: %s\r\n" % (trailer[0], trailer[1]))

        # and finally, the closing ceremony...
        self.wfile.write("\r\n")

    def send_error(self, code, message=None):
        """Send and log an error reply.
        This method is overloaded to make use of load_status()
        to allow handling of "Unsupported Method" errors.
        """

        headers = []
        headers.extend(self.server.global_headers)
        configuration = self.server.configuration
        docpath = self.server.docpath

        if not hasattr(self, 'headers'):
            self.headers = self.MessageClass(self.rfile, 0)

        trace_data_length = self.headers.get('content-length')
        unsupported_request_data = None

        if trace_data_length:
            unsupported_request_data = self.rfile.read(int(trace_data_length))

        # there are certain situations where variables are (not yet) registered
        # ( e.g. corrupted request syntax ). In this case, we set them manually.
        if hasattr(self, 'path') and self.path is not None:
            requeststring = self.path
        else:
            requeststring = ''
            self.path = None
            if message is not None:
                logger.info(message)

        # generate the appropriate status code, header and payload
        (status, headers, trailers, payload, chunks) = self.load_status(code,
                                                                        requeststring.partition('?')[0],
                                                                        self.headers,
                                                                        headers,
                                                                        configuration,
                                                                        docpath)

        # send http status to client
        self.send_response(status)

        # send all headers to client
        for header in headers:
            self.send_header(header[0], header[1])

        self.end_headers()

        # decide upon sending content as a whole or chunked
        if chunks == '0':
            # send payload as a whole to the client
            if type(payload) != bytes:
                payload = payload.encode()
            self.wfile.write(payload)
        else:
            # send payload in chunks to the client
            self.send_chunked(chunks, payload, trailers)

        # loggers
        self.log(self.request_version, self.command, self.client_address, (self.path,
                                                                           self.headers._headers,
                                                                           unsupported_request_data), status)

    def do_TRACE(self):
        """Handle TRACE requests."""

        # fetch configuration dependent variables from server instance
        headers = []
        headers.extend(self.server.global_headers)
        configuration = self.server.configuration
        docpath = self.server.docpath

        # retrieve TRACE body data
        # ( sticking to the HTTP protocol, there should not be any body in TRACE requests,
        #   an attacker could though use the body to inject data if not flushed correctly,
        #   which is done by accessing the data like we do now - just to be secure.. )

        trace_data_length = self.headers.get('content-length')
        trace_data = None

        if trace_data_length:
            trace_data = self.rfile.read(int(trace_data_length))

        # check configuration: are we allowed to use this method?
        if self.server.disable_method_trace is True:

            # Method disabled by configuration. Fall back to 501.
            status = 501
            (status, headers, trailers, payload, chunks) = self.load_status(status,
                                                                            self.path,
                                                                            self.headers,
                                                                            headers,
                                                                            configuration,
                                                                            docpath)

        else:

            # Method is enabled
            status = 200
            payload = ''
            headers.append(('Content-Type', 'message/http'))

            # Gather all request data and return it to sender..
            for rqheader in self.headers:
                payload = payload + str(rqheader) + ': ' + self.headers.get(rqheader) + "\n"

        # send initial HTTP status line to client
        self.send_response(status)

        # send all headers to client
        for header in headers:
            self.send_header(header[0], header[1])

        self.end_headers()

        # send payload (the actual content) to client
        if type(payload) != bytes:
            payload = payload.encode()
        self.wfile.write(payload)

        # loggers
        self.log(self.request_version,
                 self.command,
                 self.client_address,
                 (self.path, self.headers._headers, trace_data),
                 status)

    def do_HEAD(self):
        """Handle HEAD requests."""

        # fetch configuration dependent variables from server instance
        headers = list()
        headers.extend(self.server.global_headers)
        configuration = self.server.configuration
        docpath = self.server.docpath

        # retrieve HEAD body data
        # ( sticking to the HTTP protocol, there should not be any body in HEAD requests,
        #   an attacker could though use the body to inject data if not flushed correctly,
        #   which is done by accessing the data like we do now - just to be secure.. )

        head_data_length = self.headers.get('content-length')
        head_data = None

        if head_data_length:
            head_data = self.rfile.read(int(head_data_length))

        # check configuration: are we allowed to use this method?
        if self.server.disable_method_head is True:

            # Method disabled by configuration. Fall back to 501.
            status = 501
            (status, headers, trailers, payload, chunks) = self.load_status(status,
                                                                            self.path,
                                                                            self.headers,
                                                                            headers,
                                                                            configuration,
                                                                            docpath)

        else:

            # try to find a configuration item for this GET request
            entity_xml = configuration.xpath(
                '//http/htdocs/node[@name="'
                + self.path.partition('?')[0] + '"]'
            )

            if entity_xml:
                # A config item exists for this entity. Handle it..
                (status, headers, trailers, payload, chunks) = self.load_entity(self.path,
                                                                                headers,
                                                                                configuration,
                                                                                docpath)

            else:
                # No config item could be found. Fall back to a standard 404..
                status = 404
                (status, headers, trailers, payload, chunks) = self.load_status(status,
                                                                                self.path,
                                                                                self.headers,
                                                                                headers,
                                                                                configuration,
                                                                                docpath)

        # send initial HTTP status line to client
        self.send_response(status)

        # send all headers to client
        for header in headers:
            self.send_header(header[0], header[1])

        self.end_headers()

        # loggers
        self.log(self.request_version,
                 self.command,
                 self.client_address,
                 (self.path, self.headers._headers, head_data),
                 status)

    def do_OPTIONS(self):
        """Handle OPTIONS requests."""

        # fetch configuration dependent variables from server instance
        headers = []
        headers.extend(self.server.global_headers)
        configuration = self.server.configuration
        docpath = self.server.docpath

        # retrieve OPTIONS body data
        # ( sticking to the HTTP protocol, there should not be any body in HEAD requests,
        #   an attacker could though use the body to inject data if not flushed correctly,
        #   which is done by accessing the data like we do now - just to be secure.. )

        options_data_length = self.headers.get('content-length')
        options_data = None

        if options_data_length:
            options_data = self.rfile.read(int(options_data_length))

        # check configuration: are we allowed to use this method?
        if self.server.disable_method_options is True:

            # Method disabled by configuration. Fall back to 501.
            status = 501
            (status, headers, trailers, payload, chunks) = self.load_status(status,
                                                                            self.path,
                                                                            self.headers,
                                                                            headers,
                                                                            configuration,
                                                                            docpath)

        else:

            status = 200
            payload = ''

            # Add ALLOW header to response. GET, POST and OPTIONS are static, HEAD and TRACE are dynamic
            allowed_methods = 'GET'

            if self.server.disable_method_head is False:
                # add head to list of allowed methods
                allowed_methods += ',HEAD'

            allowed_methods += ',POST,OPTIONS'

            if self.server.disable_method_trace is False:
                allowed_methods += ',TRACE'

            headers.append(('Allow', allowed_methods))

            # Calculate and append a content length header
            headers.append(('Content-Length', payload.__len__()))

            # Append CC header
            headers.append(('Connection', 'close'))

            # Append CT header
            headers.append(('Content-Type', 'text/html'))

        # send initial HTTP status line to client
        self.send_response(status)

        # send all headers to client
        for header in headers:
            self.send_header(header[0], header[1])

        self.end_headers()

        # loggers
        self.log(self.request_version,
                 self.command,
                 self.client_address,
                 (self.path, self.headers._headers, options_data),
                 status)

    def do_GET(self):
        """Handle GET requests"""

        # fetch configuration dependent variables from server instance
        headers = []
        headers.extend(self.server.global_headers)
        configuration = self.server.configuration
        docpath = self.server.docpath

        # retrieve GET body data
        # ( sticking to the HTTP protocol, there should not be any body in GET requests,
        #   an attacker could though use the body to inject data if not flushed correctly,
        #   which is done by accessing the data like we do now - just to be secure.. )

        get_data_length = self.headers.get('content-length')
        get_data = None

        if get_data_length:
            get_data = self.rfile.read(int(get_data_length))

        # try to find a configuration item for this GET request
        logger.debug('Trying to handle GET to resource <%s>, initiated by %s', self.path, self.client_address)
        entity_xml = configuration.xpath(
            '//http/htdocs/node[@name="' + self.path.partition('?')[0] + '"]'
        )

        if entity_xml:
            # A config item exists for this entity. Handle it..
            (status, headers, trailers, payload, chunks) = self.load_entity(self.path,
                                                                            headers,
                                                                            configuration,
                                                                            docpath)

        else:
            # No config item could be found. Fall back to a standard 404..
            status = 404
            (status, headers, trailers, payload, chunks) = self.load_status(status,
                                                                            self.path,
                                                                            self.headers,
                                                                            headers,
                                                                            configuration,
                                                                            docpath,
                                                                            'GET')

        # send initial HTTP status line to client
        self.send_response(status)

        # send all headers to client
        for header in headers:
            self.send_header(header[0], header[1])

        self.end_headers()

        # decide upon sending content as a whole or chunked
        if chunks == '0':
            # send payload as a whole to the client
            self.wfile.write(str_to_bytes(payload))
        else:
            # send payload in chunks to the client
            self.send_chunked(chunks, payload, trailers)

        # loggers
        self.log(self.request_version,
                 self.command,
                 self.client_address,
                 (self.path, self.headers._headers, get_data),
                 status)

    def do_POST(self):
        """Handle POST requests"""

        # fetch configuration dependent variables from server instance
        headers = list()
        headers.extend(self.server.global_headers)
        configuration = self.server.configuration
        docpath = self.server.docpath

        # retrieve POST data ( important to flush request buffers )
        post_data_length = self.headers.get('content-length')
        post_data = None

        if post_data_length:
            post_data = self.rfile.read(int(post_data_length))

        # try to find a configuration item for this POST request
        entity_xml = configuration.xpath(
            '//http/htdocs/node[@name="' + self.path.partition('?')[0] + '"]'
        )

        if entity_xml:
            # A config item exists for this entity. Handle it..
            (status, headers, trailers, payload, chunks) = self.load_entity(self.path,
                                                                            headers,
                                                                            configuration,
                                                                            docpath)

        else:
            # No config item could be found. Fall back to a standard 404..
            status = 404
            (status, headers, trailers, payload, chunks) = self.load_status(status,
                                                                            self.path,
                                                                            self.headers,
                                                                            headers,
                                                                            configuration,
                                                                            docpath,
                                                                            'POST',
                                                                            post_data)

        # send initial HTTP status line to client
        self.send_response(status)

        # send all headers to client
        for header in headers:
            self.send_header(header[0], header[1])

        self.end_headers()

        # decide upon sending content as a whole or chunked
        if chunks == '0':
            # send payload as a whole to the client
            if type(payload) != bytes:
                payload = payload.encode()
            self.wfile.write(payload)
        else:
            # send payload in chunks to the client
            self.send_chunked(chunks, payload, trailers)

        # loggers
        self.log(self.request_version,
                 self.command,
                 self.client_address,
                 (self.path, self.headers._headers, post_data),
                 status)


class TemplateParser(HTMLParser):
    def __init__(self, data):
        self.databus = conpot_core.get_databus()
        if type(data) == bytes:
            data = data.decode()
        self.data = data
        HTMLParser.__init__(self)
        self.payload = self.data
        self.feed(self.data)

    def handle_startendtag(self, tag, attrs):
        """ handles template tags provided in XHTML notation.

            Expected format:    <condata source="(engine)" key="(descriptor)" />
            Example:            <condata source="databus" key="SystemDescription" />

            at the moment, the parser is space- and case-sensitive(!),
            this could be improved by using REGEX for replacing the template tags
            with actual values.
        """

        source = ''
        key = ''

        # only parse tags that are conpot template tags ( <condata /> )
        if tag == 'condata':

            # initialize original tag (needed for value replacement)
            origin = '<' + tag

            for attribute in attrs:

                # extend original tag
                origin = origin + ' ' + attribute[0] + '="' + attribute[1] + '"'

                # fill variables with all meta information needed to
                # gather actual data from the other engines (databus, modbus, ..)
                if attribute[0] == 'source':
                    source = attribute[1]
                elif attribute[0] == 'key':
                    key = attribute[1]

            # finalize original tag
            origin += ' />'

            # we really need a key in order to do our work..
            if key:
                # deal with databus powered tags:
                if source == 'databus':
                    self.result = self.databus.get_value(key)
                    self.payload = self.payload.replace(origin, str(self.result))

                # deal with eval powered tags:
                elif source == 'eval':
                    result = ''
                    # evaluate key
                    try:
                        result = eval(key)
                    except Exception as e:
                        logger.exception(e)
                    self.payload = self.payload.replace(origin, result)


class ThreadedHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    """Handle requests in a separate thread."""


class SubHTTPServer(ThreadedHTTPServer):
    """this class is necessary to allow passing custom request handler into
       the RequestHandlerClass"""
    daemon_threads = True

    def __init__(self, server_address, RequestHandlerClass, template, docpath):
        http.server.HTTPServer.__init__(self, server_address, RequestHandlerClass)

        self.docpath = docpath

        # default configuration
        self.update_header_date = True             # this preserves authenticity
        self.disable_method_head = False
        self.disable_method_trace = False
        self.disable_method_options = False
        self.tarpit = '0'

        # load the configuration from template and parse it
        # for the first time in order to reduce further handling..
        self.configuration = etree.parse(template)

        xml_config = self.configuration.xpath('//http/global/config/*')
        if xml_config:

            # retrieve all global configuration entities
            for entity in xml_config:

                if entity.attrib['name'] == 'protocol_version':
                    RequestHandlerClass.protocol_version = entity.text

                elif entity.attrib['name'] == 'update_header_date':
                    if entity.text.lower() == 'false':
                        # DATE header auto update disabled by configuration
                        self.update_header_date = False
                    elif entity.text.lower() == 'true':
                        # DATE header auto update enabled by configuration
                        self.update_header_date = True

                elif entity.attrib['name'] == 'disable_method_head':
                    if entity.text.lower() == 'false':
                        # HEAD method enabled by configuration
                        self.disable_method_head = False
                    elif entity.text.lower() == 'true':
                        # HEAD method disabled by configuration
                        self.disable_method_head = True

                elif entity.attrib['name'] == 'disable_method_trace':
                    if entity.text.lower() == 'false':
                        # TRACE method enabled by configuration
                        self.disable_method_trace = False
                    elif entity.text.lower() == 'true':
                        # TRACE method disabled by configuration
                        self.disable_method_trace = True

                elif entity.attrib['name'] == 'disable_method_options':
                    if entity.text.lower() == 'false':
                        # OPTIONS method enabled by configuration
                        self.disable_method_options = False
                    elif entity.text.lower() == 'true':
                        # OPTIONS method disabled by configuration
                        self.disable_method_options = True

                elif entity.attrib['name'] == 'tarpit':
                    if entity.text:
                        self.tarpit = self.config_sanitize_tarpit(entity.text)

        # load global headers from XML
        self.global_headers = []
        xml_headers = self.configuration.xpath('//http/global/headers/*')
        if xml_headers:

            # retrieve all headers assigned to this status code
            for header in xml_headers:
                if header.attrib['name'].lower() == 'date' and self.update_header_date is True:
                    # All HTTP date/time stamps MUST be represented in Greenwich Mean Time (GMT),
                    # without exception ( RFC-2616 )
                    self.global_headers.append((header.attrib['name'],
                                                time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())))
                else:
                    self.global_headers.append((header.attrib['name'], header.text))

    def config_sanitize_tarpit(self, value):

        # checks tarpit value for being either a single int or float,
        # or a series of two concatenated integers and/or floats seperated by semicolon and returns
        # either the (sanitized) value or zero.

        if value is not None:

            x, _, y = value.partition(';')

            try:
                _ = float(x)
            except ValueError:
                # first value is invalid, ignore the whole setting.
                logger.error("Invalid tarpit value: '%s'. Assuming no latency.", value)
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

    def do_tarpit(self, delay):

        # sleeps the thread for $delay ( should be either 1 float to apply a static period of time to sleep,
        # or 2 floats seperated by semicolon to sleep a randomized period of time determined by ( rand[x;y] )

        lbound, _, ubound = delay.partition(";")

        if not lbound or lbound is None:
            # no lower boundary found. Assume zero latency
            pass
        elif not ubound or ubound is None:
            # no upper boundary found. Assume static latency
            gevent.sleep(float(lbound))
        else:
            # both boundaries found. Assume random latency between lbound and ubound
            gevent.sleep(random.uniform(float(lbound), float(ubound)))


class CommandResponder(object):

    def __init__(self, host, port, template, docpath):

        # Create HTTP server class
        self.httpd = SubHTTPServer((host, port), HTTPServer, template, docpath)
        self.server_port = self.httpd.server_port

    def serve_forever(self):
        self.httpd.serve_forever()

    def stop(self):
        logging.info("HTTP server will shut down gracefully as soon as all connections are closed.")
        self.httpd.shutdown()
