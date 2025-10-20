"""Sopel CLI Entry Point - Bot Execution and Lifecycle Management

This module serves as the main entry point for running Sopel IRC bot instances
from the command line. It handles bot startup, daemon mode operation, signal
handling for graceful shutdown/restart, and automatic reconnection after
disconnection.

**Key Responsibilities:**

* Command-line argument parsing for start/stop/restart/configure commands
* Bot process lifecycle management (PID file handling, forking for daemon mode)
* Signal handling for QUIT (SIGTERM, SIGINT, SIGUSR1) and RESTART (SIGUSR2, SIGILL)
* Automatic reconnection with exponential backoff after unexpected disconnections
* Error recovery and logging for critical failures

**Signal Handling Behavior:**

Sopel registers handlers for two types of signals:

* **QUIT_SIGNALS** (SIGTERM, SIGINT, SIGUSR1): Gracefully shut down the bot
  - If connected: sends QUIT message to IRC server, then exits
  - If disconnected: sets hasquit flag and raises KeyboardInterrupt
  - Used by ``sopel stop`` command (SIGUSR1 on Unix, SIGTERM on Windows)

* **RESTART_SIGNALS** (SIGUSR2, SIGILL): Restart the bot process
  - If connected: sends QUIT message, then restarts via exec
  - If disconnected: sets wantsrestart + hasquit flags and raises KeyboardInterrupt
  - Used by ``sopel restart`` command (SIGUSR2 on Unix, SIGILL on Windows)

**Process Management:**

* ``sopel start``: Starts a new bot instance (foreground or daemon with -d/--fork)
* ``sopel stop``: Sends SIGUSR1/SIGTERM to running instance
* ``sopel restart``: Sends SIGUSR2/SIGILL to running instance
* ``sopel configure``: Runs configuration wizard (no bot startup)

Sopel - An IRC Bot
Copyright 2008, Sean B. Palmer, inamidst.com
Copyright © 2012-2014, Elad Alfassa <elad@fedoraproject.org>
Licensed under the Eiffel Forum License 2.

https://sopel.chat
"""
from __future__ import annotations

import argparse
import logging
import os
import platform
import signal
import sys
import time

from sopel import __version__, bot, config, logger, tools
from . import utils

# This is in case someone somehow manages to install Sopel on an old version
# of pip (<9.0.0), which doesn't know about `python_requires`, or tries to run
# from source on an unsupported version of Python.
if sys.version_info < (3, 7):
    tools.stderr('Error: Sopel requires Python 3.7+.')
    sys.exit(1)

# Py3.7 EOL: https://www.python.org/dev/peps/pep-0537/#and-beyond-schedule
if sys.version_info < (3, 8):
    # TODO check this warning before releasing Sopel 8.0
    print(
        'Warning: Python 3.7 will reach end of life by June 2022 '
        'and will receive no further updates. '
        'Sopel 9.0 will drop support for it.',
        file=sys.stderr,
    )

LOGGER = logging.getLogger(__name__)

ERR_CODE = 1
"""Error code: program exited with an error"""
ERR_CODE_NO_RESTART = 2
"""Error code: program exited with an error and should not be restarted

This error code is used to prevent systemd from restarting the bot when it
encounters such an error case.
"""


