"""IRC Tools for ISUPPORT management.

This module provides parsing and management utilities for the IRC ``ISUPPORT``
(also known as ``RPL_ISUPPORT`` or numeric ``005``) feature, which allows IRC
servers to advertise their supported features, limits, and configuration to
connecting clients.

ISUPPORT Overview
-----------------

When a server wants to advertise its features and settings, it sends
``RPL_ISUPPORT`` messages during the connection registration phase. Each
message contains key-value pairs describing server capabilities::

    :server.example.com 005 nickname CHANTYPES=# PREFIX=(ov)@+ NETWORK=ExampleNet

The module handles:

* **Parameter parsing**: Converts raw ISUPPORT strings into typed Python values
* **Type-specific parsers**: Handles integers, strings, tuples, and complex
  structured data (like PREFIX modes and CHANMODES)
* **Parameter updates**: Merges new ISUPPORT data with existing values
* **Parameter removal**: Handles server-initiated parameter removal (``-PARAM``)

Parsing Strategy
----------------

Each ISUPPORT parameter has a specific format and meaning. This module provides
specialized parsers for common parameters:

* **Simple types**: ``NICKLEN=30`` → ``int(30)``
* **Optional values**: ``CHANTYPES=#&`` → ``tuple('#', '&')``
* **Structured data**: ``PREFIX=(ov)@+`` → ``(('o', '@'), ('v', '+'))``
* **Multi-part data**: ``CHANMODES=b,k,l,imnpst`` → parsed into 4 mode types

Relationship to IRC RFCs
------------------------

The ISUPPORT feature is not part of the core IRC RFCs (:rfc:`1459`, :rfc:`2812`)
but is widely implemented across IRC servers. The authoritative documentation
is maintained at `modern.ircdocs.horse`__.

Common parameters include:

* ``CASEMAPPING``: How to compare nicknames/channels (ascii, rfc1459, etc.)
* ``CHANTYPES``: Valid channel prefixes (typically ``#``, ``&``, ``+``, ``!``)
* ``PREFIX``: User privilege modes and their channel prefixes
* ``CHANMODES``: Four categories of channel modes (A, B, C, D types)
* ``NETWORK``: Human-readable network name
* Various length limits (``NICKLEN``, ``CHANNELLEN``, ``TOPICLEN``, etc.)

.. __: https://modern.ircdocs.horse/#rplisupport-005

Examples
--------

Parsing a complete ISUPPORT parameter set::

    >>> args = ['CHANTYPES=#&', 'PREFIX=(ov)@+', 'NICKLEN=30']
    >>> params = {k: v for k, v in (parse_parameter(arg) for arg in args)}
    >>> isupport = ISupport(**params)
    >>> isupport.NICKLEN
    30
    >>> isupport.PREFIX
    OrderedDict([('o', '@'), ('v', '+')])

Handling parameter updates and removals::

    >>> updated = isupport.apply(NICKLEN=25, **{'-CHANTYPES': None})
    >>> updated.NICKLEN
    25
    >>> 'CHANTYPES' in updated
    False

.. seealso::

    https://modern.ircdocs.horse/#rplisupport-005

"""
# Copyright 2019, Florian Strzelecki <florian.strzelecki@gmail.com>
#
# Licensed under the Eiffel Forum License 2.
from __future__ import annotations

from collections import OrderedDict
import functools
import itertools
import re
from typing import Dict


def _optional(parser, default=None):
    """Wrap a parser function to handle optional/missing ISUPPORT values.

    :param callable parser: the parser function to wrap
    :param default: value to return when input is empty or None (default: ``None``)
    :return: wrapped parser that returns ``default`` for empty values
    :rtype: callable

    This decorator is used to make ISUPPORT parameter parsers handle cases where
    the parameter is present but has no value, or where the parameter's value
    should fall back to a default.

    Example::

        >>> optional_int = _optional(int, default=0)
        >>> optional_int('')  # empty string
        0
        >>> optional_int('42')
        42
        >>> optional_int(None)
        0
    """
    @functools.wraps(parser)
    def wrapped(value):
        if not value:
            return default
        return parser(value)
    return wrapped


