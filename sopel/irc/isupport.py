"""IRC Tools for ISUPPORT management.

When a server wants to advertise its features and settings, it can use the
``RPL_ISUPPORT`` command (``005`` numeric) with a list of arguments.

Overview
--------

ISUPPORT parameters provide server capability information to IRC clients,
allowing them to adapt their behavior based on server-specific features,
limits, and supported modes. This module provides parsing and storage for
these parameters.

Common ISUPPORT parameters include:

- **CHANTYPES**: Channel prefixes (e.g., ``#``, ``&``)
- **CHANMODES**: Four groups of channel modes (list, param-always,
  param-set, no-param)
- **PREFIX**: User privilege modes and their nick prefixes (e.g., ``@`` for op)
- **NETWORK**: Network name
- **NICKLEN**, **CHANNELLEN**: Maximum lengths for nicks and channel names
- **CHANLIMIT**: Maximum number of channels per channel type
- **TARGMAX**: Maximum targets per command

Parsing Strategy
----------------

This module implements a parser-based approach where each known ISUPPORT
parameter has a dedicated parsing function. Parameters can be:

1. **Integer values**: ``NICKLEN=30``
2. **String values**: ``NETWORK=Libera``
3. **No value**: ``SAFELIST`` (presence indicates feature availability)
4. **Structured data**: ``PREFIX=(ov)@+`` (parsed into mode-prefix pairs)
5. **Removed parameters**: ``-AWAYLEN`` (prefixed with ``-`` to indicate removal)

The :class:`ISupport` class provides a read-only dict-like interface to access
parsed parameters, with some parameters exposed as convenient properties that
return structured data.

Example
-------

Typical ISUPPORT parsing from server messages::

    >>> params = ['NETWORK=Libera', 'PREFIX=(ov)@+', 'CHANTYPES=#']
    >>> parsed = dict(parse_parameter(p) for p in params)
    >>> isupport = ISupport(**parsed)
    >>> isupport.NETWORK
    'Libera'
    >>> isupport.PREFIX
    OrderedDict([('o', '@'), ('v', '+')])
    >>> isupport['CHANTYPES']
    ('#',)

IRC RFCs and Standards
-----------------------

ISUPPORT is documented in:

- **RPL_ISUPPORT (005)**: Modern IRC documentation at
  https://modern.ircdocs.horse/#rplisupport-005
- **Parameter list**: https://modern.ircdocs.horse/#rplisupport-parameters
- **Original specification**: Based on draft-brocklesby-irc-isupport-03

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
    """Make a parser function optional with a default fallback.

    This decorator transforms a parser to handle empty or None values by
    returning a default value instead of attempting to parse. Useful for
    ISUPPORT parameters that may be advertised without values.

    :param callable parser: The parser function to wrap
    :param default: The default value to return when input is empty or None
                    (defaults to ``None``)
    :return: A wrapped parser that handles empty values gracefully
    :rtype: callable

    Example::

        >>> optional_int = _optional(int, default=0)
        >>> optional_int('42')
        42
        >>> optional_int('')  # Empty value returns default
        0
        >>> optional_int(None)  # None returns default
        0
    """
    @functools.wraps(parser)
    def wrapped(value):
        # Return default for empty strings or None values
        if not value:
            return default
        return parser(value)
    return wrapped


def _no_value(value):
    """Parser for ISUPPORT parameters that don't have meaningful values.

    Some ISUPPORT parameters are boolean flags where their presence indicates
    support for a feature, regardless of any value. This parser ignores the
    value and always returns None.

    :param value: The parameter value (ignored)
    :return: Always returns None
    :rtype: None

    Example::

        >>> _no_value('anything')
        None
        >>> _no_value('')
        None

    Used for parameters like ``SAFELIST`` where the parameter's presence is
    what matters, not its value.
    """
    # Always ignore the value and return None - presence indicates support
    return None


def _single_character(value):
    """Validate and return a single-character value.

    Ensures that the parameter value is exactly one character long. Used for
    ISUPPORT parameters that specify single-character mode letters.

    :param str value: The value to validate
    :return: The single character value
    :rtype: str
    :raises ValueError: If value is more than one character

    Example::

        >>> _single_character('e')
        'e'
        >>> _single_character('I')
        'I'
        >>> _single_character('ab')  # doctest: +SKIP
        ValueError: Too many characters: 'ab'.

    Used for parameters like ``EXCEPTS`` (ban exception mode, typically 'e')
    and ``INVEX`` (invite exception mode, typically 'I').
    """
    # Validate that the value is exactly one character
    if len(value) > 1:
        raise ValueError('Too many characters: %r.' % value)

    return value


def _map_items(parser=str, map_separator=',', item_separator=':'):
    """Create a parser for key-value map parameters.

    Many ISUPPORT parameters contain comma-separated key:value pairs that need
    to be parsed into structured data. This function creates a parser that
    splits the value and applies a type parser to each item's value component.

    :param callable parser: Parser function to apply to values (defaults to str)
    :param str map_separator: Character separating key-value pairs
                              (defaults to ',')
    :param str item_separator: Character separating keys from values
                               (defaults to ':')
    :return: A parser function for map-style parameters
    :rtype: callable

    Example::

        >>> parse_chanlimit = _map_items(int)
        >>> parse_chanlimit('#:70,&:')
        (('&', None), ('#', 70))

    The result is a sorted tuple of (key, value) pairs, where values are
    parsed by the provided parser function or None if no value is present.

    Used for parameters like:
    - ``CHANLIMIT=#:70,&:`` - channel limits per type
    - ``MAXLIST=beI:100,q:50`` - max list entries per mode
    - ``TARGMAX=PRIVMSG:3,WHOIS:1`` - max targets per command
    """
    @functools.wraps(parser)
    def wrapped(value):
        # Split by map_separator (e.g., ',') to get individual items
        # Then split each item by item_separator (e.g., ':') to get key-value pairs
        items = sorted(
            item.split(item_separator)
            for item in value.split(map_separator))

        # Parse values with the provided parser, or None if empty
        return tuple(
            (k, parser(v) if v else None)
            for k, v in items
        )
    return wrapped


def _parse_chanmodes(value):
    """Parse the CHANMODES parameter into categorized mode groups.

    IRC channel modes are categorized into four types (A, B, C, D) based on
    how they behave and whether they require parameters. This parser splits
    the comma-separated mode lists into these categories.

    :param str value: The CHANMODES value (e.g., 'b,k,l,imnpst')
    :return: Tuple of (A, B, C, D, extras) where each element is a string
             of mode characters, and extras is a tuple of any additional groups
    :rtype: tuple
    :raises ValueError: If fewer than 4 mode groups are present

    Mode categories:
    - **Type A**: List modes (e.g., 'b' for ban, 'e' for exception)
    - **Type B**: Modes with a parameter (e.g., 'k' for key/password)
    - **Type C**: Modes with parameter only when set (e.g., 'l' for limit)
    - **Type D**: Modes without parameters (e.g., 'i' for invite-only)

    Example::

        >>> _parse_chanmodes('beI,k,l,imnpst')
        ('beI', 'k', 'l', 'imnpst', ())
        >>> _parse_chanmodes('b,k,l,imnpst,extra,more')
        ('b', 'k', 'l', 'imnpst', ('extra', 'more'))

    .. seealso::

        https://modern.ircdocs.horse/#chanmodes-parameter
    """
    items = value.split(',')

    # Require at least the standard 4 mode type groups
    if len(items) < 4:
        raise ValueError('Not enough channel types to unpack from %r.' % value)

    # Return first 4 groups plus any extras in their own tuple
    # Result structure: (A, B, C, D, (E, F, G, H, ..., Z))
    # Standard groups are A, B, C, D at indices 0-3
    # Any server-specific extras are grouped in a tuple at index 4
    return tuple(items[:4]) + (tuple(items[4:]),)


def _parse_elist(value):
    # letters are case-insensitives
    return tuple(sorted(set(letter.upper() for letter in value)))


def _parse_extban(value):
    args = value.split(',')

    if len(args) < 2:
        raise ValueError('Invalid value for EXTBAN: %r.' % value)

    prefix = args[0] or None
    items = tuple(sorted(set(args[1])))

    return (prefix, items)


def _parse_prefix(value):
    result = re.match(r'\((?P<modes>\S+)\)(?P<prefixes>\S+)', value)

    if not result:
        raise ValueError('Invalid value for PREFIX: %r' % value)

    modes = result.group('modes')
    prefixes = result.group('prefixes')

    if len(modes) != len(prefixes):
        raise ValueError('Mode list does not match for PREFIX: %r' % value)

    return tuple(zip(modes, prefixes))


ISUPPORT_PARSERS = {
    'AWAYLEN': int,
    'CASEMAPPING': str,
    'CHANLIMIT': _map_items(int),
    'CHANMODES': _parse_chanmodes,
    'CHANNELLEN': int,
    'CHANTYPES': _optional(tuple),
    'ELIST': _parse_elist,
    'EXCEPTS': _optional(_single_character, default='e'),
    'EXTBAN': _parse_extban,
    'HOSTLEN': int,
    'INVEX': _optional(_single_character, default='I'),
    'KICKLEN': int,
    'MAXLIST': _map_items(int),
    'MAXTARGETS': _optional(int),
    'MODES': _optional(int),
    'NETWORK': str,
    'NICKLEN': int,
    'PREFIX': _optional(_parse_prefix),
    'SAFELIST': _no_value,
    'SILENCE': _optional(int),
    'STATUSMSG': _optional(tuple),
    'TARGMAX': _optional(_map_items(int), default=tuple()),
    'TOPICLEN': int,
    'USERLEN': int,
}


def parse_parameter(arg):
    items = arg.split('=', 1)
    if len(items) == 2:
        key, value = items
    else:
        key, value = items[0], None

    if key.startswith('-'):
        # ignore value for removed parameters
        return (key, None)

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
        self.__isupport = dict(
            (key.upper(), value)
            for key, value in kwargs.items()
            if not key.startswith('-'))

    def __getitem__(self, key):
        key_ci = key.upper()
        if key_ci not in self.__isupport:
            raise KeyError(key_ci)
        return self.__isupport[key_ci]

    def __contains__(self, key):
        return key.upper() in self.__isupport

    def __getattr__(self, name):
        if name not in self.__isupport:
            raise AttributeError(name)

        return self.__isupport[name]

    def __setattr__(self, name, value):
        # make sure you can't set the value of any ISUPPORT attribute yourself
        if name == '_ISupport__isupport':
            # allow to set self.__isupport inside of the class
            super().__setattr__(name, value)
        elif name in self.__isupport:
            # reject any modification of __isupport
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
        """Build a new instance of :class:`ISupport`.

        :return: a new instance, updated with the latest advertised features
        :rtype: :class:`ISupport`

        This method applies the latest advertised features from the server:
        the result contains the new and updated parameters, and doesn't contain
        the removed parameters (marked by ``-{PARAMNAME}``)::

            >>> updated = {'-AWAYLEN': None, 'NICKLEN': 25, 'CHANNELLEN': 10}
            >>> new = isupport.apply(**updated)
            >>> 'CHANNELLEN' in new
            True
            >>> 'AWAYLEN' in new
            False

        """
        kwargs_upper = dict(
            (key.upper(), value)
            for key, value in kwargs.items()
        )
        kept = (
            (key, value)
            for key, value in self.__isupport.items()
            if ('-%s' % key) not in kwargs_upper
        )
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