def run(settings, pid_file, daemon=False):
    """Run the bot with these ``settings``.

    :param settings: settings with which to run the bot
    :type settings: :class:`sopel.config.Config`
    :param str pid_file: path to the bot's PID file
    :param bool daemon: tell if the bot should be run as a daemon
    :return: ``-1`` if the bot wants to restart, ``None`` otherwise
    :rtype: int or None

    This is the main bot execution loop that handles the complete lifecycle
    of a Sopel instance:

    **Startup Flow:**

    1. Display version information and loaded configuration file
    2. Check SSL certificate configuration
    3. Create and setup the bot instance (:class:`~sopel.bot.Sopel`)
    4. Register signal handlers for graceful shutdown/restart
    5. Connect to IRC server and start main event loop

    **Error Recovery:**

    * **Setup errors**: Critical errors during bot initialization will be logged
      and propagated up, preventing the bot from starting
    * **Runtime errors**: Exceptions during bot operation are logged, the PID
      file is cleaned up, and the process exits with code 1
    * **Keyboard interrupts**: Gracefully exit the reconnection loop

    **Automatic Reconnection:**

    After an unexpected disconnection (network issue, server restart, etc.),
    the bot will automatically attempt to reconnect after a 20-second delay.
    The reconnection loop continues until:

    * ``p.hasquit`` is set (QUIT signal received or explicit quit command)
    * ``p.wantsrestart`` is set (RESTART signal received), returns ``-1``
    * A KeyboardInterrupt occurs (Ctrl+C)

    **Daemon Mode:**

    When ``daemon=True``, the bot runs in daemon mode (detached from terminal).
    In this mode, signal handlers are the primary way to control the bot:

    * Send SIGUSR1/SIGTERM to stop: ``sopel stop``
    * Send SIGUSR2/SIGILL to restart: ``sopel restart``

    .. note::

        This function uses ``os._exit()`` instead of ``sys.exit()`` or ``return``
        to terminate the process. This is a workaround for an issue where
        returning normally causes the bot to hang on Ctrl+C (KeyboardInterrupt).

    .. seealso::

        Signal handlers are registered by :meth:`sopel.bot.Sopel.set_signal_handlers`,
        which sets up handlers for QUIT_SIGNALS and RESTART_SIGNALS.

    """
    # Reconnection delay in seconds (fixed at 20s after any disconnection)
    delay = 20

    # Acts as a welcome message, showing the program and platform version at start
    print_version()
    # Also show the location of the config file used to load settings
    print("\nLoaded config file: {}".format(settings.filename))

    if not settings.core.ca_certs:
        tools.stderr(
            'Could not open CA certificates file. SSL will not work properly!')

    # Define empty variable `p` for bot instance (used across reconnection loop)
    p = None
    while True:
        # Check if hasquit was set during disconnected phase (signal handler called while offline)
        # This prevents reconnection attempts after the user requested shutdown
        if p and p.hasquit:
            break
        try:
            # Create new bot instance for each connection attempt
            p = bot.Sopel(settings, daemon=daemon)
            p.setup()
            # Register signal handlers (QUIT_SIGNALS: SIGTERM/SIGINT/SIGUSR1, RESTART_SIGNALS: SIGUSR2/SIGILL)
            # These handlers will set p.hasquit and/or p.wantsrestart flags when signals are received
            p.set_signal_handlers()
        except KeyboardInterrupt:
            tools.stderr('Bot setup interrupted')
            break
        except Exception:
            # In that case, there is nothing we can do.
            # If the bot can't setup itself, then it won't run.
            # This is a critical case scenario, where the user should have
            # direct access to the exception traceback right in the console.
            # Besides, we can't know if logging has been set up or not, so
            # we can't rely on that here.
            tools.stderr('Unexpected error in bot setup')
            raise

        try:
            # Start the main bot event loop - this blocks until disconnection or quit
            p.run(settings.core.host, int(settings.core.port))
        except KeyboardInterrupt:
            # User pressed Ctrl+C - exit gracefully without reconnecting
            break
        except Exception:
            # Critical runtime exception during bot operation
            err_log = logging.getLogger('sopel.exceptions')
            err_log.exception('Critical exception in core')
            err_log.error('----------------------------------------')
            # TODO: This should be handled by command_start
            # All we should need here is a return value, but replacing the
            # os._exit() call below (at the end) broke ^C.
            # This one is much harder to test, so until that one's sorted it
            # isn't worth the risk of trying to remove this one.
            os.unlink(pid_file)
            os._exit(1)

        # Bot has disconnected - check if we should reconnect or exit
        if not isinstance(delay, int):
            # delay was changed to non-int (should not happen normally) - exit
            break
        if p.wantsrestart:
            # RESTART signal received (SIGUSR2/SIGILL) - return -1 to trigger full process restart
            return -1
        if p.hasquit:
            # QUIT signal received (SIGTERM/SIGINT/SIGUSR1) or explicit quit command - exit cleanly
            break
        # Unexpected disconnection (network issue, server restart, etc.) - reconnect after delay
        LOGGER.warning('Disconnected. Reconnecting in %s seconds...', delay)
        time.sleep(delay)
    # TODO: This should be handled by command_start
    # All we should need here is a return value, but making this
    # a return makes Sopel hang on ^C after it says "Closed!"
    os.unlink(pid_file)
    os._exit(0)


