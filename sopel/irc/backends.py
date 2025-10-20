# Copyright 2019, Florian Strzelecki <florian.strzelecki@gmail.com>
#
# Licensed under the Eiffel Forum License 2.
# When working on core IRC protocol related features, consult protocol
# documentation at http://www.irchelp.org/irchelp/rfc/
"""IRC connection backend implementations using asynchat/asyncore.

This module provides concrete implementations of the IRC connection backend
interface using Python's :mod:`asynchat` and :mod:`asyncore` libraries for
asynchronous socket I/O. These backends handle the low-level network
communication between Sopel and IRC servers.

**Backend Architecture:**

The module provides two main backend implementations:

1. :class:`AsynchatBackend` - Base implementation for plain TCP connections
2. :class:`SSLAsynchatBackend` - SSL/TLS-aware extension for secure connections

Both backends implement the :class:`~sopel.irc.abstract_backends.AbstractIRCBackend`
interface and inherit from :class:`asynchat.async_chat` to leverage Python's
asynchronous I/O framework.

**Why asynchat/asyncore:**

The :mod:`asynchat` library is used because it provides:

- Line-based protocol handling with automatic buffering and terminator detection
- Asynchronous I/O without threading complexity for each connection
- Built-in support for partial reads and writes in non-blocking mode
- Simple integration with asyncore's event loop via ``asyncore.loop()``

This allows Sopel to handle IRC messages (which are line-delimited by ``\\r\\n``)
efficiently without blocking on network I/O operations.

**Connection State Machine:**

The backend manages connections through the following state transitions::

    DISCONNECTED --> CONNECTING --> CONNECTED --> DISCONNECTED
         ^              |              |              |
         |              v              v              |
         +----------[error]--------[close]-----------+

State transitions are managed by these key methods:

- ``initiate_connect()`` - DISCONNECTED → CONNECTING: Creates socket and initiates connection
- ``handle_connect()`` - CONNECTING → CONNECTED: Connection accepted, starts timeout scheduler
- ``handle_close()`` - CONNECTED → DISCONNECTED: Closes socket and stops scheduler
- ``handle_error()`` - ANY → DISCONNECTED: Handles exceptions during any state

**Thread Safety:**

Connection operations use a reentrant lock (``threading.RLock``) to ensure
thread-safe writes to the socket. The ``irc_send()`` method acquires this lock
before calling the underlying ``send()`` method, allowing multiple threads to
safely queue outgoing IRC messages.

**Timeout Management:**

The backend uses a job scheduler to monitor connection health:

- Sends PING every ``ping_interval`` seconds (default: 45% of server timeout)
- Detects server timeout if no messages received for ``server_timeout`` seconds (default: 120s)
- Automatically closes connection on timeout to trigger reconnection logic

See :func:`_send_ping` and :func:`_check_timeout` for implementation details.
"""
from __future__ import annotations

import asynchat
import asyncore
import datetime
import errno
import inspect
import logging
import os
import socket
import ssl
import threading

from sopel.tools import jobs
from .abstract_backends import AbstractIRCBackend
from .utils import get_cnames


LOGGER = logging.getLogger(__name__)


