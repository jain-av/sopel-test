# coding=utf-8
"""
ping.py - Sopel Ping Plugin
Copyright 2008 (?), Sean B. Palmer, inamidst.com

https://sopel.chat
"""
import random
from sopel.plugin import rule, priority, thread, command
from sopel.config.types import StaticSection, ListAttribute
from sopel.module import SopelModule

class PingSection(StaticSection):
    greetings = ListAttribute('greetings', default=['Hi', 'Hey', 'Hello'])
    punctuations = ListAttribute('punctuations', default=['', '.', '…', '!'])

class Ping(SopelModule):
    config_section = PingSection

    @rule(r'(?i)(hi|hello|hey),? $nickname[ \t]*$')
    def hello(self, bot, trigger):
        greeting = random.choice(self.config.greetings)
        punctuation = random.choice(self.config.punctuations)
        bot.say(greeting + ' ' + trigger.nick + punctuation)

    @rule(r'(?i)(Fuck|Screw) you,? $nickname[ \t]*$')
    def rude(self, bot, trigger):
        bot.say('Watch your mouth, ' + trigger.nick + ', or I\'ll tell your mother!')

    @rule('$nickname!')
    @priority('high')
    @thread(False)
    def interjection(self, bot, trigger):
        bot.say(trigger.nick + '!')

    @command('ping')
    def ping(self, bot, trigger):
        """Reply to ping command."""
        bot.reply('Pong!')