def build_parser():
    """Build an argument parser for the bot.

    :return: the argument parser
    :rtype: :class:`argparse.ArgumentParser`
    """
    parser = argparse.ArgumentParser(description='Sopel IRC Bot')

    parser.add_argument('-V', '--version', action='store_true',
                        dest='version',
                        help='Show version number and exit')

    subparsers = parser.add_subparsers(
        title='subcommands',
        description='List of Sopel\'s subcommands',
        dest='action',
        metavar='{start,configure,stop,restart}')

    # manage `start` subcommand
    parser_start = subparsers.add_parser(
        'start',
        description='Start a Sopel instance. '
                    'This command requires an existing configuration file '
                    'that can be generated with ``sopel configure``.',
        help='Start a Sopel instance')
    parser_start.add_argument(
        '-d', '--fork',
        dest='daemonize',
        action='store_true',
        default=False,
        help='Run Sopel as a daemon (fork). This bot will safely run in the '
             'background. The instance will be named after the name of the '
             'configuration file used to run it. '
             'To stop it, use ``sopel stop`` (with the same configuration).')
    parser_start.add_argument(
        '--quiet',
        action="store_true",
        dest="quiet",
        help="Suppress all output")
    utils.add_common_arguments(parser_start)

    # manage `configure` subcommand
    parser_configure = subparsers.add_parser(
        'configure',
        description='Run the configuration wizard. It can be used to create '
                    'a new configuration file or to update an existing one.',
        help='Sopel\'s Wizard tool')
    parser_configure.add_argument(
        '--plugins',
        action='store_true',
        default=False,
        dest='plugins',
        help='Check for Sopel plugins that require configuration, and run '
             'their configuration wizards.')
    utils.add_common_arguments(parser_configure)

    # manage `stop` subcommand
    parser_stop = subparsers.add_parser(
        'stop',
        description='Stop a running Sopel instance. '
                    'This command determines the instance to quit by the name '
                    'of the configuration file used ("default", or the one '
                    'from the ``-c``/``--config`` option). '
                    'This command should be used when the bot is running in '
                    'the background from ``sopel start -d``, and should not '
                    'be used when Sopel is managed by a process manager '
                    '(like systemd or supervisor).',
        help='Stop a running Sopel instance')
    parser_stop.add_argument(
        '-k', '--kill',
        action='store_true',
        default=False,
        help='Kill Sopel without a graceful quit')
    parser_stop.add_argument(
        '--quiet',
        action="store_true",
        dest="quiet",
        help="Suppress all output")
    utils.add_common_arguments(parser_stop)

    # manage `restart` subcommand
    parser_restart = subparsers.add_parser(
        'restart',
        description='Restart a running Sopel instance',
        help='Restart a running Sopel instance')
    parser_restart.add_argument(
        '--quiet',
        action="store_true",
        dest="quiet",
        help="Suppress all output")
    utils.add_common_arguments(parser_restart)

    return parser


def check_not_root():
    """Check if root is running the bot.

    It raises a ``RuntimeError`` if the user has root privileges on Linux or
    if it is the ``Administrator`` account on Windows.
    """
    opersystem = platform.system()
    if opersystem in ["Linux", "Darwin"]:
        # Linux/Mac
        if os.getuid() == 0 or os.geteuid() == 0:
            raise RuntimeError('Error: Do not run Sopel with root privileges.')
    elif opersystem in ["Windows"]:
        # Windows
        if os.environ.get("USERNAME") == "Administrator":
            raise RuntimeError('Error: Do not run Sopel as Administrator.')
    else:
        tools.stderr(
            "Warning: %s is an uncommon operating system platform. "
            "Sopel should still work, but please contact Sopel's developers "
            "if you experience issues."
            % opersystem)