def _send_ping(backend):
    """Send periodic PING to keep IRC connection alive and detect network issues.

    :param backend: the IRC backend instance to ping
    :type backend: :class:`AsynchatBackend`

    **Timing Logic:**

    This function implements a keepalive mechanism by sending PING messages at
    regular intervals. The timing is controlled by ``backend.ping_interval``
    (default: 54 seconds for a 120-second timeout).

    **Algorithm:**

    1. Skip if backend is not connected
    2. Find the most recent activity timestamp from:
       - ``last_event_at`` - last IRC message received from server
       - ``last_ping_at`` - last PING we sent to server
    3. Calculate seconds elapsed since most recent activity
    4. Send PING only if elapsed time exceeds ``ping_interval``

    **Example Scenario:**

    - ``server_timeout`` = 120 seconds
    - ``ping_interval`` = 54 seconds (120 * 0.45)
    - Last message received at T=0
    - At T=54s: PING sent (no messages received for 54s)
    - At T=60s: Server responds with PONG, updates ``last_event_at``
    - At T=114s: Another PING sent (54s since last event)

    This ensures the connection is tested well before the server timeout would
    trigger, while avoiding unnecessary PINGs when the connection is active.

    **Error Handling:**

    Socket errors during PING are logged but do not raise exceptions. The
    ``_check_timeout()`` function will detect the lack of response and close
    the connection if the server timeout is exceeded.

    .. note::
        This function is called every 5 seconds by the timeout scheduler
        registered in :meth:`AsynchatBackend.__init__`.
    """
    if not backend.is_connected():
        return

    events = []
    need_ping = True

    # Ensure we have a time to check first
    # Collect all activity timestamps to find the most recent
    if backend.last_event_at:
        events.append(backend.last_event_at)

    if backend.last_ping_at:
        events.append(backend.last_ping_at)

    # At least a PING was sent, or a message was received
    if events:
        # Use the most recent activity time to determine if we need to ping
        last_event = max(events)
        dt = datetime.datetime.utcnow() - last_event
        time_passed = dt.total_seconds()
        # Only ping if enough time has passed since last activity
        need_ping = time_passed > backend.ping_interval

    # Send PING only if needed
    if need_ping:
        try:
            backend.send_ping(backend.host)
            backend.last_ping_at = datetime.datetime.utcnow()
        except socket.error:
            # Log but don't raise - _check_timeout will handle connection failure
            LOGGER.exception('Socket error on PING')


def _check_timeout(backend):
    """Monitor connection health and close if server stops responding.

    :param backend: the IRC backend instance to monitor
    :type backend: :class:`AsynchatBackend`

    **Timeout Detection Logic:**

    This function implements connection timeout detection by monitoring the time
    elapsed since the last received message from the IRC server. If no messages
    have been received for longer than ``backend.server_timeout`` seconds
    (default: 120 seconds), the connection is forcibly closed.

    **Algorithm:**

    1. Skip if backend is not connected
    2. Calculate seconds elapsed since ``last_event_at`` (last received message)
    3. If elapsed time exceeds ``server_timeout``:
       - Log error with exact timeout duration
       - Discard pending read/write buffers (no point processing stale data)
       - Close the connection via ``handle_close()``

    **Relationship with _send_ping():**

    This function works in tandem with :func:`_send_ping`:

    - ``_send_ping()`` proactively tests the connection (every 54s by default)
    - ``_check_timeout()`` detects when the connection is dead (after 120s by default)

    If the server is healthy:

    - Bot sends PING at T=54s
    - Server responds with PONG, updating ``last_event_at``
    - Timeout never triggers because events keep resetting the timer

    If the server becomes unresponsive:

    - Bot sends PING at T=54s (no response)
    - Bot sends PING at T=108s (no response)
    - At T=120s: ``_check_timeout()`` detects timeout and closes connection
    - Bot's reconnection logic will attempt to reconnect

    **Buffer Handling:**

    When a timeout is detected, ``discard_buffers()`` is called to clear both
    the incoming read buffer and outgoing write buffer. This is necessary because:

    - Outgoing data won't be sent (connection is dead)
    - Incoming data may be partial or corrupted (connection hung mid-message)
    - Starting fresh on reconnect is safer than processing stale buffered data

    .. note::
        This function is called every 10 seconds by the timeout scheduler
        registered in :meth:`AsynchatBackend.__init__`.
    """
    if not backend.is_connected():
        return
    # Calculate time since last received message from server
    dt = datetime.datetime.utcnow() - backend.last_event_at
    time_passed = dt.total_seconds()
    if time_passed > backend.server_timeout:
        LOGGER.error(
            'Server timeout detected after %ss; closing.', time_passed)
        # Discard buffers: no need to read/write anything more, just quit
        # Stale buffered data should not be processed after timeout
        LOGGER.debug('Discard current buffers.')
        backend.discard_buffers()
        # Close now - this will trigger bot's reconnection logic
        backend.handle_close()


