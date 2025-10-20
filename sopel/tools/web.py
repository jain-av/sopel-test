"""
The ``tools.web`` package contains utility functions for interaction with web
applications, APIs, or websites in your plugins.

.. versionadded:: 7.0
"""
# Copyright © 2008, Sean B. Palmer, inamidst.com
# Copyright © 2009, Michael Yanovich <yanovich.1@osu.edu>
# Copyright © 2012, Dimitri Molenaars, Tyrope.nl.
# Copyright © 2012-2013, Elad Alfassa, <elad@fedoraproject.org>
# Copyright © 2019, dgw, technobabbl.es
# Licensed under the Eiffel Forum License 2.

from __future__ import annotations

import html
from html.entities import name2codepoint
import re
import urllib
from urllib.parse import urlparse, urlunparse

from sopel import __version__, tools


__all__ = [
    'USER_AGENT',
    'DEFAULT_HEADERS',
    'decode',
    'entity',
    'iri_to_uri',
    'quote',
    'unquote',
    'quote_query',
    'search_urls',
    'trim_url',
    'urlencode',
    'urlencode_non_ascii',
]

USER_AGENT = 'Sopel/{} (https://sopel.chat)'.format(__version__)
"""User agent string to be sent with HTTP requests.

This constant contains a properly formatted User-Agent string following the
format ``"Sopel/VERSION (PROJECT_URL)"``, which identifies the bot software
and version to web servers. Using a descriptive User-Agent helps server
administrators identify legitimate bot traffic and is considered polite HTTP
etiquette.

Example usage with the ``requests`` library::

    import requests

    from sopel.tools import web

    result = requests.get(
        'https://some.site/api/endpoint',
        user_agent=web.USER_AGENT
    )

.. note::
    The version number is automatically populated from ``sopel.__version__``
    at import time.

"""
DEFAULT_HEADERS = {'User-Agent': USER_AGENT}
"""Default header dict for use with ``requests`` methods.

Use it like this::

    import requests

    from sopel.tools import web

    result = requests.get(
        'https://some.site/api/endpoint',
        headers=web.DEFAULT_HEADERS
    )

.. important::
   You should *never* modify this directly in your plugin code. Make a copy
   and use :py:meth:`~dict.update` if you need to add or change headers::

       from sopel.tools import web

       default_headers = web.DEFAULT_HEADERS.copy()
       custom_headers = {'Accept': 'text/*'}

       default_headers.update(custom_headers)

"""


r_entity = re.compile(r'&([^;\s]+);')
"""Regular expression to match HTML entities.

.. deprecated:: 8.0

    Will be removed in Sopel 9, along with :func:`entity`.
"""


@tools.deprecated(
    version='8.0',
    removed_in='9.0',
    reason="No longer needed now that Python 3.4+ has `html.unescape()`",
)
def entity(match):
    """Convert an entity reference to the appropriate character.

    :param str match: the entity name or code, as matched by
        :py:const:`r_entity`
    :return str: the Unicode character corresponding to the given ``match``
        string, or a fallback representation if the reference cannot be
        resolved to a character

    .. deprecated:: 8.0

        Will be removed in Sopel 9. Use :func:`decode` directly or migrate to
        Python's standard-library equivalent, :func:`html.unescape`.

    """
    value = match.group(1).lower()
    if value.startswith('#x'):
        return chr(int(value[2:], 16))
    elif value.startswith('#'):
        return chr(int(value[1:]))
    elif value in name2codepoint:
        return chr(name2codepoint[value])
    return '[' + value + ']'


def decode(text):
    """Decode HTML entities into Unicode text.

    :param str text: the HTML page or snippet to process
    :return str: ``text`` with all entity references replaced

    .. versionchanged:: 8.0

        Renamed ``html`` parameter to ``text``. (Python gained a standard
        library module named :mod:`html` in version 3.4.)

    """
    # TODO deprecated?
    return html.unescape(text)


def quote(string, safe='/'):
    """Safely encodes a string for use in a URL.

    :param str string: the string to encode; will be converted to string if not
                       already
    :param str safe: characters that should not be percent-encoded; defaults to
                     ``'/'`` to preserve path separators. Common values include
                     ``''`` (encode everything) or ``'/='`` (preserve paths and
                     query parameter separators)
    :return str: the ``string`` with special characters percent-encoded using
                 UTF-8 encoding (e.g., spaces become ``%20``, ``&`` becomes
                 ``%26``)

    This function uses UTF-8 encoding to convert characters to percent-encoded
    format per :rfc:`3986`. ASCII letters, digits, and the characters
    ``_.-~`` are never encoded, along with any characters specified in the
    ``safe`` parameter.

    Example::

        quote("hello world")  # Returns: "hello%20world"
        quote("path/to/file", safe='')  # Returns: "path%2Fto%2Ffile"
        quote("key=value", safe='=')  # Returns: "key=value"

    .. note::
        This is a convenient wrapper around :py:func:`urllib.parse.quote`.

    """
    # TODO deprecated?
    return urllib.parse.quote(str(string), safe)


# six-like shim for Unicode safety
def unquote(string):
    """Decodes a URL-encoded (percent-encoded) string.

    :param str string: the percent-encoded string to decode (e.g.,
                       ``"hello%20world"``)
    :return str: the decoded ``string`` with percent-encoded sequences replaced
                 by their corresponding UTF-8 characters

    This function decodes percent-encoded sequences (e.g., ``%20``, ``%26``) back
    to their original UTF-8 characters. It handles UTF-8 multi-byte sequences
    correctly.

    Example::

        unquote("hello%20world")  # Returns: "hello world"
        unquote("path%2Fto%2Ffile")  # Returns: "path/to/file"
        unquote("caf%C3%A9")  # Returns: "café"

    .. note::

        This is a convenient wrapper around :py:func:`urllib.parse.unquote`,
        which always uses UTF-8 encoding for decoding percent-encoded sequences.

    """
    # TODO deprecated?
    return urllib.parse.unquote(string)