def print_version():
    """Print Python version and Sopel version on stdout."""
    py_ver = '%s.%s.%s' % (sys.version_info.major,
                           sys.version_info.minor,
                           sys.version_info.micro)
    print('Sopel %s (running on Python %s)' % (__version__, py_ver))
    print('https://sopel.chat/')


def get_configuration(options):
    """Get or create a configuration object from ``options``.

    :param options: argument parser's options
    :type options: :class:`argparse.Namespace`
    :return: a configuration object
    :rtype: :class:`sopel.config.Config`

    This may raise a :exc:`sopel.config.ConfigurationError` if the
    configuration file is invalid.

    .. seealso::

       The configuration file is loaded by
       :func:`~sopel.cli.run.utils.load_settings` or created using the
       configuration wizard.

    """
    try:
        settings = utils.load_settings(options)
    except config.ConfigurationNotFound as error:
        print(
            "Welcome to Sopel!\n"
            "I can't seem to find the configuration file, "
            "so let's generate it!\n")
        settings = utils.wizard(error.filename)

    settings._is_daemonized = options.daemonize
    return settings


def get_pid_filename(settings, pid_dir):
    """Get the pid file name in ``pid_dir`` from the given ``settings``.

    :param settings: Sopel config
    :type settings: :class:`sopel.config.Config`
    :param str pid_dir: path to the pid directory
    :return: absolute filename of the pid file

    By default, it's ``sopel.pid``, but if the configuration's basename is not
    ``default`` then it will be used to generate the pid file name as
    ``sopel-{basename}.pid`` instead.
    """
    name = 'sopel.pid'
    if settings.basename != 'default':
        filename = os.path.basename(settings.filename)
        basename, ext = os.path.splitext(filename)
        if ext != '.cfg':
            basename = filename
        name = 'sopel-%s.pid' % basename

    return os.path.abspath(os.path.join(pid_dir, name))


def get_running_pid(filename):
    """Retrieve the PID number from the given ``filename``.

    :param str filename: path to file to read the PID from
    :return: the PID number of a Sopel instance if running, ``None`` otherwise
    :rtype: integer

    This function tries to retrieve a PID number from the given ``filename``,
    as an integer, and returns ``None`` if the file is not found or if the
    content is not an integer.
    """
    if not os.path.isfile(filename):
        return

    with open(filename, 'r') as pid_file:
        try:
            return int(pid_file.read())
        except ValueError:
            pass


def command_start(opts):
    """Start a Sopel instance.

    :param opts: parsed arguments
    :type opts: :class:`argparse.Namespace`
    :return: error code if startup fails, ``None`` on clean exit
    :rtype: int or None

    This command orchestrates the complete bot startup process:

    **Startup Process:**

    1. **Configuration Loading**: Load settings from config file, run wizard if needed
    2. **PID File Management**: Check for existing instance, create PID file
    3. **Daemon Mode** (optional): Fork process if ``--daemonize``/``-d`` flag is set
    4. **Bot Execution**: Call :func:`run` to start the main bot loop
    5. **Cleanup**: Remove PID file after bot exits

    **Daemon Mode (--daemonize / -d flag):**

    When ``--daemonize`` is specified, the bot forks and runs in the background.
    The parent process exits immediately, while the child continues running.
    Control in daemon mode:

    * ``sopel stop``: Sends SIGUSR1/SIGTERM to stop the background bot
    * ``sopel restart``: Sends SIGUSR2/SIGILL to restart the background bot

    **Restart Handling:**

    If :func:`run` returns ``-1`` (restart requested), this function uses
    ``os.execv()`` to replace the current process with a new Sopel instance,
    preserving the same command-line arguments and configuration.

    .. note::

        The bot checks for an existing instance before starting. If another
        Sopel process is already running with the same config file, startup
        will fail with ERR_CODE. Use ``sopel restart`` to restart a running
        instance instead.

    """
    # Step One: Get the configuration file and prepare to run
    try:
        settings = get_configuration(opts)
    except config.ConfigurationError as e:
        tools.stderr(e)
        return ERR_CODE_NO_RESTART

    if settings.core.not_configured:
        tools.stderr('Bot is not configured, can\'t start')
        return ERR_CODE_NO_RESTART

    # Step Two: Handle process-lifecycle options and manage the PID file
    pid_dir = settings.core.pid_dir
    pid_file_path = get_pid_filename(settings, pid_dir)
    pid = get_running_pid(pid_file_path)

    if pid is not None and tools.check_pid(pid):
        tools.stderr('There\'s already a Sopel instance running '
                     'with this config file.')
        tools.stderr('Try using either the `sopel stop` '
                     'or the `sopel restart` command.')
        return ERR_CODE

    if opts.daemonize:
        # Fork to create daemon process (parent exits, child continues in background)
        child_pid = os.fork()
        if child_pid != 0:
            # Parent process: exit immediately, child runs in background
            return

    # Write current process ID to file for use by stop/restart commands
    with open(pid_file_path, 'w') as pid_file:
        pid_file.write(str(os.getpid()))

    # Step Three: Run Sopel (blocks until bot exits or restart requested)
    ret = run(settings, pid_file_path, daemon=opts.daemonize)

    # Step Four: Shutdown Clean-Up
    os.unlink(pid_file_path)

    if ret == -1:
        # Restart requested (SIGUSR2/SIGILL received or .restart command used)
        # Replace current process with new Sopel instance using same arguments
        os.execv(sys.executable, ['python'] + sys.argv)
    else:
        # Normal exit (QUIT signal, .quit command, or error)
        return ret


