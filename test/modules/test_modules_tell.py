# coding=utf-8
"""Tests for Sopel's ``tell`` plugin"""
from __future__ import absolute_import, division, print_function, unicode_literals

import datetime
import io
import os
import sys

import pytest

from sopel import formatting
from sopel.modules import tell


if sys.version_info.major >= 3:
    unicode = str


def test_load_reminders_empty(tmpdir):
    tmpfile = tmpdir.join('tell.db')
    tmpfile.write('\n')
    result = tell.load_reminders(tmpfile.strpath)

    assert len(result.keys()) == 0, 'There should be no key at all.'


def test_load_reminders_one_tellee(tmpdir):
    tellee = 'Exirel'
    teller = 'dgw'
    verb = 'tell'
    timenow = '1569488444'
    msg_1 = 'You forgot an S in "élèves".'
    msg_2 = 'You forgot another S in "garçons".'
    tmpfile = tmpdir.join('tell.db')

    reminders = [
        [tellee, teller, verb, timenow, msg_1],
        [tellee, teller, verb, timenow, msg_2],
    ]
    tmpfile.write_text(
        '\n'.join('\t'.join(item) for item in reminders),
        encoding='utf-8')
    result = tell.load_reminders(tmpfile.strpath)

    assert len(result.keys()) == 1, (
        'There should be one and only one key, found these instead: %s'
        % ', '.join(result.keys())
    )
    assert 'Exirel' in result, (
        'Tellee not found, found %s instead' % result.keys()[0])
    assert len(result['Exirel']) == 2, (
        'There should be two reminders, found %d instead'
        % len(result['Exirel'])
    )
    reminder_1 = result['Exirel'][0]
    reminder_2 = result['Exirel'][1]
    assert reminder_1 == (teller, verb, timenow, msg_1)
    assert reminder_2 == (teller, verb, timenow, msg_2)


def test_load_reminders_multiple_tellees(tmpdir):
    tellee_1 = 'Exirel'
    tellee_2 = 'HumorBaby'
    teller = 'dgw'
    verb = 'tell'
    timenow = '1569488444'
    msg = 'Review requested for "àçé"'
    tmpfile = tmpdir.join('tell.db')

    reminders = [
        [tellee_1, teller, verb, timenow, msg],
        [tellee_2, teller, verb, timenow, msg],
    ]
    tmpfile.write_text(
        '\n'.join('\t'.join(item) for item in reminders),
        encoding='utf-8')
    result = tell.load_reminders(tmpfile.strpath)

    assert len(result.keys()) == 2, (
        'There should be two keys, found these instead: %s'
        % ', '.join(result.keys())
    )
    assert tellee_1 in result
    assert tellee_2 in result
    assert len(result[tellee_1]) == 1, (
        'There should be one reminder for %s, found %d instead'
        % (tellee_1, len(result[tellee_1]))
    )
    assert len(result[tellee_2]) == 1, (
        'There should be one reminder for %s, found %d instead'
        % (tellee_2, len(result[tellee_2]))
    )
    reminder_1 = result[tellee_1][0]
    reminder_2 = result[tellee_2][0]
    assert reminder_1 == (teller, verb, timenow, msg)
    assert reminder_2 == (teller, verb, timenow, msg)


def test_load_reminders_irc_formatting(tmpdir):
    tellee = 'Exirel'
    teller = 'dgw'
    verb = 'tell'
    timenow = '1569488444'
    formatted_message = (
        'This message has \x0301,04colored text\x03, \x0400ff00hex-colored '
        'text\x04, \x02bold\x02, \x1ditalics\x1d, \x1funderline\x1f, '
        '\x11monospace\x11, \x16reverse\x16, \x1estrikethrough or\x0f '
        'strikethrough and normal text.')
    tmpfile = tmpdir.join('tell.db')

    reminders = [
        [tellee, teller, verb, timenow, formatted_message],
    ]
    tmpfile.write_text(
        '\n'.join('\t'.join(item) for item in reminders),
        encoding='utf-8')
    result = tell.load_reminders(tmpfile.strpath)

    assert len(result.keys()) == 1, (
        'There should be one and only one key, found these instead: %s'
        % ', '.join(result.keys())
    )
    assert 'Exirel' in result, (
        'Tellee not found, found %s instead' % result.keys()[0])
    assert len(result['Exirel']) == 1, (
        'There should be one reminder, found %d instead'
        % len(result['Exirel'])
    )
    reminder = result['Exirel'][0]
    assert reminder == (teller, verb, timenow, formatted_message)