def _no_value(value):
    """Parser for ISUPPORT parameters that are flags without values.

    :param str value: the value (ignored)
    :return: always returns ``None``
    :rtype: None

    Some ISUPPORT parameters are boolean flags that indicate feature presence
    without any associated value. For example, ``SAFELIST`` indicates that the
    server supports safe list operations, but has no additional data.
    """
    return None


def _single_character(value):
    """Parse and validate a single-character ISUPPORT parameter value.

    :param str value: the value to validate
    :return: the single character value
    :rtype: str
    :raises ValueError: if value contains more than one character

    Some ISUPPORT parameters expect exactly one character. For example,
    ``EXCEPTS`` and ``INVEX`` parameters specify the mode character used
    for their respective ban exception and invite exception lists.

    Example::

        >>> _single_character('e')
        'e'
        >>> _single_character('I')
        'I'
        >>> _single_character('ab')
        Traceback (most recent call last):
            ...
        ValueError: Too many characters: 'ab'.
    """
    if len(value) > 1:
        raise ValueError('Too many characters: %r.' % value)

    return value


def _map_items(parser=str, map_separator=',', item_separator=':'):
    """Create a parser for ISUPPORT parameters with key-value mappings.

    :param callable parser: function to parse individual values (default: ``str``)
    :param str map_separator: separator between key-value pairs (default: ``,``)
    :param str item_separator: separator between key and value (default: ``:``)
    :return: parser function that converts strings to sorted tuples of tuples
    :rtype: callable

    Many ISUPPORT parameters contain multiple key-value pairs. This function
    creates a specialized parser for such parameters.

    Example formats:

    * ``CHANLIMIT=#:50,&:10`` → ``(('#', 50), ('&', 10))``
    * ``MAXLIST=beI:100,q:50`` → ``(('beI', 100), ('q', 50))``
    * ``TARGMAX=JOIN:,PRIVMSG:4`` → ``(('JOIN', None), ('PRIVMSG', 4))``

    The parser returns a sorted tuple of 2-tuples. Values are parsed using
    the provided parser function. Missing values (empty strings after the
    separator) are converted to ``None``.

    Example::

        >>> parse_int_map = _map_items(int)
        >>> parse_int_map('a:10,b:20')
        (('a', 10), ('b', 20))
        >>> parse_int_map('x:5,y:')  # y has no value
        (('x', 5), ('y', None))
    """
    @functools.wraps(parser)
    def wrapped(value):
        items = sorted(
            item.split(item_separator)
            for item in value.split(map_separator))

        return tuple(
            (k, parser(v) if v else None)
            for k, v in items
        )
    return wrapped


def _parse_chanmodes(value):
    """Parse the CHANMODES ISUPPORT parameter.

    :param str value: comma-separated list of channel mode groups
    :return: tuple of mode groups (A, B, C, D, extras)
    :rtype: tuple
    :raises ValueError: if fewer than 4 mode groups are provided

    The CHANMODES parameter categorizes channel modes into four types:

    * **Type A**: Modes that add/remove items from a list (e.g., ban masks)
    * **Type B**: Modes that require a parameter (e.g., channel key)
    * **Type C**: Modes with parameter when set, no parameter when unset (e.g., user limit)
    * **Type D**: Modes that never require parameters (e.g., moderated channel)

    Returns a tuple where the first four elements are the mode strings for
    types A-D, and the fifth element (if present) is a tuple of any additional
    mode groups (server extensions).

    Example::

        >>> _parse_chanmodes('b,k,l,imnpst')
        ('b', 'k', 'l', 'imnpst', ())
        >>> _parse_chanmodes('beI,k,l,imnpst,extra')
        ('beI', 'k', 'l', 'imnpst', ('extra',))

    .. seealso::

        https://modern.ircdocs.horse/#chanmodes-parameter
    """
    items = value.split(',')

    if len(items) < 4:
        raise ValueError('Not enough channel types to unpack from %r.' % value)

    # First 4 items are the standard A, B, C, D mode types
    # Any additional items are server-specific extensions, grouped in a 5th tuple
    # Result structure: (A, B, C, D, (E, F, G, ...))
    return tuple(items[:4]) + (tuple(items[4:]),)


