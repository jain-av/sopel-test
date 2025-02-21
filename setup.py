#!/usr/bin/env python
# coding=utf-8
from __future__ import absolute_import, division, print_function, unicode_literals

import sys

try:
    from setuptools import setup, __version__ as setuptools_version
except ImportError:
    print(
        'You do not have setuptools, and can not install Sopel. The easiest '
        'way to fix this is to install pip by following the instructions at '
        'https://pip.readthedocs.io/en/latest/installing/\n'
        'Alternately, you can run Sopel without installing it by running '
        '"python sopel.py"',
        file=sys.stderr,
    )
    sys.exit(1)
else:
    version_info = setuptools_version.split('.')
    major = int(version_info[0])
    minor = int(version_info[1])

    if major < 30 or (major == 30 and minor < 3):
        print(
            'Your version of setuptools is outdated: version 30.3 or above '
            'is required to install Sopel. You can do that with '
            '"pip install -U setuptools"\n'
            'Alternately, you can run Sopel without installing it by running '
            '"python sopel.py"',
            file=sys.stderr,
        )
        sys.exit(1)

# We check Python's version ourselves in case someone installed Sopel on an
# old version of pip (<9.0.0), which doesn't know about `python_requires`.
if sys.version_info < (3, 7):
    # Maybe not the best way to do this, but this question is tiring.
    raise ImportError('Sopel requires Python 3.7+.')


def read_reqs(path):
    with open(path, 'r') as fil:
        return list(fil.readlines())


requires = read_reqs('requirements.txt')
dev_requires = requires + read_reqs('dev-requirements.txt')

setup(
    install_requires=requires, extras_require={"dev": dev_requires},
)
