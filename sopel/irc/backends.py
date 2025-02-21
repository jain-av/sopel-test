# coding=utf-8
# Copyright 2019, Florian Strzelecki <florian.strzelecki@gmail.com>
#
# Licensed under the Eiffel Forum License 2.
# When working on core IRC protocol related features, consult protocol
# documentation at http://www.irchelp.org/irchelp/rfc/
from __future__ import absolute_import, division, print_function, unicode_literals

import asyncio
import datetime
import logging
import os
import socket
import sys
import threading

from sopel import loader, plugin
from sopel.tools import jobs
from .abstract_backends import AbstractIRCBackend
from .utils import get_cnames

try:
    import ssl
    if not hasattr(ssl, 'match_hostname'):
        # Attempt to import ssl_match_hostname from python-backports
        # TODO: Remove when dropping Python 2 support
        import backports.ssl_match_hostname
        ssl.match_hostname = backports.ssl_match_hostname.match_hostname
        ssl.CertificateError = backports.ssl_match_hostname.CertificateError
    has_ssl = True
except ImportError:
    # no SSL support
    has_ssl = False

if sys.version_info.major >= 3:
    unicode = str


LOGGER = logging.getLogger(__name__)


@plugin.thread(False)
@plugin.interval(5)
async def _send_ping(backend):
    if not backend.is_connected():
        return

    events = []
    need_ping = True

    # Ensure we have a time to check first
    if backend.last_event_at:
        events.append(backend.last_event_at)

    if backend.last_ping_at:
        events.append(backend.last_ping_at)

    # At least a PING was sent, or a message was received
    if events:
        last_event = max(events)
        dt = datetime.datetime.utcnow() - last_event
        time_passed = dt.total_seconds()
        need_ping = time_passed > backend.ping_interval

    # Send PING only if needed
    if need_ping:
        try:
            backend.send_ping(backend.host)
            backend.last_ping_at = datetime.datetime.utcnow()
        except socket.error:
            LOGGER.exception('Socket error on PING')


@plugin.thread(False)
@plugin.interval(10)
async def _check_timeout(backend):
    if not backend.is_connected():
        return
    dt = datetime.datetime.utcnow() - backend.last_event_at
    time_passed = dt.total_seconds()
    if time_passed > backend.server_timeout:
        LOGGER.error(
            'Server timeout detected after %ss; closing.', time_passed)
        # discard buffers: no need to read/write anything more, just quit
        LOGGER.debug('Discard current buffers.')
        backend.discard_buffers()
        # close now
        backend.handle_close()


