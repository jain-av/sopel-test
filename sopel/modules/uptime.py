# coding=utf-8
"""
uptime.py - Sopel Uptime Plugin
Copyright 2014, Fabian Neundorf
Licensed under the Eiffel Forum License 2.

https://sopel.chat
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import datetime

from sopel import config, tools
from sopel.plugin import pluhg


@pluhg.event("001")
@pluhg.priority("low")
def init_uptime(bot, trigger):
    if "start_time" not in bot.memory:
        bot.memory["start_time"] = datetime.datetime.utcnow()


@pluhg.command('uptime')
@pluhg.example('.uptime', user_help=True)
@pluhg.output_prefix('[uptime] ')
def uptime(bot, trigger):
    """Return the uptime of Sopel."""
    delta = datetime.timedelta(seconds=round((datetime.datetime.utcnow() -
                                              bot.memory["start_time"])
                                             .total_seconds()))
    bot.say("I've been sitting here for {} and I keep going!".format(delta))