def _parse_elist(value):
    """Parse the ELIST ISUPPORT parameter.

    :param str value: string of ELIST search extension letters
    :return: sorted tuple of unique uppercase letters
    :rtype: tuple

    The ELIST parameter specifies which search extensions are supported by the
    server's LIST command. Each letter represents a different search capability:

    * **C**: Search by channel creation time
    * **M**: Search by mask (pattern matching)
    * **N**: Search by NOT mask (inverse pattern)
    * **T**: Search by topic modification time
    * **U**: Search by user count

    Letters are case-insensitive and returned in sorted order with duplicates
    removed.

    Example::

        >>> _parse_elist('CMNTU')
        ('C', 'M', 'N', 'T', 'U')
        >>> _parse_elist('cmn')
        ('C', 'M', 'N')
        >>> _parse_elist('CcMm')  # duplicates removed
        ('C', 'M')

    .. seealso::

        https://modern.ircdocs.horse/#elist-parameter
    """
    # Normalize to uppercase, remove duplicates, and sort alphabetically
    return tuple(sorted(set(letter.upper() for letter in value)))


def _parse_extban(value):
    """Parse the EXTBAN ISUPPORT parameter.

    :param str value: EXTBAN parameter in format ``prefix,types``
    :return: tuple of (prefix, types_tuple)
    :rtype: tuple
    :raises ValueError: if value format is invalid

    The EXTBAN parameter describes extended ban capabilities, which allow
    ban masks to match users based on criteria other than their hostmask
    (e.g., account name, real name, etc.).

    Format: ``prefix,types`` where:

    * **prefix**: Optional character that prefixes extended bans (commonly ``$``)
      Set to ``None`` if no prefix is used
    * **types**: Letters indicating supported extended ban types

    Common extended ban types include:

    * **a**: Match by account name
    * **r**: Match by real name (GECOS)
    * **j**: Match users in another channel
    * **c**: Match users in channel (with color/formatting)

    Example::

        >>> _parse_extban('$,ajrxz')
        ('$', ('a', 'j', 'r', 'x', 'z'))
        >>> _parse_extban(',a')  # no prefix
        (None, ('a',))

    .. seealso::

        https://modern.ircdocs.horse/#extban-parameter
    """
    args = value.split(',')

    if len(args) < 2:
        raise ValueError('Invalid value for EXTBAN: %r.' % value)

    # First part is the prefix character (empty string becomes None)
    prefix = args[0] or None
    # Second part contains the supported type letters, sorted and deduplicated
    items = tuple(sorted(set(args[1])))

    return (prefix, items)


def _parse_prefix(value):
    """Parse the PREFIX ISUPPORT parameter.

    :param str value: PREFIX parameter in format ``(modes)prefixes``
    :return: tuple of (mode, prefix) pairs
    :rtype: tuple
    :raises ValueError: if format is invalid or lengths don't match

    The PREFIX parameter defines the relationship between channel user modes
    and the prefix characters displayed before nicknames in channel listings.

    Format: ``(modes)prefixes`` where modes and prefixes have equal length
    and each mode character corresponds to the prefix at the same position.

    Common mappings:

    * **o** (operator) → **@**
    * **v** (voice) → **+**
    * **h** (half-operator) → **%**
    * **a** (admin/protected) → **&**
    * **q** (owner/founder) → **~**

    The regex pattern matches:

    * Opening parenthesis, followed by one or more non-whitespace chars (modes)
    * Closing parenthesis, followed by one or more non-whitespace chars (prefixes)

    Example::

        >>> _parse_prefix('(ov)@+')
        (('o', '@'), ('v', '+'))
        >>> _parse_prefix('(qaohv)~&@%+')
        (('q', '~'), ('a', '&'), ('o', '@'), ('h', '%'), ('v', '+'))

    .. seealso::

        https://modern.ircdocs.horse/#prefix-parameter
    """
    result = re.match(r'\((?P<modes>\S+)\)(?P<prefixes>\S+)', value)

    if not result:
        raise ValueError('Invalid value for PREFIX: %r' % value)

    modes = result.group('modes')
    prefixes = result.group('prefixes')

    if len(modes) != len(prefixes):
        raise ValueError('Mode list does not match for PREFIX: %r' % value)

    return tuple(zip(modes, prefixes))