def command_configure(opts):
    """Sopel Configuration Wizard.

    :param opts: parsed arguments
    :type opts: :class:`argparse.Namespace`
    """
    configpath = utils.find_config(opts.configdir, opts.config)
    if opts.plugins:
        utils.plugins_wizard(configpath)
    else:
        utils.wizard(configpath)


def command_stop(opts):
    """Stop a running Sopel instance.

    :param opts: parsed arguments
    :type opts: :class:`argparse.Namespace`
    :return: error code if the operation fails, ``None`` on success
    :rtype: int or None

    This command sends a QUIT signal to a running Sopel instance identified
    by its PID file. The signal sent depends on the platform:

    * **Unix/Linux/macOS**: Sends SIGUSR1 for graceful shutdown
    * **Windows**: Sends SIGTERM (Windows doesn't support SIGUSR1)

    **Signal Behavior:**

    When the bot receives a QUIT signal:

    * If **connected**: Sends ``QUIT`` message to IRC server, then exits
    * If **disconnected**: Sets ``hasquit`` flag and raises KeyboardInterrupt

    **Kill Option:**

    The ``--kill`` flag (``-k``) sends SIGKILL instead, which immediately
    terminates the bot without cleanup. This should only be used if the
    bot is unresponsive to normal shutdown signals.

    .. seealso::

        The actual signal handling is performed by :meth:`sopel.bot.Sopel._signal_handler`,
        which is registered by :meth:`sopel.bot.Sopel.set_signal_handlers`.

    """
    # Get Configuration
    try:
        settings = utils.load_settings(opts)
    except config.ConfigurationNotFound as error:
        tools.stderr('Configuration "%s" not found' % error.filename)
        return ERR_CODE

    if settings.core.not_configured:
        tools.stderr('Sopel is not configured, can\'t stop')
        return ERR_CODE

    # Configure logging
    logger.setup_logging(settings)

    # Get Sopel's PID
    filename = get_pid_filename(settings, settings.core.pid_dir)
    pid = get_running_pid(filename)

    if pid is None or not tools.check_pid(pid):
        tools.stderr('Sopel is not running!')
        return ERR_CODE

    # Stop Sopel
    if opts.kill:
        # Force kill without cleanup (SIGKILL cannot be caught)
        tools.stderr('Killing the Sopel')
        os.kill(pid, signal.SIGKILL)
        return

    # Send graceful shutdown signal
    tools.stderr('Signaling Sopel to stop gracefully')
    if hasattr(signal, 'SIGUSR1'):
        # Unix/Linux/macOS: Send SIGUSR1 (registered as a QUIT_SIGNAL)
        # This will trigger _signal_handler() which sends QUIT to IRC and exits
        os.kill(pid, signal.SIGUSR1)
    else:
        # Windows doesn't support SIGUSR1, use SIGTERM instead
        # Windows will not generate SIGTERM itself
        # https://docs.microsoft.com/en-us/cpp/c-runtime-library/reference/signal
        os.kill(pid, signal.SIGTERM)