class AsynchatBackend(AbstractIRCBackend):
    """IRC backend implementation using :mod:`asyncio`.

    :param bot: a Sopel instance
    :type bot: :class:`sopel.bot.Sopel`
    :param int server_timeout: connection timeout in seconds
    :param int ping_interval: ping interval in seconds

    The ``server_timeout`` option defaults to ``120`` seconds if not provided.

    The ``ping_interval`` defaults to ``server_timeout * 0.45`` if not specified.
    """
    def __init__(self, bot, server_timeout=None, ping_interval=None, **kwargs):
        AbstractIRCBackend.__init__(self, bot)
        self.writing_lock = threading.RLock()
        self.buffer = ''
        self.server_timeout = server_timeout or 120
        self.ping_interval = ping_interval or (self.server_timeout * 0.45)
        self.last_event_at = None
        self.last_ping_at = None
        self.host = None
        self.port = None
        self.source_address = None
        self.timeout_scheduler = jobs.Scheduler(self)
        self.transport = None  # asyncio.Transport
        self.protocol = None  # asyncio.Protocol
        self._connected = False

        # register timeout jobs
        self.register_timeout_jobs([
            _send_ping,
            _check_timeout,
        ])

    def is_connected(self):
        return self._connected

    def on_irc_error(self, pretrigger):
        if self.bot.hasquit:
            # discard buffers: no need to read/write anything more, just quit
            LOGGER.debug('Discard current buffers.')
            self.discard_buffers()
            # close now
            self.handle_close()

    def irc_send(self, data):
        """Send an IRC line as raw ``data`` to the socket connection.

        :param bytes data: raw line to send

        This uses :meth:`asyncore.dispatcher.send` method to send ``data``
        directly. This method is thread-safe.
        """
        with self.writing_lock:
            if self.transport:
                self.transport.write(data)
            else:
                LOGGER.warning("Attempted to send data without a transport.")

    async def run_forever(self):
        """Run forever."""
        LOGGER.debug('Running forever.')
        # asyncio.run(self._async_main())  # moved to main bot
        pass

    def register_timeout_jobs(self, handlers):
        """Register the timeout handlers for the timeout scheduler."""
        for handler in handlers:
            loader.clean_callable(handler, self.bot.settings)
            job = jobs.Job.from_callable(self.bot.settings, handler)
            self.timeout_scheduler.register(job)
            LOGGER.debug('Timeout Job registered: %s', str(job))

    async def initiate_connect(self, host, port, source_address):
        """Initiate IRC connection.

        :param str host: IRC server hostname
        :param int port: IRC server port
        :param str source_address: the source address from which to initiate
                                   the connection attempt
        """
        self.host = host
        self.port = port
        self.source_address = source_address

        LOGGER.info('Connecting to %s:%s...', host, port)
        loop = asyncio.get_event_loop()

        try:
            LOGGER.debug('Connection attempt')
            # create_connection returns (transport, protocol)
            transport, protocol = await loop.create_connection(
                lambda: IRCProtocol(self),
                host,
                port,
                local_addr=source_address
            )
            self.transport = transport
            self.protocol = protocol
            self._connected = True

        except socket.error as e:
            LOGGER.exception('Connection error: %s', e)
            self.handle_close()
        except Exception as e:
            LOGGER.exception('General connection error: %s', e)
            self.handle_close()

    def handle_connect(self):
        """Called when the active opener's socket actually makes a connection."""
        LOGGER.info('Connection accepted by the server...')
        self.timeout_scheduler.start()
        self.bot.on_connect()

    def handle_close(self):
        """Called when the connection must be closed."""
        LOGGER.debug('Stopping timeout watchdog')
        self.timeout_scheduler.stop()
        LOGGER.info('Closing connection')
        if self.transport:
            self.transport.close()
            self.transport = None
        self._connected = False
        self.bot.on_close()

    def handle_error(self):
        """Called when an exception is raised and not otherwise handled.

        This method is an override of :meth:`asyncore.dispatcher.handle_error`,
        the :class:`asynchat.async_chat` being a subclass of
        :class:`asyncore.dispatcher`.
        """
        LOGGER.info('Connection error...')
        self.bot.on_error()

    def collect_incoming_data(self, data):
        """Try to make sense of incoming data as Unicode.

        :param bytes data: the incoming raw bytes

        The incoming line is discarded (and thus ignored) if guessing the text
        encoding and decoding it fails.
        """
        # We can't trust clients to pass valid Unicode.
        try:
            data = unicode(data, encoding='utf-8')
        except UnicodeDecodeError:
            # not Unicode; let's try CP-1252
            try:
                data = unicode(data, encoding='cp1252')
            except UnicodeDecodeError:
                # Okay, let's try ISO 8859-1
                try:
                    data = unicode(data, encoding='iso8859-1')
                except UnicodeDecodeError:
                    # Discard line if encoding is unknown
                    return
        if data:
            self.bot.log_raw(data, '<<')
        self.buffer += data
        self.last_event_at = datetime.datetime.utcnow()

    def found_terminator(self):
        """Handle the end of an incoming message."""
        line = self.buffer
        if line.endswith('\r'):
            line = line[:-1]
        self.buffer = ''
        self.bot.on_message(line)

    def on_scheduler_error(self, scheduler, exc):
        """Called when the Job Scheduler fails."""
        LOGGER.exception('Error with the timeout scheduler: %s', exc)
        self.handle_close()

    def on_job_error(self, scheduler, job, exc):
        """Called when a job from the Job Scheduler fails."""
        LOGGER.exception('Error with the timeout scheduler: %s', exc)
        self.handle_close()

    def data_received(self, data):
        """Handle incoming data from the transport."""
        self.collect_incoming_data(data)
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            self.found_terminator()

    # Define connection_made/lost to satisfy the protocol
    def connection_made(self, transport):
        """Called when a connection is made."""
        self.transport = transport
        self.handle_connect()

    def connection_lost(self, exc):
        """Called when the connection is lost or closed."""
        self.handle_close()