def test_dump_reminders_empty(tmpdir):
    tmpfile = tmpdir.join('tell.db')
    tell.dump_reminders(tmpfile.strpath, {})

    assert os.path.exists(tmpfile.strpath)

    with io.open(tmpfile.strpath, 'r', encoding='utf-8') as fd:
        data = fd.read()
        assert not data, 'Data for empty tell should be empty'


def test_dump_reminders_one_tellee(tmpdir):
    tellee = 'Exirel'
    teller = 'dgw'
    verb = 'tell'
    timenow = '1569488444'
    msg_1 = 'You forgot an S in "élèves".'
    msg_2 = 'You forgot another S  in "garçons".'

    tmpfile = tmpdir.join('tell.db')
    tell.dump_reminders(tmpfile.strpath, {
        tellee: [
            (teller, verb, timenow, msg_1),
            (teller, verb, timenow, msg_2),
        ]
    })

    assert os.path.exists(tmpfile.strpath)

    results = tell.load_reminders(tmpfile.strpath)

    assert len(results.keys()) == 1, 'There should be one key in results'
    assert tellee in results
    assert len(results[tellee]) == 2, 'There should be 2 messages in results'
    assert (teller, verb, timenow, msg_1) in results[tellee]
    assert (teller, verb, timenow, msg_2) in results[tellee]


def test_dump_reminders_multiple_tellees(tmpdir):
    tellee_1 = 'Exirel'
    tellee_2 = 'HumorBaby'
    teller = 'dgw'
    verb = 'tell'
    timenow = '1569488444'
    msg = 'Review requested for "àçé"'

    tmpfile = tmpdir.join('tell.db')
    tell.dump_reminders(tmpfile.strpath, {
        tellee_1: [(teller, verb, timenow, msg)],
        tellee_2: [(teller, verb, timenow, msg)],
    })

    assert os.path.exists(tmpfile.strpath)

    results = tell.load_reminders(tmpfile.strpath)

    assert len(results.keys()) == 2, 'There should be two keys in results'
    assert tellee_1 in results
    assert tellee_2 in results
    assert len(results[tellee_1]) == 1, (
        'There should be 1 message for %s' % tellee_1)
    assert len(results[tellee_2]) == 1, (
        'There should be 1 message for %s' % tellee_2)
    assert (teller, verb, timenow, msg) in results[tellee_1]
    assert (teller, verb, timenow, msg) in results[tellee_2]


def test_nick_match_tellee():
    assert tell.nick_match_tellee('Exirel', 'exirel')
    assert tell.nick_match_tellee('exirel', 'EXIREL')
    assert not tell.nick_match_tellee('exirel', 'dgw')


def test_nick_match_tellee_wildcards():
    assert tell.nick_match_tellee('Exirel', 'Exirel*')
    assert tell.nick_match_tellee('Exirel', 'Exi*')
    assert tell.nick_match_tellee('Exirel', 'exi*')
    assert tell.nick_match_tellee('exirel', 'EXI*')
    assert tell.nick_match_tellee('Exirel', 'Exi:')
    assert tell.nick_match_tellee('Exirel', 'exi:')
    assert tell.nick_match_tellee('exirel', 'EXI:')
    assert tell.nick_match_tellee('Exirel', 'Exirel:')
    assert not tell.nick_match_tellee('Exirel', 'dgw:')
    assert not tell.nick_match_tellee('Exirel', 'dgw*')


def test_get_reminders():
    today = datetime.datetime.now().date().strftime('%Y-%m-%d')
    reminders = [
        ('dgw', 'tell', '2019-09-25 - 14:34:08UTC', 'Let\'s fix all the things!'),
        ('HumorBaby', 'tell', '%s - 14:35:55UTC' % today, 'Thanks for the review.'),
    ]

    lines = tell.get_nick_reminders(reminders, 'Exirel')

    assert len(lines) == 2, (
        'There should be as many lines as there are reminders, found %d'
        % len(lines))
    assert lines[0] == (
        'Exirel: '
        '2019-09-25 - 14:34:08UTC '
        '<dgw> tell Exirel Let\'s fix all the things!')
    assert lines[1] == (
        'Exirel: '
        '%s - 14:35:55UTC '
        '<HumorBaby> tell Exirel Thanks for the review.' % today)


