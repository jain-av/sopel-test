# coding=utf-8
"""Tests for sopel.cli.utils"""
from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
from contextlib import contextmanager
import os
from pathlib import Path

import pytest

from sopel import config
from sopel.cli.utils import (
    add_common_arguments,
    enumerate_configs,
    find_config,
    get_many_text,
    green,
    load_settings,
    red,
    yellow,
)


TMP_CONFIG = """
[core]
owner = testnick
nick = TestBot
enable = coretasks
"""


@contextmanager
def cd(newdir):
    prevdir = os.getcwd()
    os.chdir(os.path.expanduser(newdir))
    try:
        yield
    finally:
        os.chdir(prevdir)


@pytest.fixture
def config_dir(tmp_path):
    """Pytest fixture used to generate a temporary configuration directory"""
    test_dir = tmp_path / "config"
    test_dir.mkdir()
    (test_dir / 'config.cfg').write_text('')
    (test_dir / 'extra.ini').write_text('')
    (test_dir / 'module.cfg').write_text('')
    (test_dir / 'README').write_text('')

    return test_dir


@pytest.fixture(autouse=True)
def default_empty_config_env(monkeypatch):
    """Pytest fixture used to ensure dev ENV does not bleed into tests"""
    monkeypatch.delenv("SOPEL_CONFIG", raising=False)
    monkeypatch.delenv("SOPEL_CONFIG_DIR", raising=False)


@pytest.fixture
def env_dir(tmp_path):
    """Pytest fixture used to generate an extra (external) config directory"""
    test_dir = tmp_path / "fromenv"
    test_dir.mkdir()
    (test_dir / 'fromenv.cfg').write_text('')

    return test_dir


def test_green():
    assert green('hello') == '\x1b[32mhello\x1b[0m'
    assert green('hello', reset=False) == '\x1b[32mhello'


def test_red():
    assert red('hello') == '\x1b[31mhello\x1b[0m'
    assert red('hello', reset=False) == '\x1b[31mhello'


def test_yellow():
    assert yellow('hello') == '\x1b[33mhello\x1b[0m'
    assert yellow('hello', reset=False) == '\x1b[33mhello'


def test_enumerate_configs(config_dir):
    """Assert function retrieves only .cfg files by default"""
    results = list(enumerate_configs(str(config_dir)))

    assert 'config.cfg' in results
    assert 'module.cfg' in results
    assert 'extra.ini' not in results
    assert 'README' not in results
    assert len(results) == 2


def test_enumerate_configs_not_a_directory():
    results = list(enumerate_configs('not_a_folder_that_exist'))
    assert results == []


def test_enumerate_configs_extension(config_dir):
    """Assert function retrieves only files with the given extension"""
    results = list(enumerate_configs(str(config_dir), '.ini'))

    assert 'config.cfg' not in results
    assert 'module.cfg' not in results
    assert 'extra.ini' in results
    assert 'README' not in results
    assert len(results) == 1


def test_find_config_local(tmp_path, config_dir):
    """Assert function retrieves configuration file from working dir first"""
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    (working_dir / 'local.cfg').write_text('')

    with cd(str(working_dir)):
        found_config = find_config(str(config_dir), 'local.cfg')
        assert found_config == str(working_dir / 'local.cfg')

        found_config = find_config(str(config_dir), 'local')
        assert found_config == str(config_dir / 'local')


def test_find_config_default(tmp_path, config_dir):
    """Assert function retrieves configuration file from given config dir"""
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    (working_dir / 'local.cfg').write_text('')

    with cd(str(working_dir)):
        found_config = find_config(str(config_dir), 'config')
        assert found_config == str(config_dir / 'config.cfg')

        found_config = find_config(str(config_dir), 'config.cfg')
        assert found_config == str(config_dir / 'config.cfg')


def test_find_config_extension(tmp_path, config_dir):
    """Assert function retrieves configuration file with the given extension"""
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    (working_dir / 'local.cfg').write_text('')

    with cd(str(working_dir)):
        found_config = find_config(
            str(config_dir), 'extra', '.ini')
        assert found_config == str(config_dir / 'extra.ini')


def test_add_common_arguments():
    """Assert function adds the ``-c``/``--config`` and ``--configdir`` options
    """
    parser = argparse.ArgumentParser()
    add_common_arguments(parser)

    options = parser.parse_args([])
    assert hasattr(options, 'config')
    assert hasattr(options, 'configdir')
    assert options.config == 'default'
    assert options.configdir == config.DEFAULT_HOMEDIR

    options = parser.parse_args(['-c', 'test-short'])
    assert options.config == 'test-short'

    options = parser.parse_args(['--config', 'test-long'])
    assert options.config == 'test-long'

    options = parser.parse_args(['--config-dir', 'test-long'])
    assert options.configdir == 'test-long'

    options = parser.parse_args(
        ['-c', 'test-short', '--config-dir', 'test-long-dir'])
    assert options.config == 'test-short'
    assert options.configdir == 'test-long-dir'

    options = parser.parse_args(
        ['--config', 'test-long', '--config-dir', 'test-long-dir'])
    assert options.config == 'test-long'
    assert options.configdir == 'test-long-dir'