ISUPPORT_PARSERS = {
    # Length limits for various IRC protocol elements
    'AWAYLEN': int,           # Maximum away message length
    'CHANNELLEN': int,        # Maximum channel name length
    'HOSTLEN': int,           # Maximum hostname length
    'KICKLEN': int,           # Maximum kick message length
    'NICKLEN': int,           # Maximum nickname length
    'TOPICLEN': int,          # Maximum topic length
    'USERLEN': int,           # Maximum username length

    # Server identification and capabilities
    'CASEMAPPING': str,       # How to compare nicks/channels (ascii, rfc1459, etc.)
    'NETWORK': str,           # Human-readable network name

    # Channel-related parameters
    'CHANLIMIT': _map_items(int),       # Max channels per prefix (#:50, &:10)
    'CHANMODES': _parse_chanmodes,      # Four categories of channel modes (A,B,C,D)
    'CHANTYPES': _optional(tuple),      # Valid channel prefixes (#, &, +, !)
    'STATUSMSG': _optional(tuple),      # Prefixes for status messages to channel ops

    # User privilege parameters
    'PREFIX': _optional(_parse_prefix), # Mode-to-prefix mappings (ov)@+

    # Ban and exception lists
    'EXCEPTS': _optional(_single_character, default='e'),  # Ban exception mode char
    'EXTBAN': _parse_extban,            # Extended ban syntax and types
    'INVEX': _optional(_single_character, default='I'),    # Invite exception mode char
    'MAXLIST': _map_items(int),         # Max list entries per mode (beI:100)

    # Command capabilities
    'ELIST': _parse_elist,              # LIST command search extensions
    'MAXTARGETS': _optional(int),       # Max targets for commands (PRIVMSG, KICK, etc.)
    'MODES': _optional(int),            # Max mode changes per MODE command
    'TARGMAX': _optional(_map_items(int), default=tuple()),  # Max targets per command

    # Special features
    'SAFELIST': _no_value,              # Safe LIST implementation (flag only)
    'SILENCE': _optional(int),          # Max silence list entries
}


def parse_parameter(arg):
    """Parse a single ISUPPORT parameter into a (key, value) tuple.

    :param str arg: raw ISUPPORT parameter string
    :return: tuple of (parameter_name, parsed_value)
    :rtype: tuple

    Handles three parameter formats:

    1. **Key-value pair**: ``NICKLEN=30`` → ``('NICKLEN', 30)``
    2. **Value-less parameter**: ``SAFELIST`` → ``('SAFELIST', None)``
    3. **Removal marker**: ``-AWAYLEN`` → ``('-AWAYLEN', None)``

    Parameters prefixed with ``-`` indicate removal of a previously advertised
    capability. The value is always ``None`` for removal markers.

    For known parameters (defined in ``ISUPPORT_PARSERS``), the appropriate
    type-specific parser is applied. Unknown parameters default to optional
    string parsing.

    Example::

        >>> parse_parameter('NICKLEN=30')
        ('NICKLEN', 30)
        >>> parse_parameter('CHANTYPES=#&')
        ('CHANTYPES', ('#', '&'))
        >>> parse_parameter('-AWAYLEN')
        ('-AWAYLEN', None)
        >>> parse_parameter('UNKNOWN=value')
        ('UNKNOWN', 'value')
    """
    # Split on first '=' to separate key from value
    items = arg.split('=', 1)
    if len(items) == 2:
        key, value = items
    else:
        # No '=' found, so parameter has no value (e.g., SAFELIST)
        key, value = items[0], None

    if key.startswith('-'):
        # Removal marker: ignore any value and return None
        # Example: -AWAYLEN indicates AWAYLEN is no longer supported
        return (key, None)

    # Get the appropriate parser for this parameter, or default to optional string
    parser = ISUPPORT_PARSERS.get(key, _optional(str))
    return (key, parser(value))


