# coding=utf-8
"""Tests for Sopel's ``url`` plugin"""
from __future__ import absolute_import, division, print_function, unicode_literals

import re

import pytest
from sopel import bot, config, loader, module, plugin, plugins, trigger
from sopel.modules import url


TMP_CONFIG = """
[core]
owner = testnick
nick = TestBot
enable = coretasks
"""


INVALID_URLS = (
    "http://.example.com/",  # empty label
    "http://example..com/",  # empty label
    "http://?",  # no host
)


@pytest.fixture
def mockbot(configfactory):
    tmpconfig = configfactory('test.cfg', TMP_CONFIG)
    url_plugin = plugins.PyModulePlugin('url', 'sopel.modules')

    # setup the bot
    sopel = bot.Sopel(tmpconfig)
    url_plugin.load()
    url_plugin.setup(sopel)
    url_plugin.register(sopel)

    @module.url(re.escape('https://example.com/') + r'(.+)')
    @plugin.label('handle_urls_https')
    def url_callback_https(bot, trigger):
        pass

    @module.url(re.escape('http://example.com/') + r'(.+)')
    @plugin.label('handle_urls_http')
    def url_callback_http(bot, trigger):
        pass

    # prepare callables to be registered
    callables = [
        url_callback_https,
        url_callback_http,
    ]

    # clean callables and set plugin name by hand
    # since the loader and plugin handlers are excluded here
    for handler in callables:
        loader.clean_callable(handler, sopel.config)
        handler.plugin_name = 'testplugin'

    # register callables
    sopel.register_callbacks(callables)

    # manually register URL Callback
    pattern = re.escape('https://help.example.com/') + r'(.+)'

    def callback(bot, trigger):
        pass

    sopel.register_url_callback(pattern, callback)
    return sopel


@pytest.mark.parametrize("site", INVALID_URLS)
def test_find_title_invalid(site):
    # All local for invalid ones
    assert url.find_title(site) is None


def test_check_callbacks(mockbot):
    """Test that check_callbacks works with both new & legacy URL callbacks."""
    assert url.check_callbacks(mockbot, 'https://example.com/test')
    assert url.check_callbacks(mockbot, 'http://example.com/test')
    assert url.check_callbacks(mockbot, 'https://help.example.com/test')
    assert not url.check_callbacks(mockbot, 'https://not.example.com/test')


def test_url_triggers_rules_and_auto_title(mockbot):
    line = ':Foo!foo@example.com PRIVMSG #sopel :https://not.example.com/test'
    pretrigger = trigger.PreTrigger(mockbot.nick, line)
    results = mockbot.get_rules_for(pretrigger)

    assert len(results) == 1, 'Only one should match'
    result = results[0]
    assert isinstance(result, plugins.Rule)
    assert result.label == 'title_auto'

    line = ':Foo!foo@example.com PRIVMSG #sopel :https://example.com/test'
    pretrigger = trigger.PreTrigger(mockbot.nick, line)
    results = mockbot.get_rules_for(pretrigger)

    assert len(results) == 2, (
        'Two rules should match: title_auto and handle_urls_https')
    labels = sorted(result.label for result in results)
    expected = ['handle_urls_https', 'title_auto']
    assert labels == expected
