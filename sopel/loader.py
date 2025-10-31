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
    """Clean the callable by setting defaults, compiling regexes, and preparing documentation.

    :param func: the callable to clean
    :type func: callable
    :param config: Sopel's settings
    :type config: :class:`sopel.config.Config`

    This function prepares a plugin callable for use by the bot by:

    * Setting default attributes for threading, rate limiting, and output
    * Extracting and formatting documentation from the callable's docstring
    * Compiling regex patterns from intent decorators
    * Preparing command examples with proper help prefix substitution
    * Setting event types to uppercase for consistent comparison

    The function distinguishes between three types of callables:

    * **Triggerables**: Handle IRC messages via commands, rules, events, or intents
    * **URL callbacks**: Process URLs found in IRC messages
    * **Jobs**: Execute on a schedule (these receive minimal attribute setup)

    Only triggerables and URL callbacks receive the full set of attributes,
    as jobs don't pass through the same dispatch machinery.

    **Example:**

    After loading a plugin module, each callable is cleaned::

        from sopel import plugin, loader

        @plugin.command('hello')
        @plugin.example('.hello', 'Says hello!')
        def hello_command(bot, trigger):
            bot.say('Hello!')

        # During plugin loading:
        loader.clean_callable(hello_command, config)
        # Now hello_command has: thread=True, rate=0, priority='medium', etc.

    .. seealso::

        :func:`clean_module` calls this function for each callable found in a
        plugin module.

        :func:`is_triggerable`, :func:`is_url_callback`, and :func:`is_limitable`
        determine which attributes are needed for each callable type.
    """
    nick = config.core.nick
    help_prefix = config.core.help_prefix
    # Initialize documentation storage: maps command names to (docstring, examples) tuples
    func._docs = {}
    doc = []
    examples = []

    # Extract and split docstring into lines for help system
    docstring = inspect.getdoc(func)
    if docstring:
        doc = docstring.splitlines()

    # Default to threaded execution unless explicitly marked as non-threaded
    # Threading prevents slow handlers from blocking other plugin dispatch
    func.thread = getattr(func, 'thread', True)

    if is_limitable(func):
        # Set rate-limiting defaults for callables that go through dispatch
        # Jobs don't pass through rate-limiting, so they don't need these attributes
        func.rate = getattr(func, 'rate', 0)  # Per-user rate limit (seconds)
        func.channel_rate = getattr(func, 'channel_rate', 0)  # Per-channel rate limit (seconds)
        func.global_rate = getattr(func, 'global_rate', 0)  # Global rate limit (seconds)
        func.unblockable = getattr(func, 'unblockable', False)  # Bypass rate limits and blocklists

    if not is_triggerable(func) and not is_url_callback(func):
        # Jobs only need threading and rate-limiting attributes (set above)
        # Adding echo, priority, output_prefix, event, etc. would be confusing
        # and wasteful for scheduled jobs that don't respond to IRC messages
        return

    # Set additional attributes for triggerables and URL callbacks
    func.echo = getattr(func, 'echo', False)  # Whether to trigger on bot's own messages
    func.priority = getattr(func, 'priority', 'medium')  # Execution priority: high, medium, or low
    func.output_prefix = getattr(func, 'output_prefix', '')  # Prefix for bot responses

    # Normalize event types to uppercase for consistent matching
    # Default to PRIVMSG (regular channel/private messages)
    if not hasattr(func, 'event'):
        func.event = ['PRIVMSG']
    else:
        func.event = [event.upper() for event in func.event]

    if any(hasattr(func, attr) for attr in ['commands', 'nickname_commands', 'action_commands']):
        if hasattr(func, 'example'):
            # Extract user-facing examples (marked with help=True in @plugin.example)
            # If no examples are marked for help, default to showing the first example (Sopel <7.0 behavior)
            examples = [rec["example"] for rec in func.example if rec["help"]] or [func.example[0]["example"]]
            # Substitute placeholders and normalize help prefix in examples
            for i, example in enumerate(examples):
                example = example.replace('$nickname', nick)  # Replace $nickname with actual bot nick
                # If example doesn't start with current help_prefix or bot nick, replace default prefix
                if example[0] != help_prefix and not example.startswith(nick):
                    example = example.replace(
                        COMMAND_DEFAULT_HELP_PREFIX, help_prefix, 1)
                examples[i] = example
        if doc or examples:
            # Build command list from regular and nickname commands (not action commands)
            cmds = []
            cmds.extend(getattr(func, 'commands', []))
            cmds.extend(getattr(func, 'nickname_commands', []))
            # Map each command name to its documentation tuple for help system lookup
            for command in cmds:
                func._docs[command] = (doc, examples)

    # Compile intent patterns for intent-based triggering
    if hasattr(func, 'intents'):
        # Determine regex type dynamically since it may vary by Python implementation
        _regex_type = type(re.compile(''))
        # Compile string patterns to regex objects with IGNORECASE flag
        # Already-compiled patterns are left as-is (for lazy loaders or advanced use)
        func.intents = [
            (intent
                if isinstance(intent, _regex_type)
                else re.compile(intent, re.IGNORECASE))
            for intent in func.intents
        ]