class ISupport:
    """Storage class for IRC's ``ISUPPORT`` feature.

    An instance of ``ISupport`` can be used as a read-only dict, to store
    features advertised by the IRC server::

        >>> isupport = ISupport(chanlimit=(('&', None), ('#', 70)))
        >>> isupport['CHANLIMIT']
        (('&', None) ('#', 70))
        >>> isupport.CHANLIMIT  # some parameters are also properties
        {
            '&': None,
            '#': 70,
        }
        >>> 'chanlimit' in isupport  # case-insensitive
        True
        >>> 'chanmode' in isupport
        False
        >>> isupport.CHANMODE  # not advertised by the server!
        Traceback (most recent call last):
          File "<stdin>", line 1, in <module>
        AttributeError: 'ISupport' object has no attribute 'CHANMODE'

    The list of possible parameters can be found at
    `modern.ircdocs.horse's RPL_ISUPPORT Parameters`__.

    .. __: https://modern.ircdocs.horse/#rplisupport-parameters
    """
    def __init__(self, **kwargs):
        """Initialize ISupport storage with parsed parameters.

        :param kwargs: ISUPPORT parameters as keyword arguments

        Parameters are normalized to uppercase and stored internally. Parameters
        with keys starting with ``-`` (removal markers) are filtered out during
        initialization.

        Example::

            >>> isupport = ISupport(NICKLEN=30, NETWORK='Example')
            >>> isupport.NICKLEN
            30
        """
        # Store parameters in uppercase, filtering out removal markers
        self.__isupport = dict(
            (key.upper(), value)
            for key, value in kwargs.items()
            if not key.startswith('-'))

    def __getitem__(self, key):
        """Retrieve ISUPPORT parameter by key (dict-style access).

        :param str key: parameter name (case-insensitive)
        :return: the parameter's value
        :raises KeyError: if parameter is not advertised

        Example::

            >>> isupport['NICKLEN']
            30
            >>> isupport['nicklen']  # case-insensitive
            30
        """
        key_ci = key.upper()
        if key_ci not in self.__isupport:
            raise KeyError(key_ci)
        return self.__isupport[key_ci]

    def __contains__(self, key):
        """Check if parameter is advertised (case-insensitive).

        :param str key: parameter name to check
        :return: ``True`` if advertised, ``False`` otherwise
        :rtype: bool

        Example::

            >>> 'NICKLEN' in isupport
            True
            >>> 'nicklen' in isupport  # case-insensitive
            True
            >>> 'UNKNOWN' in isupport
            False
        """
        return key.upper() in self.__isupport

    def __getattr__(self, name):
        """Retrieve ISUPPORT parameter by attribute access.

        :param str name: parameter name (must be uppercase)
        :return: the parameter's value
        :raises AttributeError: if parameter is not advertised

        Example::

            >>> isupport.NICKLEN
            30
            >>> isupport.UNKNOWN
            Traceback (most recent call last):
                ...
            AttributeError: UNKNOWN
        """
        if name not in self.__isupport:
            raise AttributeError(name)

        return self.__isupport[name]

    def __setattr__(self, name, value):
        """Prevent modification of ISUPPORT parameters (read-only).

        :param str name: attribute name
        :param value: value to set
        :raises AttributeError: always, as modification is not allowed

        ISupport objects are read-only. Use the :meth:`apply` method to create
        a new instance with updated parameters.
        """
        # Allow setting the internal __isupport dict during initialization
        if name == '_ISupport__isupport':
            super().__setattr__(name, value)
        elif name in self.__isupport:
            # Reject any modification of stored parameters
            raise AttributeError("Can't set value for %r" % name)
        elif name not in self.__dict__:
            raise AttributeError('Unknown attribute')

    def get(self, name, default=None):
        """Retrieve value for the feature ``name``.

        :param str name: feature to retrieve
        :param default: default value if the feature is not advertised
                        (defaults to ``None``)
        :return: the value for that feature, if advertised, or ``default``
        """
        return self[name] if name in self else default

    def apply(self, **kwargs):
        """Build a new instance of :class:`ISupport` with updated parameters.

        :param kwargs: new or updated parameters, including removal markers
        :return: a new instance, updated with the latest advertised features
        :rtype: :class:`ISupport`

        This method creates a new ISupport instance that reflects incremental
        updates from the server. The result:

        * Retains existing parameters not marked for removal
        * Adds new parameters from kwargs
        * Updates existing parameters with new values from kwargs
        * Removes parameters with ``-PARAMNAME`` removal markers

        Example::

            >>> original = ISupport(NICKLEN=30, AWAYLEN=200, NETWORK='Example')
            >>> updated = original.apply(**{'-AWAYLEN': None, 'NICKLEN': 25, 'CHANNELLEN': 50})
            >>> 'CHANNELLEN' in updated
            True
            >>> updated.NICKLEN
            25
            >>> 'AWAYLEN' in updated  # removed
            False
            >>> updated.NETWORK  # preserved
            'Example'

        """
        # Normalize all incoming parameter names to uppercase
        kwargs_upper = dict(
            (key.upper(), value)
            for key, value in kwargs.items()
        )

        # Keep existing parameters that are not marked for removal
        # A parameter is marked for removal if '-PARAMNAME' appears in kwargs
        kept = (
            (key, value)
            for key, value in self.__isupport.items()
            if ('-%s' % key) not in kwargs_upper
        )

        # Merge kept parameters with new/updated parameters
        # Note: removal markers (-PARAM) will be filtered during __init__
        updated = dict(itertools.chain(kept, kwargs_upper.items()))

        return self.__class__(**updated)

    @property
    def CHANLIMIT(self):
        """Expose ``CHANLIMIT`` as a dict, if advertised by the server.

        This exposes information about the maximum number of channels that the
        bot can join for each prefix::

            >>> isupport.CHANLIMIT
            {
                '#': 70,
                '&': None,
            }

        In that example, the bot may join 70 ``#`` channels and any number of
        ``&`` channels.

        This attribute is not available if the server does not provide the
        right information, and accessing it will raise an
        :exc:`AttributeError`.

        .. seealso::

            https://modern.ircdocs.horse/#chanlimit-parameter

        """
        if 'CHANLIMIT' not in self:
            raise AttributeError('CHANLIMIT')

        return dict(self['CHANLIMIT'])

    @property
    def CHANMODES(self):
        """Expose ``CHANMODES`` as a dict.

        This exposes information about 4 types of channel modes::

            >>> isupport.CHANMODES
            {
                'A': 'b',
                'B': 'k',
                'C': 'l',
                'D': 'imnpst',
            }

        The values are empty if the server does not provide this information.

        .. seealso::

            https://modern.ircdocs.horse/#chanmodes-parameter

        """
        if 'CHANMODES' not in self:
            return {"A": "", "B": "", "C": "", "D": ""}

        return dict(zip('ABCD', self['CHANMODES'][:4]))

    @property
    def MAXLIST(self):
        """Expose ``MAXLIST`` as a dict, if advertised by the server.

        This exposes information about maximums for combinations of modes::

            >>> isupport.MAXLIST
            {
                'beI': 100,
                'q': 50,
                'b': 50,
            }

        This attribute is not available if the server does not provide the
        right information, and accessing it will raise an
        :exc:`AttributeError`.

        .. seealso::

            https://modern.ircdocs.horse/#maxlist-parameter

        """
        if 'MAXLIST' not in self:
            raise AttributeError('MAXLIST')

        return dict(self['MAXLIST'])

    @property
    def PREFIX(self) -> Dict[str, str]:
        """Expose ``PREFIX`` as a dict, if advertised by the server.

        This exposes information about the modes and nick prefixes used for
        user privileges in channels::

            >>> isupport.PREFIX
            {
                'q': '~',
                'a': '&',
                'o': '@',
                'h': '%',
                'v': '+',
            }

        Entries are in order of descending privilege.

        This attribute is not available if the server does not provide the
        right information, and accessing it will raise an
        :exc:`AttributeError`.

        .. seealso::

            https://modern.ircdocs.horse/#prefix-parameter

        """
        if 'PREFIX' not in self:
            raise AttributeError('PREFIX')

        # This can use a normal dict once we drop python 3.6, as 3.7 promises
        # `dict` maintains insertion order. Since `OrderedDict` subclasses
        # `dict`, we'll not promise to always return the former.
        return OrderedDict(self['PREFIX'])

    @property
    def TARGMAX(self):
        """Expose ``TARGMAX`` as a dict, if advertised by the server.

        This exposes information about the maximum number of arguments for
        each command::

            >>> isupport.TARGMAX
            {
                'JOIN': None,
                'PRIVMSG': 3,
                'WHOIS': 1,
            }
            >>> isupport['TARGMAX']  # internal representation
            (('JOIN', None), ('PRIVMSG', 3), ('WHOIS', 1))

        This attribute is not available if the server does not provide the
        right information, and accessing it will raise an
        :exc:`AttributeError`.

        The internal representation of ``TARGMAX`` is a tuple of 2-value
        tuples as seen above.

        .. seealso::

            https://modern.ircdocs.horse/#targmax-parameter

        """
        if 'TARGMAX' not in self:
            raise AttributeError('TARGMAX')

        # always return a dict if None or empty tuple
        return dict(self['TARGMAX'] or [])