def test_add_common_arguments_subparser():
    """Assert function adds the multiple options on a subparser.

    The expected options are ``-c``/``--config`` and ``--config-dir``.
    """
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='action')
    sub = subparsers.add_parser('sub')
    add_common_arguments(sub)

    options = parser.parse_args(['sub'])
    assert hasattr(options, 'config')
    assert hasattr(options, 'configdir')
    assert options.config == 'default'
    assert options.configdir == config.DEFAULT_HOMEDIR

    options = parser.parse_args(['sub', '-c', 'test-short'])
    assert options.config == 'test-short'

    options = parser.parse_args(['sub', '--config', 'test-long'])
    assert options.config == 'test-long'

    options = parser.parse_args(['sub', '--config-dir', 'test-long'])
    assert options.configdir == 'test-long'

    options = parser.parse_args(
        ['sub', '-c', 'test-short', '--config-dir', 'test-long-dir'])
    assert options.config == 'test-short'
    assert options.configdir == 'test-long-dir'

    options = parser.parse_args(
        ['sub', '--config', 'test-long', '--config-dir', 'test-long-dir'])
    assert options.config == 'test-long'
    assert options.configdir == 'test-long-dir'


MANY_TEXTS = (
    ([], ''),
    (['a'], 'the a element'),
    (['a', 'b'], 'elements a and b'),
    (['a', 'b', 'c'], 'elements a, b, and c'),
    (['a', 'b', 'c', 'd'], 'elements a, b, c, and d'),
)


@pytest.mark.parametrize('items, expected', MANY_TEXTS)
def test_get_many_text(items, expected):
    result = get_many_text(
        items,
        'the {item} element',
        'elements {first} and {second}',
        'elements {left}, and {last}')
    assert result == expected


def test_load_settings(config_dir):
    (config_dir / 'config.cfg').write_text(TMP_CONFIG)
    parser = argparse.ArgumentParser()
    add_common_arguments(parser)

    options = parser.parse_args([
        '--config-dir', str(config_dir),
        '-c', 'config.cfg',
    ])

    settings = load_settings(options)
    assert isinstance(settings, config.Config)
    assert settings.basename == 'config'


def test_load_settings_arg_priority_over_env(monkeypatch, config_dir, env_dir):
    monkeypatch.setenv('SOPEL_CONFIG', 'fromenv')
    monkeypatch.setenv('SOPEL_CONFIG_DIR', str(env_dir))

    (config_dir / 'fromenv.cfg').write_text(TMP_CONFIG)
    (config_dir / 'fromarg.cfg').write_text(TMP_CONFIG)
    parser = argparse.ArgumentParser()
    add_common_arguments(parser)

    options = parser.parse_args([
        '--config-dir', str(config_dir),
        '-c', 'fromarg',
    ])

    settings = load_settings(options)
    assert isinstance(settings, config.Config)
    assert settings.basename == 'fromarg'
    assert os.path.dirname(settings.filename) == str(config_dir)


def test_load_settings_default(config_dir):
    (config_dir / 'default.cfg').write_text(TMP_CONFIG)
    parser = argparse.ArgumentParser()
    add_common_arguments(parser)

    options = parser.parse_args(['--config-dir', str(config_dir)])

    settings = load_settings(options)
    assert isinstance(settings, config.Config)


def test_load_settings_default_env_var(monkeypatch, config_dir, env_dir):
    monkeypatch.setenv('SOPEL_CONFIG', 'fromenv')
    monkeypatch.setenv('SOPEL_CONFIG_DIR', str(env_dir))

    (config_dir / 'config.cfg').write_text(TMP_CONFIG)
    (env_dir / 'fromenv.cfg').write_text(TMP_CONFIG)
    parser = argparse.ArgumentParser()
    add_common_arguments(parser)

    options = parser.parse_args([])

    settings = load_settings(options)
    assert isinstance(settings, config.Config)
    assert settings.basename == 'fromenv'
    assert os.path.dirname(settings.filename) == str(env_dir)


def test_load_settings_default_not_found(config_dir):
    parser = argparse.ArgumentParser()
    add_common_arguments(parser)

    options = parser.parse_args(['--config-dir', str(config_dir)])

    with pytest.raises(config.ConfigurationNotFound):
        load_settings(options)


def test_load_settings_invalid(config_dir):
    parser = argparse.ArgumentParser()
    add_common_arguments(parser)

    options = parser.parse_args([
        '--config-dir', str(config_dir),
        '-c', 'config.cfg',
    ])

    (config_dir / 'config.cfg').write_text('')  # Ensure file exists but is empty
    with pytest.raises(ValueError):  # no [core] section
        load_settings(options)