class AsynchatBackend(AbstractIRCBackend, asynchat.async_chat):
    """IRC backend implementation using :mod:`asynchat` (:mod:`asyncore`).

    :param bot: a Sopel instance
    :type bot: :class:`sopel.bot.Sopel`
    :param int server_timeout: connection timeout in seconds
    :param int ping_interval: ping interval in seconds

    The ``server_timeout`` option defaults to ``120`` seconds if not provided.

    The ``ping_interval`` defaults to ``server_timeout * 0.45`` if not specified.
    """
    def __init__(self, bot, server_timeout=None, ping_interval=None, **kwargs):
        AbstractIRCBackend.__init__(self, bot)
        asynchat.async_chat.__init__(self)
        self.writing_lock = threading.RLock()
        self.set_terminator(b'\r\n')
        self.buffer = ''
        self.server_timeout = server_timeout or 120
        self.ping_interval = ping_interval or (self.server_timeout * 0.45)
        self.last_event_at = None
        self.last_ping_at = None
        self.host = None
        self.port = None
        self.source_address = None
        self.timeout_scheduler = jobs.Scheduler(self)

        # register timeout jobs
        self.register_timeout_jobs([
            (5, _send_ping),
            (10, _check_timeout),
        ])

    def is_connected(self):
        return self.connected

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
            self.send(data)

    def run_forever(self):
        """Run forever."""
        LOGGER.debug('Running forever.')
        asyncore.loop()

    def register_timeout_jobs(self, handlers):
        """Register the timeout handlers for the timeout scheduler."""
        for timer, handler in handlers:
            job = jobs.Job(
                intervals=[timer],
                handler=handler,
                threaded=False,
                doc=inspect.getdoc(handler),
            )
            self.timeout_scheduler.register(job)
            LOGGER.debug('Timeout Job registered: %s', str(job))

    def initiate_connect(self, host, port, source_address):
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
        try:
            LOGGER.debug('Set socket')
            self.set_socket(socket.create_connection((host, port),
                            source_address=source_address))
            LOGGER.debug('Connection attempt')
            self.connect((host, port))
        except socket.error as e:
            LOGGER.exception('Connection error: %s', e)
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
        self.close()
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

        **Buffer Management:**

        This method is called by :mod:`asynchat` whenever data is received from
        the socket but before the line terminator (``\\r\\n``) is found. The data
        is accumulated in ``self.buffer`` until a complete line is assembled.

        **Encoding Detection Strategy:**

        IRC messages should be UTF-8, but legacy clients and servers may use
        other encodings. The fallback order is:

        1. UTF-8 (modern standard, supports all Unicode)
        2. CP-1252 (Windows Latin-1, common in Western Europe)
        3. ISO-8859-1 (Latin-1, more permissive fallback)

        If all three fail, the message is logged with ``<<!`` prefix and discarded
        to prevent processing corrupted data.

        **Activity Tracking:**

        ``last_event_at`` is updated for every successfully decoded message. This
        timestamp is used by :func:`_check_timeout` to detect dead connections.
        """
        # Re-add the terminator that asynchat strips during line detection
        # This is needed for proper line reconstruction
        data += self.get_terminator()

        # We can't trust clients to pass valid Unicode.
        # Try to decode using progressively more permissive encodings
        try:
            # First attempt: UTF-8 (strict, modern standard)
            data = str(data, encoding='utf-8')
        except UnicodeDecodeError:
            # not Unicode; let's try CP-1252
            try:
                # Second attempt: CP-1252 (Windows Latin-1)
                data = str(data, encoding='cp1252')
            except UnicodeDecodeError:
                # Okay, let's try ISO 8859-1
                try:
                    # Third attempt: ISO-8859-1 (permissive fallback)
                    data = str(data, encoding='iso8859-1')
                except UnicodeDecodeError:
                    # All encodings failed - log and discard this message
                    self.bot.log_raw(data, '<<!')
                    LOGGER.warning(
                        "Couldn't guess character encoding of message, ignoring: %r",
                        data,
                    )
                    return

        # Log the decoded incoming data for debugging
        if data:
            self.bot.log_raw(data, '<<')
        # Append to buffer - asynchat will call found_terminator() when \r\n is seen
        self.buffer += data
        # Update activity timestamp for timeout detection
        self.last_event_at = datetime.datetime.utcnow()

    def found_terminator(self):
        """Handle the end of an incoming message."""
        line = self.buffer
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


class SSLAsynchatBackend(AsynchatBackend):
    """SSL-aware extension of :class:`AsynchatBackend`.

    :param bot: a Sopel instance
    :type bot: :class:`sopel.bot.Sopel`
    :param bool verify_ssl: whether to validate the IRC server's certificate
                            (default ``True``, for good reason)
    :param str ca_certs: filesystem path to a CA Certs file containing trusted
                         root certificates
    :param str certfile: filesystem path to a certificate for SSL/TLS client
                         authentication (CertFP)
    :param str keyfile: filesystem path to the private key for ``certfile``
    """
    def __init__(self, bot, verify_ssl=True, ca_certs=None, certfile=None, keyfile=None, **kwargs):
        AsynchatBackend.__init__(self, bot, **kwargs)
        self.verify_ssl = verify_ssl
        self.ssl = None
        self.ca_certs = ca_certs
        self.certfile = certfile
        self.keyfile = keyfile

    def handle_connect(self):
        """Handle SSL/TLS connection setup with certificate validation.

        This method is called after the TCP connection is established but before
        IRC protocol communication begins. It wraps the plain socket with SSL/TLS
        and optionally validates the server's certificate.

        **SSL/TLS Setup:**

        The connection setup varies based on ``verify_ssl`` setting:

        **With verification disabled (verify_ssl=False):**

        - Wraps socket with SSL but does NOT validate server certificate
        - Accepts any certificate (vulnerable to MITM attacks)
        - Still encrypts traffic (provides confidentiality but not authenticity)
        - Should only be used for testing or private networks

        **With verification enabled (verify_ssl=True, default):**

        - Wraps socket with SSL and enforces certificate validation
        - Requires ``ca_certs`` path to trusted root certificates
        - Validates certificate chain of trust
        - Performs hostname matching against certificate

        **Certificate Handling:**

        1. **Client authentication (CertFP):**
           - If ``certfile`` and ``keyfile`` are provided, uses them for client cert authentication
           - Allows bot to authenticate to IRC servers using TLS client certificates
           - Common for NickServ-free authentication on modern IRC networks

        2. **Server certificate validation:**
           - Retrieves server's certificate via ``getpeercert()``
           - Matches hostname in config against certificate's Common Name or SAN
           - If hostname doesn't match, tries CNAME aliases as fallback
           - Prevents MITM attacks by ensuring we're talking to the intended server

        **Hostname Matching with CNAME Fallback:**

        IRC networks often use CNAMEs (e.g., ``irc.example.com`` → ``server1.example.com``).
        The certificate might be issued for the canonical name rather than the alias:

        1. First try to match configured hostname (e.g., ``irc.example.com``)
        2. If that fails, query DNS for CNAMEs
        3. Try matching each CNAME against the certificate
        4. If any CNAME matches, allow connection with warning
        5. If nothing matches, abort connection (likely MITM attack)

        **Error Recovery:**

        If certificate validation fails completely:

        - Logs error about invalid certificate
        - Removes PID file (if running as daemon)
        - Exits with code 1 (TODO: should use proper quit mechanism)

        .. warning::
            Currently uses deprecated ``ssl.wrap_socket()`` API. Future versions
            should migrate to ``SSLContext`` for better security and protocol negotiation.

        .. note::
            ``suppress_ragged_eofs=True`` handles IRC servers that close connections
            without sending proper SSL shutdown messages, common in older implementations.
        """
        # TODO: Refactor to use SSLContext and an appropriate PROTOCOL_* constant
        # See https://lgtm.com/rules/1507225275976/
        # These warnings are ignored for now, because we can't easily fix them
        # while maintaining compatibility with py2.7 AND 3.3+, but in Sopel 8
        # the supported range should narrow sufficiently to fix these for real.
        # Each Python version still generally selects the most secure protocol
        # version(s) it supports.
        if not self.verify_ssl:
            # Wrap without verification - encrypts but doesn't validate server identity
            self.ssl = ssl.wrap_socket(self.socket,  # lgtm [py/insecure-default-protocol]
                                       certfile=self.certfile,
                                       keyfile=self.keyfile,
                                       do_handshake_on_connect=True,
                                       suppress_ragged_eofs=True)
        else:
            # Wrap with verification - enforces certificate validation
            self.ssl = ssl.wrap_socket(self.socket,  # lgtm [py/insecure-default-protocol]
                                       certfile=self.certfile,
                                       keyfile=self.keyfile,
                                       do_handshake_on_connect=True,
                                       suppress_ragged_eofs=True,
                                       cert_reqs=ssl.CERT_REQUIRED,
                                       ca_certs=self.ca_certs)
            # Validate server certificate hostname matches configuration
            try:
                # Try to match configured hostname against certificate
                ssl.match_hostname(self.ssl.getpeercert(), self.host)
            except ssl.CertificateError:
                # The hostname in config and certificate don't match
                # This could be a MITM attack or legitimate CNAME usage
                LOGGER.error("hostname mismatch between configuration and certificate")
                # Check (via exception) if a CNAME matches as a fallback
                # IRC networks often use CNAMEs that don't match certificate CN
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
                    # No hostname or CNAME matched - likely MITM attack
                    LOGGER.error("Invalid certificate, no hostname matches.")
                    # TODO: refactor access to bot's settings
                    if hasattr(self.bot.settings.core, 'pid_file_path'):
                        # TODO: refactor to quit properly (no "os._exit")
                        # Remove PID file before exit to avoid stale lock
                        os.unlink(self.bot.settings.core.pid_file_path)
                        os._exit(1)
        # Replace plain socket with SSL-wrapped socket for all future I/O
        self.set_socket(self.ssl)
        LOGGER.info('Connection accepted by the server...')
        LOGGER.debug('Starting job scheduler for connection timeout...')
        # Start timeout monitoring now that connection is established
        self.timeout_scheduler.start()
        # Notify bot that connection is ready for IRC protocol handshake
        self.bot.on_connect()

    def send(self, data):
        """SSL-aware override for :meth:`~asyncore.dispatcher.send`.

        :param bytes data: raw data to send over SSL socket
        :return: number of bytes actually sent
        :rtype: int

        **SSL Error Handling:**

        SSL sockets require special error handling compared to plain sockets:

        - ``EWOULDBLOCK`` / ``ESRCH``: SSL handshake needs more data, return 0 to retry later
        - Other SSL errors: re-raise to trigger error handling

        Returning 0 tells asyncore the socket is not ready for writing and to try again later.
        """
        try:
            result = self.socket.send(data)
            return result
        except ssl.SSLError as why:
            # SSL handshake may need to read/write multiple times
            if why[0] in (asyncore.EWOULDBLOCK, errno.ESRCH):
                # Not ready yet, asyncore will retry
                return 0
            # Other SSL errors should propagate up
            raise why

    def recv(self, buffer_size):
        """SSL-aware override for :meth:`~asyncore.dispatcher.recv`.

        :param int buffer_size: maximum number of bytes to receive
        :return: received data, or empty bytes if connection closed
        :rtype: bytes

        **SSL Error Handling:**

        SSL sockets use ``read()`` instead of ``recv()`` and require special
        error handling:

        - Empty data: server closed connection cleanly, close our side too
        - ``ECONNRESET`` / ``ENOTCONN`` / ``ESHUTDOWN``: connection errors, close socket
        - ``ENOENT``: SSL needs to write before reading (handshake state), return empty to retry
        - Other errors: re-raise for error handler

        **Non-blocking Behavior:**

        SSL handshakes may require multiple read/write cycles. Returning empty
        bytes when ``ENOENT`` occurs allows asyncore to continue the event loop
        and retry the read later when the socket is ready.

        From a (now deleted) blog post by Evan "K7FOS" Fosmark:
        https://k7fos.com/2010/09/ssl-support-in-asynchatasync_chat
        """
        try:
            # SSL sockets use read() not recv()
            data = self.socket.read(buffer_size)
            if not data:
                # Server closed connection
                self.handle_close()
                return b''
            return data
        except ssl.SSLError as why:
            # Handle various SSL/connection error conditions
            if why[0] in (asyncore.ECONNRESET, asyncore.ENOTCONN,
                          asyncore.ESHUTDOWN):
                # Connection is broken, close our side
                self.handle_close()
                return ''
            elif why[0] == errno.ENOENT:
                # SSL handshake needs to write before reading
                # Required in order to keep it non-blocking
                return b''
            else:
                # Other SSL errors should propagate
                raise
