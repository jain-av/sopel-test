"""Utility functions to manage plugin callables from a Python module.

.. important::

    Its usage and documentation is for Sopel core development and advanced
    developers. It is subject to rapid changes between versions without much
    (or any) warning.

    Do **not** build your plugin based on what is here, you do **not** need to.

"""
from __future__ import annotations

import inspect
import logging
import re

from sopel.config.core_section import COMMAND_DEFAULT_HELP_PREFIX


LOGGER = logging.getLogger(__name__)


def clean_callable(func, config):
    """Normalize and prepare a plugin callable for use by the bot.

    :param func: the callable to clean
    :type func: callable
    :param config: Sopel's settings
    :type config: :class:`sopel.config.Config`

    This function introspects a callable (typically a plugin function decorated
    with :mod:`sopel.plugin` decorators) and sets default values for all
    attributes that Sopel expects during plugin execution. It performs three
    main tasks:

    1. **Attribute Introspection**: Examines the callable for decorator-set
       attributes like ``commands``, ``rule``, ``rate``, ``thread``, etc.
    2. **Default Value Assignment**: Sets sensible defaults for any attributes
       not explicitly configured via decorators (e.g., ``thread=True``,
       ``priority='medium'``).
    3. **Documentation Processing**: Extracts docstrings and examples,
       formatting them with the bot's configured nickname and help prefix.

    Attributes set on the callable:

    **Threading and Execution Control:**
        - ``thread`` (bool): Whether to run in a separate thread (default: True)
        - ``priority`` (str): Execution priority - 'high', 'medium', or 'low'
          (default: 'medium')
        - ``echo`` (bool): Whether to receive bot's own messages (default: False)

    **Rate Limiting** (only for limitable callables):
        - ``rate`` (int): Per-user rate limit in seconds (default: 0 = no limit)
        - ``channel_rate`` (int): Per-channel rate limit (default: 0)
        - ``global_rate`` (int): Server-wide rate limit (default: 0)
        - ``unblockable`` (bool): Exempt from ignore/block system (default: False)

    **IRC Event Handling:**
        - ``event`` (list): IRC events to respond to (default: ['PRIVMSG'])
        - ``output_prefix`` (str): Prefix for output messages (default: '')

    **Documentation:**
        - ``_docs`` (dict): Maps command names to (docstring, examples) tuples

    **Pattern Matching** (when present):
        - ``intents`` (list): Compiled regex patterns for intent matching

    The loader only adds attributes relevant to the callable's type. For
    example, job functions (decorated with :func:`~sopel.plugin.interval`)
    won't receive command-related attributes, and non-limitable callables
    won't get rate limit attributes.

    Example::

        # Before clean_callable() - decorated function with minimal attributes
        @plugin.command('hello')
        @plugin.rate(user=5)
        def hello_cmd(bot, trigger):
            '''Say hello to the user.'''
            bot.say('Hello!')

        # After clean_callable() - attributes normalized and defaults set:
        # hello_cmd.commands = ['hello']
        # hello_cmd.rate = 5
        # hello_cmd.channel_rate = 0  # default added
        # hello_cmd.global_rate = 0   # default added
        # hello_cmd.thread = True     # default added
        # hello_cmd.priority = 'medium'  # default added
        # hello_cmd.echo = False      # default added
        # hello_cmd.event = ['PRIVMSG']  # default added
        # hello_cmd._docs = {'hello': (['Say hello to the user.'], [])}
        # hello_cmd._sopel_callable = True

    .. seealso::

        The decorators in :mod:`sopel.plugin` that set the attributes this
        function introspects: :func:`~sopel.plugin.command`,
        :func:`~sopel.plugin.rule`, :func:`~sopel.plugin.rate`,
        :func:`~sopel.plugin.thread`, :func:`~sopel.plugin.priority`, etc.

    """
    nick = config.core.nick
    help_prefix = config.core.help_prefix
    # Initialize documentation storage for help system
    func._docs = {}
    doc = []
    examples = []

    # Extract and parse docstring for help text
    docstring = inspect.getdoc(func)
    if docstring:
        doc = docstring.splitlines()

    # Default to threaded execution unless explicitly disabled by @plugin.thread(False)
    func.thread = getattr(func, 'thread', True)

    if is_limitable(func):
        # Only set rate limiting attributes for callables that will pass through
        # the rate-limiting machinery (rules, commands, URL callbacks, etc.).
        # Job functions (decorated with @plugin.interval) don't need these.
        func.rate = getattr(func, 'rate', 0)  # per-user rate limit (0 = no limit)
        func.channel_rate = getattr(func, 'channel_rate', 0)  # per-channel rate limit
        func.global_rate = getattr(func, 'global_rate', 0)  # server-wide rate limit
        func.unblockable = getattr(func, 'unblockable', False)  # bypass ignore/block system

    if not is_triggerable(func) and not is_url_callback(func):
        # Job functions (interval-based) don't need the trigger-related attributes
        # below (event, priority, echo, docs, etc.). Return early to avoid memory
        # waste and potential confusion.
        return

    # Set defaults for triggerable and URL callback functions
    func.echo = getattr(func, 'echo', False)  # receive bot's own messages
    func.priority = getattr(func, 'priority', 'medium')  # execution priority
    func.output_prefix = getattr(func, 'output_prefix', '')  # prefix for bot.say()

    # IRC event types this callable responds to (default to PRIVMSG for normal messages)
    if not hasattr(func, 'event'):
        func.event = ['PRIVMSG']
    else:
        # Normalize event names to uppercase (IRC protocol standard)
        func.event = [event.upper() for event in func.event]

    # Process documentation for command-based callables (commands, nickname_commands, action_commands)
    if any(hasattr(func, attr) for attr in ['commands', 'nickname_commands', 'action_commands']):
        if hasattr(func, 'example'):
            # Extract user-facing examples (marked with help=True in @plugin.example).
            # If no examples are marked for help display, fall back to showing the first
            # example for backward compatibility with Sopel < 7.0 behavior.
            examples = [rec["example"] for rec in func.example if rec["help"]] or [func.example[0]["example"]]
            # Format examples with bot's actual nickname and configured help prefix
            for i, example in enumerate(examples):
                # Replace placeholder nickname with actual bot nickname
                example = example.replace('$nickname', nick)
                # Replace default help prefix (.) with configured prefix if needed
                if example[0] != help_prefix and not example.startswith(nick):
                    example = example.replace(
                        COMMAND_DEFAULT_HELP_PREFIX, help_prefix, 1)
                examples[i] = example
        # Build documentation mapping: command name -> (docstring lines, examples)
        if doc or examples:
            cmds = []
            cmds.extend(getattr(func, 'commands', []))
            cmds.extend(getattr(func, 'nickname_commands', []))
            # Map each command to its documentation for the help system
            for command in cmds:
                func._docs[command] = (doc, examples)

    # Compile intent patterns into regex objects for efficient matching
    if hasattr(func, 'intents'):
        # Get the regex pattern type (implementation may vary across Python versions)
        _regex_type = type(re.compile(''))
        # Ensure all intents are compiled regex objects for consistent matching behavior.
        # If an intent is already compiled, keep it; otherwise compile as case-insensitive.
        func.intents = [
            (intent
                if isinstance(intent, _regex_type)
                else re.compile(intent, re.IGNORECASE))
            for intent in func.intents
        ]


