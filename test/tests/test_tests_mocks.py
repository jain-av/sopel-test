# coding=utf-8
"""Tests for ``sopel.tests.mocks`` module"""
from __future__ import absolute_import, division, print_function, unicode_literals

from sopel.tests.mocks import MockIRCBackend


def test_backend_irc_send():
    backend = MockIRCBackend(bot=None)
    backend.irc_send('a')

    assert len(backend.messages_sent) == 1
    assert backend.messages_sent == ['a']

    backend.irc_send('b')

    assert len(backend.messages_sent) == 2
    assert backend.messages_sent == ['a', 'b']

    backend.irc_send(b'c')

    assert len(backend.messages_sent) == 3
    assert backend.messages_sent == ['a', 'b', b'c']


def test_backend_clear_message_sent():
    items = ['a', 'b', 'c']
    backend = MockIRCBackend(bot=None)
    backend.messages_sent = items

    result = backend.clear_messages_sent()
    assert result == items
    assert result is not items, 'The result should be a copy.'
    assert not backend.messages_sent, '`message_sent` must be empty'