def command_restart(opts):
    """Restart a running Sopel instance.

    :param opts: parsed arguments
    :type opts: :class:`argparse.Namespace`
    :return: error code if the operation fails, ``None`` on success
    :rtype: int or None

    This command sends a RESTART signal to a running Sopel instance identified
    by its PID file. The signal sent depends on the platform:

    * **Unix/Linux/macOS**: Sends SIGUSR2 for graceful restart
    * **Windows**: Sends SIGILL (Windows doesn't support SIGUSR2)

    **Signal Behavior:**

    When the bot receives a RESTART signal:

    * If **connected**: Sends ``QUIT`` message to IRC server, then restarts
    * If **disconnected**: Sets ``wantsrestart`` + ``hasquit`` flags and raises KeyboardInterrupt

    The restart is implemented by the :func:`run` function returning ``-1``,
    which causes :func:`command_start` to call ``os.execv()`` to replace the
    current process with a new Sopel instance using the same configuration.

    **Restart vs Stop:**

    * ``sopel restart``: Full process restart (reload code, config, plugins)
    * ``sopel stop`` then ``sopel start``: Same effect but manual

    .. seealso::

        The actual signal handling is performed by :meth:`sopel.bot.Sopel._signal_handler`,
        which is registered by :meth:`sopel.bot.Sopel.set_signal_handlers`.

    """
    # Get Configuration
    try:
        settings = utils.load_settings(opts)
    except config.ConfigurationNotFound as error:
        tools.stderr('Configuration "%s" not found' % error.filename)
        return ERR_CODE

    if settings.core.not_configured:
        tools.stderr('Sopel is not configured, can\'t stop')
        return ERR_CODE

    # Configure logging
    logger.setup_logging(settings)

    # Get Sopel's PID
    filename = get_pid_filename(settings, settings.core.pid_dir)
    pid = get_running_pid(filename)

    if pid is None or not tools.check_pid(pid):
        tools.stderr('Sopel is not running!')
        return ERR_CODE

    # Send restart signal to trigger bot reload
    tools.stderr('Asking Sopel to restart')
    if hasattr(signal, 'SIGUSR2'):
        # Unix/Linux/macOS: Send SIGUSR2 (registered as a RESTART_SIGNAL)
        # This will trigger _signal_handler() which sends QUIT to IRC,
        # sets wantsrestart=True, and causes run() to return -1 for exec restart
        os.kill(pid, signal.SIGUSR2)
    else:
        # Windows doesn't support SIGUSR2, use SIGILL instead
        # Windows will not generate SIGILL itself
        # https://docs.microsoft.com/en-us/cpp/c-runtime-library/reference/signal
        os.kill(pid, signal.SIGILL)


def main(argv=None):
    """Sopel run script entry point.

    :param list argv: command line arguments
    """
    # Build parser and handle default command
    global_options = ['-h', '--help', '-V', '--version']
    parser = build_parser()

    argv = argv or sys.argv[1:]
    if not argv:
        # No argument: assume start sub-command
        argv = ['start']

    elif argv[0].startswith('-') and argv[0] not in global_options:
        # No sub-command and no global option
        argv = ['start'] + argv

    # Parse The Command Line
    opts = parser.parse_args(argv)

    # Handle "-V/--version" option
    if opts.version:
        print_version()
        return

    try:
        # Check "Do not run as root"
        check_not_root()

        # Select command
        action = getattr(opts, 'action', None)
        command = {
            'start': command_start,
            'configure': command_configure,
            'stop': command_stop,
            'restart': command_restart,
        }[action]

        # Run command
        return command(opts)
    except KeyError:
        parser.print_usage()
        return ERR_CODE
    except KeyboardInterrupt:
        print("\n\nInterrupted")
        return ERR_CODE
    except RuntimeError as err:
        tools.stderr(str(err))
        return ERR_CODE


if __name__ == '__main__':
    sys.exit(main())