def is_limitable(obj):
    """Check if ``obj`` needs to carry attributes related to limits.

    :param obj: any :term:`function` to check
    :type obj: callable
    :return: ``True`` if ``obj`` must have limit-related attributes
    :rtype: bool

    Limitable callables aren't necessarily triggerable directly, but they all
    must pass through Sopel's rate-limiting machinery during dispatching.
    Therefore, they must have the attributes checked by that machinery.

    This includes:

    * **Triggerables**: commands, rules, events, intents, action commands
    * **URL callbacks**: functions that process URLs in messages

    Jobs (scheduled callables with ``interval`` attribute) are excluded because
    they don't go through the dispatch and rate-limiting code paths.

    .. seealso::

        :func:`is_triggerable` and :func:`is_url_callback` are more specific
        checks that build upon this one.
    """
    # Jobs have an 'interval' attribute and don't use rate-limiting
    forbidden_attrs = (
        'interval',
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    # Attributes that indicate a callable goes through dispatch and rate-limiting
    # Includes rule matchers (rule, find_rules, search_rules), commands, events, intents, and URL handlers
    # Lazy loaders are deferred pattern compilation for performance
    allowed_attrs = (
        'rule',  # Regular rule pattern matching
        'rule_lazy_loaders',  # Deferred rule compilation
        'find_rules',  # Find-style pattern matching
        'find_rules_lazy_loaders',
        'search_rules',  # Search-style pattern matching
        'search_rules_lazy_loaders',
        'event',  # IRC event handlers (PRIVMSG, JOIN, etc.)
        'intents',  # Intent-based triggering
        'commands',  # Regular commands (.hello)
        'nickname_commands',  # Nickname-prefixed commands (BotName: hello)
        'action_commands',  # ACTION/CTCP commands
        'url_regex',  # URL pattern matching
        'url_lazy_loaders',  # Deferred URL pattern compilation
    )
    allowed = any(hasattr(obj, attr) for attr in allowed_attrs)

    return allowed and not forbidden


def is_triggerable(obj):
    """Check if ``obj`` can handle the bot's triggers.

    :param obj: any :term:`function` to check
    :type obj: callable
    :return: ``True`` if ``obj`` can handle the bot's triggers
    :rtype: bool

    A triggerable is a callable that will be used by the bot to handle a
    particular trigger (i.e. an IRC message): it can be a regex rule, an
    event, an intent, a command, a nickname command, or an action command.

    **Distinction from other callable types:**

    * **Triggerables**: Respond to IRC messages (this function returns True)
    * **URL callbacks**: Process URLs found in messages (excluded here)
    * **Jobs**: Execute on schedule (excluded here)

    Triggerables are registered with the bot's rules manager and dispatched
    when IRC messages match their patterns or commands.

    .. seealso::

        Many of the decorators defined in :mod:`sopel.plugin` make the
        decorated function a triggerable object: :func:`~sopel.plugin.command`,
        :func:`~sopel.plugin.rule`, :func:`~sopel.plugin.event`,
        :func:`~sopel.plugin.intent`, etc.
    """
    # Jobs and URL callbacks are not triggerables
    # Jobs run on schedule, URL callbacks process URLs
    forbidden_attrs = (
        'interval',  # Marks a scheduled job
        'url_regex',  # Marks a URL callback
        'url_lazy_loaders',  # Marks a URL callback with lazy compilation
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    # Attributes that mark a callable as responding to IRC messages
    # Rules use pattern matching, commands use prefix matching, events match IRC protocol events
    allowed_attrs = (
        'rule',  # Full-message regex matching
        'rule_lazy_loaders',  # Deferred rule compilation
        'find_rules',  # Substring regex matching
        'find_rules_lazy_loaders',
        'search_rules',  # Search-anywhere regex matching
        'search_rules_lazy_loaders',
        'event',  # IRC protocol event handlers (JOIN, PART, PRIVMSG, etc.)
        'intents',  # Intent-based natural language matching
        'commands',  # Prefix commands like .help
        'nickname_commands',  # Commands prefixed with bot's nickname
        'action_commands',  # Commands in CTCP ACTION messages (/me)
    )
    allowed = any(hasattr(obj, attr) for attr in allowed_attrs)

    return allowed and not forbidden


def is_url_callback(obj):
    """Check if ``obj`` can handle a URL callback.

    :param obj: any :term:`function` to check
    :type obj: callable
    :return: ``True`` if ``obj`` can handle a URL callback
    :rtype: bool

    A URL callback handler is a callable that will be used by the bot to
    handle a particular URL in an IRC message.

    **Distinction from other callable types:**

    * **URL callbacks**: Process URLs matching specific patterns (this function returns True)
    * **Triggerables**: Respond to IRC messages generally (excluded here)
    * **Jobs**: Execute on schedule (excluded here)

    URL callbacks are triggered when the bot detects a URL in a message that
    matches the callback's pattern. Multiple callbacks can be triggered for
    different URLs in the same message.

    .. seealso::

        Both :func:`sopel.plugin.url` and :func:`sopel.plugin.url_lazy` make the
        decorated function a URL callback handler. The ``url_lazy`` variant
        defers regex compilation for better startup performance.
    """
    # Jobs are not URL callbacks - they run on schedule
    forbidden_attrs = (
        'interval',  # Marks a scheduled job
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    # Attributes that mark a callable as handling URL patterns
    # url_regex contains compiled patterns, url_lazy_loaders defers compilation
    allowed_attrs = (
        'url_regex',  # Pre-compiled URL pattern(s)
        'url_lazy_loaders',  # Deferred URL pattern compilation
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
    :rtype: tuple of (list, list, list, list)

    This function inspects a loaded plugin module and categorizes its callables
    into four groups:

    * **Triggerables**: Commands, rules, events, and intents that respond to IRC messages
    * **Jobs**: Scheduled callables that execute at regular intervals
    * **Shutdown handlers**: Functions named ``shutdown`` for cleanup on bot exit
    * **URL callbacks**: Functions that process URLs found in messages

    The function performs these operations:

    1. Iterates through all module attributes using :func:`vars`
    2. Identifies Sopel callables by checking for ``_sopel_callable`` flag
    3. Categorizes each callable using :func:`is_triggerable` and :func:`is_url_callback`
    4. Calls :func:`clean_callable` to set default attributes on each callable
    5. Returns categorized lists for registration with the bot

    **Example:**

    When a plugin module is loaded::

        import sopel.loader
        import my_plugin

        callables, jobs, shutdowns, urls = sopel.loader.clean_module(
            my_plugin, config
        )
        # callables: [hello_command, goodbye_command, join_handler]
        # jobs: [periodic_task]
        # shutdowns: [shutdown]
        # urls: [handle_youtube_url]

    .. note::

        Callables without the ``_sopel_callable`` flag are ignored (except
        for ``shutdown`` functions), preventing unintended functions from
        being registered as plugin handlers.

    .. seealso::

        :func:`clean_callable` processes each individual callable found.

        Plugin decorators in :mod:`sopel.plugin` set the ``_sopel_callable``
        flag and other attributes that this function checks.
    """
    # Initialize lists to categorize the different callable types
    callables = []  # Triggerables: commands, rules, events, intents
    shutdowns = []  # Cleanup handlers called on bot shutdown
    jobs = []  # Scheduled tasks with interval attribute
    urls = []  # URL pattern handlers

    # Inspect all module-level attributes
    for obj in vars(module).values():
        if callable(obj):
            # Check if this callable was marked by a Sopel decorator
            is_sopel_callable = getattr(obj, '_sopel_callable', False) is True

            # Shutdown functions are special: recognized by name, not decorator
            if getattr(obj, '__name__', None) == 'shutdown':
                shutdowns.append(obj)
            # Skip non-Sopel callables (regular module functions)
            elif not is_sopel_callable:
                continue
            # Categorize by callable type using attribute inspection
            elif is_triggerable(obj):
                # Commands, rules, events, intents - respond to IRC messages
                clean_callable(obj, config)
                callables.append(obj)
            elif hasattr(obj, 'interval'):
                # Jobs - scheduled execution (not message-triggered)
                clean_callable(obj, config)
                jobs.append(obj)
            elif is_url_callback(obj):
                # URL callbacks - triggered when URLs match patterns
                clean_callable(obj, config)
                urls.append(obj)

    # Return in order: triggerables, jobs, shutdowns, urls
    return callables, jobs, shutdowns, urls
