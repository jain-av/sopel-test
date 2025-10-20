# Copyright 2019, Florian Strzelecki <florian.strzelecki@gmail.com>
#
# Licensed under the Eiffel Forum License 2.
"""Abstract IRC backend interface.

This module defines the abstract interface that all IRC connection backends
must implement. Backends handle the low-level details of connecting to an IRC
server, sending and receiving data, and managing connection state.

**Backend Lifecycle:**

The bot expects backends to follow this lifecycle:

1. **Initialization**: Backend is instantiated with a bot instance
2. **Connection**: Bot calls backend methods to establish connection
3. **Active Communication**: Backend sends/receives IRC messages via
   ``irc_send()`` and data callback mechanisms
4. **Error Handling**: Backend calls ``on_irc_error()`` when server sends
   error events
5. **Disconnection**: Backend closes connection cleanly on quit/error

**Contract Between Backend and Bot:**

- Backend **MUST** implement ``is_connected()``, ``on_irc_error()``, and
  ``irc_send()``
- Backend **SHOULD** call ``bot.on_message_sent()`` after sending data
  (automatically done via ``send_command()``)
- Backend **MUST** respect ``bot.hasquit`` flag in ``on_irc_error()`` and
  close connection when set
- Backend **MAY** override helper methods (``send_nick()``, ``send_user()``,
  etc.) for specialized behavior

**Implementing a Custom Backend:**

To create a custom backend:

1. Inherit from ``AbstractIRCBackend``
2. Implement the three abstract methods: ``is_connected()``, ``on_irc_error()``,
   and ``irc_send()``
3. Set up your connection mechanism (sockets, asyncio, etc.)
4. Call ``bot.on_message_sent()`` after successfully sending data
5. Handle incoming data and pass it to the bot's message handler

Example custom backend::

    from sopel.irc.abstract_backends import AbstractIRCBackend
    import socket

    class CustomSocketBackend(AbstractIRCBackend):
        def __init__(self, bot):
            super().__init__(bot)
            self.socket = None

        def is_connected(self):
            return self.socket is not None

        def on_irc_error(self, pretrigger):
            # Check if bot wants to quit
            if self.bot.hasquit:
                # Close connection to allow bot to quit/reconnect
                if self.socket:
                    self.socket.close()
                    self.socket = None

        def irc_send(self, data):
            # Send raw bytes to server
            if self.is_connected():
                self.socket.sendall(data)
            else:
                raise ConnectionError("Not connected to server")

See :class:`sopel.irc.backends.AsyncioBackend` for a complete implementation
example.
"""
from __future__ import annotations

import abc

from .utils import safe