class SSLAsynchatBackend(AsynchatBackend):
    """SSL-aware extension of :class:`AsynchatBackend`.

    :param bot: a Sopel instance
    :type bot: :class:`sopel.bot.Sopel`
    :param bool verify_ssl: whether to validate the IRC server's certificate
                            (default ``True``, for good reason)
    :param str ca_certs: filesystem path to a CA Certs file containing trusted
                         root certificates
    """
    def __init__(self, bot, verify_ssl=True, ca_certs=None, **kwargs):
        AsynchatBackend.__init__(self, bot, **kwargs)
        self.verify_ssl = verify_ssl
        self.ssl = None
        self.ca_certs = ca_certs

    async def initiate_connect(self, host, port, source_address):
        """Initiate IRC connection with SSL.

        :param str host: IRC server hostname
        :param int port: IRC server port
        :param str source_address: the source address from which to initiate
                                   the connection attempt
        """
        self.host = host
        self.port = port
        self.source_address = source_address

        LOGGER.info('Connecting to %s:%s with SSL...', host, port)
        loop = asyncio.get_event_loop()

        ssl_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH, cafile=self.ca_certs)
        if self.verify_ssl:
            ssl_context.check_hostname = True
        else:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        try:
            LOGGER.debug('Connection attempt')
            transport, protocol = await loop.create_connection(
                lambda: IRCProtocol(self),
                host,
                port,
                ssl=ssl_context,
                local_addr=source_address
            )
            self.transport = transport
            self.protocol = protocol
            self._connected = True

        except socket.error as e:
            LOGGER.exception('Connection error: %s', e)
            self.handle_close()
        except Exception as e:
            LOGGER.exception('General connection error: %s', e)
            self.handle_close()


class IRCProtocol(asyncio.Protocol):
    """Asyncio protocol for handling IRC communication."""
    def __init__(self, backend):
        self.backend = backend

    def connection_made(self, transport):
        """Called when a connection is made."""
        self.backend.connection_made(transport)

    def data_received(self, data):
        """Called when data is received."""
        self.backend.data_received(data)

    def connection_lost(self, exc):
        """Called when the connection is lost or closed."""
        self.backend.connection_lost(exc)

    def handle_connect(self):
        """Handle potential TLS connection."""
        # TODO: Refactor to use SSLContext and an appropriate PROTOCOL_* constant
        # See https://lgtm.com/rules/1507225275976/
        # These warnings are ignored for now, because we can't easily fix them
        # while maintaining compatibility with py2.7 AND 3.3+, but in Sopel 8
        # the supported range should narrow sufficiently to fix these for real.
        # Each Python version still generally selects the most secure protocol
        # version(s) it supports.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if self.verify_ssl:
            context.verify_mode = ssl.CERT_REQUIRED
            if self.ca_certs:
                context.load_verify_locations(self.ca_certs)
            context.check_hostname = True
        else:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        try:
            self.ssl = context.wrap_socket(self.socket,
                                           server_hostname=self.host)
            # connect to host specified in config first
            if self.verify_ssl:
                try:
                    ssl.match_hostname(self.ssl.getpeercert(), self.host)
                except ssl.CertificateError:
                    # the host in config and certificate don't match
                    LOGGER.error("hostname mismatch between configuration and certificate")
                    # check (via exception) if a CNAME matches as a fallback
                    has_matched = False
                    for hostname in get_cnames(self.host):
                        try:
                            ssl.match_hostname(self.ssl.getpeercert(), hostname)
                            LOGGER.warning(
                                "using {0} instead of {1} for TLS connection"
                                .format(hostname, self.host))
                            has_matched = True
                            break
                        except ssl.CertificateError:
                            pass

                    if not has_matched:
                        # everything is broken
                        LOGGER.error("Invalid certificate, no hostname matches.")
                        # TODO: refactor access to bot's settings
                        if hasattr(self.bot.settings.core, 'pid_file_path'):
                            # TODO: refactor to quit properly (no "os._exit")
                            os.unlink(self.bot.settings.core.pid_file_path)
                            os._exit(1)
        except OSError as e:
            LOGGER.exception('SSL handshake failed')
            self.handle_close()
            return
        self.set_socket(self.ssl)
        LOGGER.info('Connection accepted by the server...')
        LOGGER.debug('Starting job scheduler for connection timeout...')
        self.timeout_scheduler.start()
        self.bot.on_connect()

    def send(self, data):
        """SSL-aware override for :meth:`~asyncore.dispatcher.send`."""
        try:
            result = self.socket.send(data)
            return result
        except ssl.SSLError as why:
            if why.errno in (asyncore.EWOULDBLOCK, errno.ESRCH):
                return 0
            raise why

    def recv(self, buffer_size):
        """SSL-aware override for :meth:`~asyncore.dispatcher.recv`.

        From a (now deleted) blog post by Evan "K7FOS" Fosmark:
        https://k7fos.com/2010/09/ssl-support-in-asynchatasync_chat
        """
        try:
            data = self.socket.recv(buffer_size)
            if not data:
                self.handle_close()
                return b''
            return data
        except ssl.SSLError as why:
            if why.errno in (asyncore.ECONNRESET, asyncore.ENOTCONN,
                          asyncore.ESHUTDOWN):
                self.handle_close()
                return ''
            elif why.errno == errno.ENOENT:
                # Required in order to keep it non-blocking
                return b''
            else:
                raise
