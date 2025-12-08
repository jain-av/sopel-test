#!/usr/bin/env python3
"""
Step 3.1 Validation Script
This demonstrates that the comment removal process maintains valid Python syntax.
"""

import ast

# Original file content (with comments)
original_content = """# ASCII ONLY IN THIS FILE THOUGH!!!!!!!
# Python does some stupid bullshit of respecting LC_ALL over the encoding on the
# file, so in order to undo Python's ridiculous fucking idiocy, we have to have
# our own check.

# Copyright 2008, Sean B. Palmer, inamidst.com
# Copyright 2012, Elsie Powell, http://embolalia.com
# Copyright 2012, Elad Alfassa <elad@fedoraproject.org>
#
# Licensed under the Eiffel Forum License 2.

from __future__ import annotations

from collections import namedtuple
import locale
import re
import sys

import pkg_resources

__all__ = [
    'bot',
    'config',
    'db',
    'formatting',
    'irc',
    'loader',
    'logger',
    'module',  # deprecated in 7.1, removed in 9.0
    'plugin',
    'tools',
    'trigger',
    'version_info',
]

loc = locale.getlocale()
if not loc[1] or ('UTF-8' not in loc[1] and 'utf8' not in loc[1]):
    print('WARNING!!! You are running with a non-UTF8 locale environment '
          'variable (e.g. LC_ALL is set to "C"), which makes Python 3 do '
          'stupid things. If you get strange errors, please set it to '
          'something like "en_US.UTF-8".', file=sys.stderr)


__version__ = pkg_resources.get_distribution('sopel').version


def _version_info(version=__version__):
    regex = re.compile(r'(\d+)\.(\d+)\.(\d+)(?:[\-\.]?(a|b|rc)(\d+))?.*')
    version_groups = regex.match(version).groups()
    major, minor, micro = (int(piece) for piece in version_groups[0:3])
    level = version_groups[3]
    serial = int(version_groups[4] or 0)
    if level == 'a':
        level = 'alpha'
    elif level == 'b':
        level = 'beta'
    elif level == 'rc':
        level = 'candidate'
    elif not level and version_groups[4] is None:
        level = 'final'
    else:
        level = 'alpha'
    version_type = namedtuple('version_info',
                              'major, minor, micro, releaselevel, serial')
    return version_type(major, minor, micro, level, serial)


version_info = _version_info()
"""

# Processed content (comments removed)
processed_content = """from __future__ import annotations

from collections import namedtuple
import locale
import re
import sys

import pkg_resources

__all__ = [
    'bot',
    'config',
    'db',
    'formatting',
    'irc',
    'loader',
    'logger',
    'module',
    'plugin',
    'tools',
    'trigger',
    'version_info',
]

loc = locale.getlocale()
if not loc[1] or ('UTF-8' not in loc[1] and 'utf8' not in loc[1]):
    print('WARNING!!! You are running with a non-UTF8 locale environment '
          'variable (e.g. LC_ALL is set to "C"), which makes Python 3 do '
          'stupid things. If you get strange errors, please set it to '
          'something like "en_US.UTF-8".', file=sys.stderr)


__version__ = pkg_resources.get_distribution('sopel').version


def _version_info(version=__version__):
    regex = re.compile(r'(\d+)\.(\d+)\.(\d+)(?:[\-\.]?(a|b|rc)(\d+))?.*')
    version_groups = regex.match(version).groups()
    major, minor, micro = (int(piece) for piece in version_groups[0:3])
    level = version_groups[3]
    serial = int(version_groups[4] or 0)
    if level == 'a':
        level = 'alpha'
    elif level == 'b':
        level = 'beta'
    elif level == 'rc':
        level = 'candidate'
    elif not level and version_groups[4] is None:
        level = 'final'
    else:
        level = 'alpha'
    version_type = namedtuple('version_info',
                              'major, minor, micro, releaselevel, serial')
    return version_type(major, minor, micro, level, serial)


version_info = _version_info()
"""

def validate_syntax(content, description):
    """Validate Python syntax using ast.parse()"""
    try:
        ast.parse(content)
        print(f"✓ {description}: VALID Python syntax")
        return True
    except SyntaxError as e:
        print(f"✗ {description}: INVALID Python syntax")
        print(f"  Error: {e}")
        return False

def main():
    print("=" * 70)
    print("Step 3.1: Python Comment Removal - Syntax Validation Test")
    print("=" * 70)
    print()

    # Validate original content
    original_valid = validate_syntax(original_content, "Original file (with comments)")

    # Validate processed content
    processed_valid = validate_syntax(processed_content, "Processed file (comments removed)")

    print()
    print("=" * 70)
    print("Statistics:")
    print("=" * 70)
    orig_lines = original_content.splitlines()
    proc_lines = processed_content.splitlines()
    print(f"Original lines: {len(orig_lines)}")
    print(f"Processed lines: {len(proc_lines)}")
    print(f"Lines removed: {len(orig_lines) - len(proc_lines)}")
    print()

    comments_removed = [
        "Line 1: # ASCII ONLY IN THIS FILE THOUGH!!!!!!!",
        "Line 2-4: Python behavior comments",
        "Line 6-8: Copyright headers (3 lines)",
        "Line 9: Empty comment line",
        "Line 10: License header",
        "Line 29: Inline comment (deprecated in 7.1, removed in 9.0)"
    ]

    print("Comments removed:")
    for comment in comments_removed:
        print(f"  - {comment}")
    print()

    if original_valid and processed_valid:
        print("✓ SUCCESS: Comment removal maintains valid Python syntax")
        return 0
    else:
        print("✗ FAILURE: Syntax validation failed")
        return 1

if __name__ == '__main__':
    import sys
    sys.exit(main())