class AbstractIRCBackend(abc.ABC):
    """Abstract class defining the interface and basic logic of an IRC backend.

    :param bot: a Sopel instance
    :type bot: :class:`sopel.bot.Sopel`

    This class provides the foundation for all IRC connection backends. It
    defines three abstract methods that **MUST** be implemented by subclasses,
    plus concrete helper methods for common IRC commands.

    **Required Methods:**

    Subclasses must implement these three abstract methods:

    - :meth:`is_connected`: Returns True if backend is connected to server
    - :meth:`on_irc_error`: Handles IRC error events from server
    - :meth:`irc_send`: Sends raw bytes to the IRC server

    **Provided Methods:**

    The class provides concrete implementations of:

    - :meth:`send_command`: High-level command sending with ``on_message_sent``
      callback
    - :meth:`prepare_command`: IRC message formatting with RFC 2812 length
      limits
    - Helper methods: ``send_ping()``, ``send_pong()``, ``send_nick()``,
      ``send_user()``, ``send_join()``, ``send_quit()``, etc.

    **Error Handling Requirements:**

    Backend implementations must:

    - Raise appropriate exceptions when operations fail (e.g.,
      ``ConnectionError`` if sending while disconnected)
    - Check ``bot.hasquit`` in ``on_irc_error()`` and close connection if set
    - Handle network errors gracefully and allow bot reconnection logic to
      manage retries
    - Clean up resources (sockets, threads, etc.) when connection closes

    **Thread Safety:**

    Backends should be thread-safe if the bot uses multiple threads for plugin
    execution. Use appropriate locking mechanisms to protect connection state.
    """
    def __init__(self, bot):
        self.bot = bot

    @abc.abstractmethod
    def is_connected(self):
        """Tell if the backend is connected or not.

        :rtype: bool

        **Implementation Notes:**

        This method should return ``True`` when the backend has an active
        connection to the IRC server and can send/receive data, ``False``
        otherwise.

        **Expected Behavior:**

        - Return ``True`` if connection is established and operational
        - Return ``False`` if disconnected, connecting, or in error state
        - Should be fast (no I/O operations) as it's called frequently
        - Thread-safe: multiple threads may call this simultaneously

        Example implementation::

            def is_connected(self):
                return self.socket is not None and self.socket.fileno() != -1
        """

    @abc.abstractmethod
    def on_irc_error(self, pretrigger):
        """Action to perform when the server sends an error event.

        :param pretrigger: PreTrigger object with the error event
        :type pretrigger: :class:`sopel.trigger.PreTrigger`

        This method is called by the bot when the IRC server sends an error
        event (typically an ERROR command indicating connection termination).

        **Implementation Notes:**

        The backend **MUST** check the ``bot.hasquit`` flag and close the
        connection if it is set. This allows the bot's main loop to detect
        the disconnection and either quit cleanly or initiate a reconnection
        attempt.

        **Expected Behavior:**

        - Check ``self.bot.hasquit`` flag
        - If ``True``: close the connection immediately to trigger bot shutdown/
          reconnect logic
        - If ``False``: optionally log the error but maintain connection
        - Should not raise exceptions (handle errors internally)
        - May trigger cleanup of resources (buffers, threads, etc.)

        **Lifecycle Integration:**

        The bot sets ``hasquit`` when:

        - User issues ``.quit`` command
        - Server sends KILL or forces disconnection
        - Fatal error occurs requiring reconnection

        Example implementation::

            def on_irc_error(self, pretrigger):
                # Log the error for debugging
                LOGGER.error("IRC Error: %s", pretrigger.args[-1])

                # If bot wants to quit, close connection
                if self.bot.hasquit:
                    self._shutdown()
        """

    @abc.abstractmethod
    def irc_send(self, data):
        """Send an IRC line as raw ``data``.

        :param bytes data: raw line to send (includes ``\\r\\n`` terminator)

        This is the low-level method for sending raw bytes to the IRC server.
        It is called by :meth:`send_command` after preparing and encoding the
        IRC message.

        **Implementation Notes:**

        The backend **MUST** send the complete byte string to the server
        without modification. The data already includes:

        - Proper IRC command formatting
        - UTF-8 encoding
        - CR-LF (``\\r\\n``) line terminator
        - Length validation per RFC 2812 (≤512 bytes including ``\\r\\n``)

        **Expected Behavior:**

        - Send all bytes in ``data`` to the IRC server
        - Raise exception if sending fails (e.g., ``ConnectionError`` if
          disconnected)
        - Handle partial sends if necessary (loop until all data sent)
        - Thread-safe: use locks if backend allows concurrent sends
        - Non-blocking sends are acceptable if backend buffers data

        **Error Handling:**

        Implementations should raise exceptions on failure:

        - ``ConnectionError``: not connected or connection lost during send
        - ``OSError``: socket error (EPIPE, ECONNRESET, etc.)
        - ``ssl.SSLError``: SSL/TLS error during encrypted send

        Example implementation::

            def irc_send(self, data):
                if not self.is_connected():
                    raise ConnectionError("Cannot send: not connected")

                try:
                    # Send all bytes (handle partial sends)
                    self.socket.sendall(data)
                except OSError as err:
                    # Log and re-raise socket errors
                    LOGGER.error("Send failed: %s", err)
                    raise
        """

    def send_command(self, *args, **kwargs):
        """Send a command through the IRC connection.

        :param args: IRC command to send with its argument(s)
        :param str text: the text to send (optional keyword argument)

        Example::

            # send the INFO command
            backend.send_command('INFO')
            # send the NICK command with the argument 'Sopel'
            backend.send_command('NICK', 'Sopel')
            # send the PRIVMSG command to channel #sopel with some text
            backend.send_command('PRIVMSG', '#sopel', text='Hello world!')

        .. note::

            This will call the :meth:`sopel.bot.Sopel.on_message_sent`
            callback on the bot instance with the raw message sent.
        """
        raw_command = self.prepare_command(*args, text=kwargs.get('text'))
        self.irc_send(raw_command.encode('utf-8'))
        self.bot.on_message_sent(raw_command)

    def prepare_command(self, *args, **kwargs):
        """Prepare an IRC command from ``args`` and optional ``text``.

        :param list args: list of text, arguments of the IRC command to send
        :param str text: optional text to send with the IRC command
        :return: the raw message to send through the connection (with ``\\r\\n``)
        :rtype: str

        This method formats IRC commands according to :rfc:`2812` Section 2.3
        and enforces the protocol's message length limits.

        **RFC 2812 Message Format:**

        IRC messages follow this format::

            <command> [<param1> <param2> ...] [:<trailing text>]\\r\\n

        Examples:

        - ``NICK Sopel\\r\\n``
        - ``JOIN #sopel\\r\\n``
        - ``PRIVMSG #sopel :Hello world!\\r\\n``

        **RFC 2812 Message Length Limits:**

        From :rfc:`2812` Section 2.3:

            IRC messages are always lines of characters terminated with a
            CR-LF (Carriage Return - Line Feed) pair, and these messages SHALL
            NOT exceed 512 characters in length, counting all characters
            including the trailing CR-LF. Thus, there are 510 characters
            maximum allowed for the command and its parameters. There is no
            provision for continuation of message lines.

        **Critical**: The 512-byte limit is in *bytes* after UTF-8 encoding,
        not Unicode character count. A single Unicode character like 'é' or
        '日' can be 2-4 bytes in UTF-8, so:

        - ``"Hello"`` = 5 characters = 5 bytes (fits)
        - ``"Héllo"`` = 5 characters = 6 bytes (é is 2 bytes)
        - ``"日本語"`` = 3 characters = 9 bytes (each character is 3 bytes)

        **Message Truncation Algorithm:**

        If a message exceeds 510 bytes:

        1. Count bytes in UTF-8 encoding
        2. If over limit, truncate by one Unicode character
        3. Re-check byte count and repeat until under limit
        4. This ensures we never split a multi-byte character

        **Text Parameter:**

        The ``text`` keyword argument is formatted with a leading colon (``:``)
        per IRC protocol, which allows the text to contain spaces.

        The returned message contains the CR-LF pair and is ready to encode
        to bytes and send via :meth:`irc_send`.
        """
        text = kwargs.get('text')
        # RFC 2812: max 512 bytes including \r\n, so 510 for command+params
        max_length = unicode_max_length = 510

        # Build command: space-separated args (e.g., "PRIVMSG #sopel")
        raw_command = ' '.join(args)

        # If text provided, append with colon separator (e.g., " :Hello world!")
        if text is not None:
            raw_command = '{args} :{text}'.format(args=raw_command,
                                                  text=safe(text))

        # Truncate to fit RFC 2812 byte limit (510 bytes before \r\n)
        # Must work with Unicode characters: can't slice bytes or we may
        # split multi-byte characters (é, 日, etc.) causing encoding errors
        while len(raw_command.encode('utf-8')) > max_length:
            # Remove one Unicode character from the end
            raw_command = raw_command[:unicode_max_length]
            # Decrease target length for next iteration (in case char was 1 byte)
            unicode_max_length = unicode_max_length - 1

        # Append CR-LF terminator required by IRC protocol
        return raw_command + '\r\n'

    def send_ping(self, host):
        """Send a ``PING`` command to the server.

        :param str host: IRC server host

        A ``PING`` command should be sent at a regular interval to make sure
        the server knows the IRC connection is still active.
        """
        self.send_command('PING', safe(host))

    def send_pong(self, host):
        """Send a ``PONG`` command to the server.

        :param str host: IRC server host

        A ``PONG`` command must be sent each time the server sends a ``PING``
        command to the client.
        """
        self.send_command('PONG', safe(host))

    def send_nick(self, nick):
        """Send a ``NICK`` command with a ``nick``.

        :param str nick: nickname to take
        """
        self.send_command('NICK', safe(nick))

    def send_user(self, user, mode, nick, name):
        """Send a ``USER`` command with a ``user``.

        :param str user: IRC username
        :param str mode: mode(s) to send for the user
        :param str nick: nickname associated with this user
        :param str name: "real name" for the user
        """
        self.send_command('USER', safe(user), mode, safe(nick), text=name)

    def send_pass(self, password):
        """Send a ``PASS`` command with a ``password``.

        :param str password: password for authentication
        """
        self.send_command('PASS', safe(password))

    def send_join(self, channel, password=None):
        """Send a ``JOIN`` command to ``channel`` with optional ``password``.

        :param str channel: channel to join
        :param str password: optional password for protected channels
        """
        if password is None:
            self.send_command('JOIN', safe(channel))
        else:
            self.send_command('JOIN', safe(channel), safe(password))

    def send_part(self, channel, reason=None):
        """Send a ``PART`` command to ``channel``.

        :param str channel: the channel to part
        :param str text: optional text for leaving the channel
        """
        self.send_command('PART', safe(channel), text=reason)

    def send_quit(self, reason=None):
        """Send a ``QUIT`` command.

        :param str reason: optional text for leaving the server

        This won't send anything if the backend isn't connected.
        """
        if self.is_connected():
            self.send_command('QUIT', text=reason)

    def send_kick(self, channel, nick, reason=None):
        """Send a ``KICK`` command for ``nick`` in ``channel`` .

        :param str channel: the channel from which to kick ``nick``
        :param str nick: nickname to kick from the ``channel``
        :param str reason: optional reason for the kick
        """
        self.send_command('KICK', safe(channel), safe(nick), text=reason)

    def send_privmsg(self, dest, text):
        """Send a ``PRIVMSG`` command to ``dest`` with ``text``.

        :param str dest: nickname or channel name
        :param str text: the text to send
        """
        self.send_command('PRIVMSG', safe(dest), text=text)

    def send_notice(self, dest, text):
        """Send a ``NOTICE`` command to ``dest`` with ``text``.

        :param str dest: nickname or channel name
        :param str text: the text to send
        """
        self.send_command('NOTICE', dest, text=text)
