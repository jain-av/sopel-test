# coding=utf-8
"""
emoticons.py - Sopel Emoticons Plugin
Copyright 2018, brasstax
Licensed under the Eiffel Forum License 2

https://sopel.chat
"""
from __future__ import absolute_import, division, print_function, unicode_literals

from sopel import plugin


@plugin.command('shrug')
@plugin.action_command('shrugs')
@plugin.example('.shrug', r'¯\_(ツ)_/¯')
def shrug(bot, trigger):
    bot.reply('¯\\_(ツ)_/¯')


@plugin.command('happy')
@plugin.example('.happy', 'ᕕ( ᐛ )ᕗ')
def happy(bot, trigger):
    bot.reply('ᕕ( ᐛ )ᕗ')


@plugin.command('smirk')
@plugin.action_command('smirks')
@plugin.example('.smirk', '(¬‿¬)')
def smirk(bot, trigger):
    bot.reply('(¬‿¬)')


@plugin.command('tableflip', 'tflip')
@plugin.action_command('flips table', 'flips a table', 'flips the table')
@plugin.example('.tableflip', '(╯°□°）╯︵ ┻━┻')
@plugin.example('.tflip', '(╯°□°）╯︵ ┻━┻')
def tableflip(bot, trigger):
    bot.reply('(╯°□°）╯︵ ┻━┻')


@plugin.command('unflip')
@plugin.action_command('unflips table', 'unflips the table')
@plugin.example('.unflip', '┬┬ ﻿ノ( ゜-゜ノ)')
def unflip(bot, trigger):
    bot.reply('┬┬ ﻿ノ( ゜-゜ノ)')


@plugin.command('lenny')
@plugin.example('.lenny', '( ͡° ͜ʖ ͡°)')
def lenny(bot, trigger):
    bot.reply('( ͡° ͜ʖ ͡°)')


@plugin.command('rage', 'anger')
@plugin.example('.rage', 'щ(ಠ益ಠщ)')
@plugin.example('.anger', 'щ(ಠ益ಠщ)')
def anger(bot, trigger):
    bot.reply('щ(ಠ益ಠщ)')


@plugin.command('cry')
@plugin.action_command('cries')
@plugin.example('.cry', '( p′︵‵。)')
def cry(bot, trigger):
    bot.reply('( p′︵‵。)')


@plugin.command('love')
@plugin.example('.love', '(●♡∀♡)')
def love(bot, trigger):
    bot.reply('(●♡∀♡)')


@plugin.command('success', 'winner')
@plugin.example('.success', '٩( ᐛ )و')
@plugin.example('.winner', '٩( ᐛ )و')
def success(bot, trigger):
    bot.reply('٩( ᐛ )و')


@plugin.command('confused', 'wat')
@plugin.example('.confused', '(●__●)???')
@plugin.example('.wat', '(●__●)???')
def wat(bot, trigger):
    bot.reply('(●__●)???')


@plugin.command('crazy')
@plugin.example('.crazy', '⊙_ʘ')
def crazy(bot, trigger):
    bot.reply('⊙_ʘ')


@plugin.command('hungry')
@plugin.example('.hungry', 'ლ(´ڡ`ლ)')
def hungry(bot, trigger):
    bot.reply('ლ(´ڡ`ლ)')


@plugin.command('surprised')
@plugin.example('.surprised', '(((( ;°Д°))))')
def surprised(bot, trigger):
    bot.reply('(((( ;°Д°))))')


@plugin.command('sick')
@plugin.example('.sick', '(-﹏-。)')
def sick(bot, trigger):
    bot.reply('(-﹏-。)')


@plugin.command('afraid')
@plugin.example('.afraid', '(　〇□〇）')
def afraid(bot, trigger):
    bot.reply('(　〇□〇）')


@plugin.command('worried')
@plugin.example('.worried', '(　ﾟдﾟ)')
def worried(bot, trigger):
    bot.reply('(　ﾟдﾟ)')