def is_limitable(obj):
    """Check if ``obj`` needs to carry attributes related to limits.

    :param obj: any :term:`function` to check
    :return: ``True`` if ``obj`` must have limit-related attributes

    Limitable callables aren't necessarily triggerable directly, but they all
    must pass through Sopel's rate-limiting machinery during dispatching.
    Therefore, they must have the attributes checked by that machinery.

    This includes:
        - Rule-based triggers (``@plugin.rule``, ``@plugin.find``, ``@plugin.search``)
        - Commands (``@plugin.command``, ``@plugin.nickname_command``, ``@plugin.action_command``)
        - Event handlers (``@plugin.event``)
        - Intent matchers (custom intent patterns)
        - URL callbacks (``@plugin.url``)

    But excludes:
        - Job functions (``@plugin.interval``) which run on timers, not in response
          to IRC messages, so rate limiting doesn't apply

    .. seealso::

        The :func:`clean_callable` function uses this check to determine whether
        to set ``rate``, ``channel_rate``, ``global_rate``, and ``unblockable``
        attributes.

    """
    # Job functions don't go through rate limiting (they're time-based, not message-based)
    forbidden_attrs = (
        'interval',
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    # Callables with any of these attributes will be dispatched through the rate limiter
    allowed_attrs = (
        'rule',                      # @plugin.rule - regex pattern matching
        'rule_lazy_loaders',         # @plugin.rule_lazy - lazy-loaded regex patterns
        'find_rules',                # @plugin.find - find pattern in message
        'find_rules_lazy_loaders',   # @plugin.find_lazy - lazy-loaded find patterns
        'search_rules',              # @plugin.search - search pattern in message
        'search_rules_lazy_loaders', # @plugin.search_lazy - lazy-loaded search patterns
        'event',                     # @plugin.event - IRC event handlers
        'intents',                   # Intent-based matching
        'commands',                  # @plugin.command - prefix commands
        'nickname_commands',         # @plugin.nickname_command - nickname-prefixed commands
        'action_commands',           # @plugin.action_command - CTCP ACTION commands
        'url_regex',                 # @plugin.url - URL pattern matching
        'url_lazy_loaders',          # @plugin.url_lazy - lazy-loaded URL patterns
    )
    allowed = any(hasattr(obj, attr) for attr in allowed_attrs)

    return allowed and not forbidden


def is_triggerable(obj):
    """Check if ``obj`` can handle the bot's triggers.

    :param obj: any :term:`function` to check
    :return: ``True`` if ``obj`` can handle the bot's triggers

    A triggerable is a callable that will be used by the bot to handle a
    particular trigger (i.e. an IRC message): it can be a regex rule, an
    event, an intent, a command, a nickname command, or an action command.
    However, it must not be a job or a URL callback.

    .. seealso::

        Many of the decorators defined in :mod:`sopel.plugin` make the
        decorated function a triggerable object.

    """
    forbidden_attrs = (
        'interval',
        'url_regex',
        'url_lazy_loaders',
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    allowed_attrs = (
        'rule',
        'rule_lazy_loaders',
        'find_rules',
        'find_rules_lazy_loaders',
        'search_rules',
        'search_rules_lazy_loaders',
        'event',
        'intents',
        'commands',
        'nickname_commands',
        'action_commands',
    )
    allowed = any(hasattr(obj, attr) for attr in allowed_attrs)

    return allowed and not forbidden


def is_url_callback(obj):
    """Check if ``obj`` can handle a URL callback.

    :param obj: any :term:`function` to check
    :return: ``True`` if ``obj`` can handle a URL callback

    A URL callback handler is a callable that will be used by the bot to
    handle a particular URL in an IRC message.

    .. seealso::

        Both :func:`sopel.plugin.url` :func:`sopel.plugin.url_lazy` make the
        decorated function a URL callback handler.

    """
    forbidden_attrs = (
        'interval',
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    allowed_attrs = (
        'url_regex',
        'url_lazy_loaders',
    )
    allowed = any(hasattr(obj, attr) for attr in allowed_attrs)

    return allowed and not forbidden


def clean_module(module, config):
    """Clean a module and return its command, rule, job, etc. callables.

    :param module: the module to clean
    :type module: :term:`module`
    :param config: Sopel's settings
    :type config: :class:`sopel.config.Config`
    :return: a tuple with triggerable, job, shutdown, and url functions
    :rtype: tuple

    This function will parse the ``module`` looking for callables:

    * shutdown actions
    * triggerables (commands, rules, etc.)
    * jobs
    * URL callbacks

    This function will set all the default attributes expected for a Sopel
    callable, i.e. properties related to threading, docs, examples, rate
    limiting, commands, rules, and other features.
    """
    callables = []
    shutdowns = []
    jobs = []
    urls = []
    for obj in vars(module).values():
        if callable(obj):
            is_sopel_callable = getattr(obj, '_sopel_callable', False) is True
            if getattr(obj, '__name__', None) == 'shutdown':
                shutdowns.append(obj)
            elif not is_sopel_callable:
                continue
            elif is_triggerable(obj):
                clean_callable(obj, config)
                callables.append(obj)
            elif hasattr(obj, 'interval'):
                clean_callable(obj, config)
                jobs.append(obj)
            elif is_url_callback(obj):
                clean_callable(obj, config)
                urls.append(obj)
    return callables, jobs, shutdowns, urls
