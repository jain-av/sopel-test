# coding=utf-8
"""
wiktionary.py - Sopel Wiktionary Plugin
Copyright 2009, Sean B. Palmer, inamidst.com
Licensed under the Eiffel Forum License 2.

https://sopel.chat
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import re

import requests
from bs4 import BeautifulSoup

from sopel import plugin
from sopel.tools import web

PLUGIN_OUTPUT_PREFIX = '[wiktionary] '

uri = 'https://en.wiktionary.org/w/index.php?title=%s&printable=yes'


# From https://en.wiktionary.org/wiki/Wiktionary:Entry_layout#Part_of_speech
PARTS_OF_SPEECH = [
    # Parts of speech
    'Adjective', 'Adverb', 'Ambiposition', 'Article', 'Circumposition',
    'Classifier', 'Conjunction', 'Contraction', 'Counter', 'Determiner',
    'Ideophone', 'Interjection', 'Noun', 'Numeral', 'Participle', 'Particle',
    'Postposition', 'Preposition', 'Pronoun', 'Proper noun', 'Verb',
    # Morphemes
    'Circumfix', 'Combining form', 'Infix', 'Interfix', 'Prefix', 'Root', 'Suffix',
    # Symbols and characters
    'Diacritical mark', 'Letter', 'Ligature', 'Number', 'Punctuation mark', 'Syllable', 'Symbol',
    # Phrases
    'Phrase', 'Proverb', 'Prepositional phrase',
    # Han characters and language-specific varieties
    'Han character', 'Hanzi', 'Kanji', 'Hanja',
    # Other
    'Romanization',
]


def text(html):
    soup = BeautifulSoup(html, 'html.parser')
    # Remove superscripts that are references from definition
    for sup in soup.find_all('sup'):
        sup.decompose()
    text = soup.get_text().strip()
    text = text.replace('\n', ' ')
    text = text.replace('\r', '')
    text = text.replace('(intransitive', '(intr.')
    text = text.replace('(transitive', '(trans.')
    text = web.decode(text)
    return text.strip()


def wikt(word):
    response = requests.get(uri % web.quote(word))
    response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
    html_content = response.text
    soup = BeautifulSoup(html_content, 'html.parser')

    etymology = None
    definitions = {}
    mode = None

    bodyContent = soup.find('div', {'id': 'bodyContent'})

    if not bodyContent:
        return etymology, definitions

    for element in bodyContent.find_all(recursive=False):
        is_new_mode = False

        if element.name == 'h2':
            span = element.find('span', class_='mw-headline')
            if span and span.get('id') == 'Etymology':
                mode = 'etymology'
                is_new_mode = True
            else:
                for pos in PARTS_OF_SPEECH:
                    if span and span.get('id') == pos.replace(' ', '_'):
                        mode = pos.lower()
                        is_new_mode = True
                        break

        if not is_new_mode:
            if mode == 'etymology' and element.name == 'p':
                if etymology is not None:
                    # multi-line etymologies do exist (e.g. see "mayhem")
                    etymology += ' ' + text(str(element))
                else:
                    etymology = text(str(element))
            elif mode is not None and element.name == 'ul':
                items = []
                for li in element.find_all('li', recursive=False):
                    items.append(text(str(li)))
                definitions.setdefault(mode, []).extend(items)

        if element.name == 'hr':
            break

    return etymology, definitions


parts = [pos.lower() for pos in PARTS_OF_SPEECH]


def format(result, definitions, number=2):
    for part in parts:
        if part in definitions:
            defs = definitions[part][:number]
            result += u' — {}: '.format(part)
            n = ['%s. %s' % (i + 1, e.strip(' .')) for i, e in enumerate(defs)]
            result += ', '.join(n)
    return result.strip(' .,')


@plugin.command('wt', 'define', 'dict')
@plugin.example('.wt bailiwick', "bailiwick — noun: 1. The district within which a bailie or bailiff has jurisdiction, 2. A person's concern or sphere of operations, their area of skill or authority")
@plugin.output_prefix(PLUGIN_OUTPUT_PREFIX)
def wiktionary(bot, trigger):
    """Look up a word on Wiktionary."""
    word = trigger.group(2)
    if word is None:
        bot.reply('You must tell me what to look up!')
        return

    _etymology, definitions = wikt(word)
    if not definitions:
        # Cast word to lower to check in case of mismatched user input
        _etymology, definitions = wikt(word.lower())
        if not definitions:
            bot.reply("Couldn't get any definitions for %s." % word)
            return

    result = format(word, definitions)
    if len(result) < 300:
        result = format(word, definitions, 3)
    if len(result) < 300:
        result = format(word, definitions, 5)

    bot.say(result, truncation=' […]')


@plugin.command('ety')
@plugin.example('.ety bailiwick', "bailiwick: From bailie (“bailiff”) and wick (“dwelling”), from Old English wīc.")
@plugin.output_prefix(PLUGIN_OUTPUT_PREFIX)
def wiktionary_ety(bot, trigger):
    """Look up a word's etymology on Wiktionary."""
    word = trigger.group(2)
    if word is None:
        bot.reply('You must give me a word!')
        return

    etymology, _definitions = wikt(word)
    if not etymology:
        bot.reply("Couldn't get the etymology for %s." % word)
        return

    result = "{}: {}".format(word, etymology)

    bot.say(result, truncation=' […]')