def quote_query(string):
    """Safely encodes a URL's query parameters.

    :param str string: a URL containing query parameters
    :return str: the input URL with query parameter values URL-encoded
    """
    parsed = urlparse(string)
    string = string.replace(parsed.query, quote(parsed.query, "/=&"), 1)
    return string


# Functions for international domain name magic

def urlencode_non_ascii(b):
    """Percent-encodes non-ASCII bytes in a URL component.

    :param bytes b: a byte string representing a URL component
    :return bytes: the byte string with non-ASCII bytes (0x80-0xFF)
                   percent-encoded

    This helper function is used by :py:func:`iri_to_uri` to encode non-ASCII
    bytes in URL components (path, query, fragment) that have been UTF-8
    encoded. It converts each byte with value 128 or higher into percent-encoded
    form (e.g., byte ``0xC3`` becomes ``b'%c3'``).

    Example::

        urlencode_non_ascii(b'caf\\xc3\\xa9')
        # Returns: b'caf%c3%a9'

    """
    return re.sub(b'[\x80-\xFF]', lambda c: '%%%02x' % ord(c.group(0)), b)


def iri_to_uri(iri):
    """Converts an Internationalized Resource Identifier (IRI) to a URI.

    :param str iri: an IRI that may contain non-ASCII characters (e.g.,
                    Unicode domain names, non-ASCII path segments)
    :return str: a fully ASCII-compatible URI suitable for use in HTTP requests
    :raise UnicodeError: if the IRI contains characters that cannot be encoded

    An IRI is the internationalized version of a URI that allows Unicode
    characters, while a URI must contain only ASCII characters. This function
    performs the necessary conversions per :rfc:`3987`:

    1. **Domain names** (the netloc/host component): Converted using IDNA
       encoding (Internationalized Domain Names in Applications). For example,
       ``"münchen.de"`` becomes ``"xn--mnchen-3ya.de"``.

    2. **Other URL components** (scheme, path, params, query, fragment):
       Non-ASCII bytes are percent-encoded. For example, ``"café"`` in a path
       becomes ``"caf%C3%A9"``.

    Example::

        # Internationalized domain name
        iri_to_uri("http://münchen.de/")
        # Returns: "http://xn--mnchen-3ya.de/"

        # Unicode in path
        iri_to_uri("https://example.com/café/menu")
        # Returns: "https://example.com/caf%C3%A9/menu"

        # Both domain and path
        iri_to_uri("https://münchen.de/café")
        # Returns: "https://xn--mnchen-3ya.de/caf%C3%A9"

    .. note::
        This function is used internally by :py:func:`search_urls` to ensure
        extracted URLs are in a canonical ASCII form.

    """
    parts = urlparse(iri)
    # Convert each URL component: IDNA for domain (index 1), percent-encoding for others
    parts_seq = list(
        part.encode('idna')
        if parti == 1 else urlencode_non_ascii(part.encode('utf-8'))
        for parti, part in enumerate(parts)
    )
    parsed = urlunparse(parts_seq)
    return parsed.decode()


# direct shortcut kept for backward compatibility reasons
# TODO consider removing this
urlencode = urllib.parse.urlencode


# Functions for URL detection

def trim_url(url):
    """Removes extra punctuation from URLs found in text.

    :param str url: the raw URL match
    :return str: the cleaned URL

    This function removes trailing punctuation that looks like it was not
    intended to be part of the URL:

    * trailing sentence- or clause-ending marks like ``.``, ``;``, etc.
    * unmatched trailing brackets/braces like ``}``, ``)``, etc.

    It is intended for use with the output of :py:func:`~.search_urls`, which
    may include trailing punctuation when used on input from chat.
    """
    # clean trailing sentence- or clause-ending punctuation
    while url[-1] in '.,?!\'":;':
        url = url[:-1]

    # clean unmatched parentheses/braces/brackets
    for (opener, closer) in [('(', ')'), ('[', ']'), ('{', '}'), ('<', '>')]:
        if url[-1] == closer and url.count(opener) < url.count(closer):
            url = url[:-1]

    return url


def search_urls(text, exclusion_char=None, clean=False, schemes=None):
    """Extracts all URLs in ``text``.

    :param str text: the text to search for URLs
    :param str exclusion_char: optional character that, if placed before a URL
        in the ``text``, will exclude it from being extracted
    :param bool clean: if ``True``, all found URLs are passed through
        :py:func:`~.trim_url` before being returned; default ``False``
    :param list schemes: optional list of URL schemes to look for; defaults to
        ``['http', 'https', 'ftp']``
    :return: :py:term:`generator iterator` of all URLs found in ``text``

    To get the URLs as a plain list, use e.g.::

        list(search_urls(text))

    """
    schemes = schemes or ['http', 'https', 'ftp']
    schemes_patterns = '|'.join(re.escape(scheme) for scheme in schemes)
    re_url = r'((?:%s)(?::\/\/\S+))' % schemes_patterns
    if exclusion_char is not None:
        re_url = r'((?<!%s)(?:%s)(?::\/\/\S+))' % (
            exclusion_char, schemes_patterns)

    r = re.compile(re_url, re.IGNORECASE | re.UNICODE)

    urls = re.findall(r, text)
    if clean:
        urls = (trim_url(url) for url in urls)

    # yield unique URLs in their order of appearance
    seen = set()
    for url in urls:
        try:
            url = iri_to_uri(url)
        except Exception:  # TODO: Be specific
            pass

        if url not in seen:
            seen.add(url)
            yield url