# Test custom lstrip implementation

UNICODE_ZS_CATEGORY = [
    '\u0020',  # SPACE
    '\u00A0',  # NO-BREAK SPACE
    '\u1680',  # OGHAM SPACE MARK
    '\u2000',  # EN QUAD
    '\u2001',  # EM QUAD
    '\u2002',  # EN SPACE
    '\u2003',  # EM SPACE
    '\u2004',  # THREE-PER-EM SPACE
    '\u2005',  # FOUR-PER-EM SPACE
    '\u2006',  # SIX-PER-EM SPACE
    '\u2007',  # FIGURE SPACE
    '\u2008',  # PUNCTUATION SPACE
    '\u2009',  # THIN SPACE
    '\u200A',  # HAIR SPACE
    '\u202F',  # NARROW NO-BREAK SPACE
    '\u205F',  # MEDIUM MATHEMATICAL SPACE
    '\u3000',  # IDEOGRAPHIC SPACE
]

SAFE_PAIRS = (
    # regression checks vs. old string.lstrip()
    ('',
     ''),
    ('a',  # one iteration of this code returned '' for one-char strings
     'a'),
    ('aa',
     'aa'),
    # basic whitespace
    ('  leading space',  # removed
     'leading space'),
    ('trailing space ',  # kept
     'trailing space '),
    (' leading AND trailing space  ',  # removed AND kept
     'leading AND trailing space  '),
    # advanced whitespace
    ('\tleading tab',  # removed
     'leading tab'),
    ('trailing tab\t',  # kept
     'trailing tab\t'),
    # whitespace inside formatting (kept)
    ('\x02  leading space inside formatting\x02',
     '\x02  leading space inside formatting\x02'),
    ('\x02trailing space inside formatting  \x02',
     '\x02trailing space inside formatting  \x02'),
    ('\x02  leading AND trailing inside formatting  \x02',
     '\x02  leading AND trailing inside formatting  \x02'),
    # whitespace outside formatting
    ('  \x02leading space outside formatting\x02',  # removed
     '\x02leading space outside formatting\x02'),
    ('\x02trailing space outside formatting\x02  ',  # kept
     '\x02trailing space outside formatting\x02  '),
    # whitespace both inside and outside formatting
    ('  \x02  leading space inside AND outside\x02',  # outside removed
     '\x02  leading space inside AND outside\x02'),
    ('\x02trailing space inside AND outside  \x02  ',  # left alone
     '\x02trailing space inside AND outside  \x02  '),
    ('  \x02  leading AND trailing inside AND outside  \x02  ',  # only leading removed
     '\x02  leading AND trailing inside AND outside  \x02  '),
)

def test_format_safe_lstrip_basic():
    """Test handling of basic whitespace."""
    assert tell._format_safe_lstrip(
        ''.join(UNICODE_ZS_CATEGORY)) == ''


def test_format_safe_lstrip_control():
    """Test handling of non-printing control characters."""
    all_formatting = ''.join(formatting.CONTROL_FORMATTING)

    # no formatting chars should be stripped,
    # but a reset should be added to the end
    assert tell._format_safe_lstrip(all_formatting) == all_formatting

    # control characters not recognized as formatting should be stripped
    assert tell._format_safe_lstrip(
        ''.join(
            c
            for c in formatting.CONTROL_NON_PRINTING
            if c not in formatting.CONTROL_FORMATTING
        )) == ''


def test_format_safe_lstrip_invalid_arg():
    """Test for correct exception if non-string is passed."""
    with pytest.raises(TypeError):
        tell._format_safe_lstrip(None)


@pytest.mark.parametrize('text, cleaned', SAFE_PAIRS)
def test_format_safe_lstrip_pairs(text, cleaned):
    """Test expected formatting-safe string sanitization."""
    assert tell._format_safe_lstrip(text) == cleaned
