"""Utility functions to manage plugin callables from a Python module.

This module provides the loader infrastructure for Sopel's plugin system. It
handles the initialization, validation, and preparation of callable functions
that respond to IRC events (commands, rules, URL callbacks, and jobs).

**Loader's Role in Plugin Initialization:**

The loader processes Python modules containing Sopel plugins and extracts
callable functions decorated with :mod:`sopel.plugin` decorators. It performs
several critical initialization tasks:

1. **Callable Discovery**: Identifies functions marked with ``_sopel_callable``
2. **Attribute Initialization**: Sets default values for threading, rate limiting,
   priority, and other execution parameters
3. **Documentation Processing**: Extracts docstrings and examples, replacing
   placeholders (``$nickname``, help prefix) with configured values
4. **Regex Compilation**: Compiles intent patterns for efficient matching
5. **Callable Classification**: Categorizes functions into triggerables, jobs,
   URL callbacks, and shutdown handlers

**Callable Types:**

* **Triggerable**: Responds to IRC messages (commands, rules, events, intents)
* **Limitable**: Passes through rate-limiting machinery (triggerables + URL callbacks)
* **URL Callback**: Handles URLs detected in IRC messages
* **Job**: Executes on a timer interval (not message-triggered)
* **Shutdown Handler**: Runs when the bot shuts down

The distinction between these types determines which attributes are initialized
and how the callable is registered with the bot's rule manager.

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
    """Clean the callable. (compile regexes, fix docs, set defaults)

    :param func: the callable to clean
    :type func: callable
    :param config: Sopel's settings
    :type config: :class:`sopel.config.Config`

    This function will set all the default attributes expected for a Sopel
    callable, i.e. properties related to threading, docs, examples, rate
    limiting, commands, rules, and other features.

    **Attribute Initialization Process:**

    1. **Extract documentation**: Parse docstring and prepare doc storage
    2. **Set threading default**: All callables default to threaded execution
    3. **Initialize rate limiting**: Add rate limit attributes for limitable callables
    4. **Early return for jobs**: Jobs don't need trigger-handling attributes
    5. **Set trigger attributes**: Add echo, priority, output_prefix for message handlers
    6. **Normalize events**: Ensure event names are uppercase
    7. **Process examples**: Build help text with nickname/prefix substitution
    8. **Compile intents**: Convert intent patterns to compiled regexes
    """
    # Extract bot configuration for documentation substitution
    nick = config.core.nick
    help_prefix = config.core.help_prefix

    # Initialize documentation storage dict (maps command names to (doc, examples) tuples)
    func._docs = {}
    doc = []
    examples = []

    # Extract and split docstring into lines for help text display
    docstring = inspect.getdoc(func)
    if docstring:
        doc = docstring.splitlines()

    # Set threading default: all callables execute in threads unless explicitly disabled
    # This prevents blocking the main bot loop during callable execution
    func.thread = getattr(func, 'thread', True)

    # Initialize rate limiting attributes for callables that pass through rate-limiting machinery
    if is_limitable(func):
        # These attributes are a waste of memory on callables that don't pass
        # through Sopel's rate-limiting machinery (e.g., jobs)
        # Rate limits control how often a user/channel/server can trigger this callable
        func.rate = getattr(func, 'rate', 0)              # Per-user rate limit (seconds)
        func.channel_rate = getattr(func, 'channel_rate', 0)  # Per-channel rate limit
        func.global_rate = getattr(func, 'global_rate', 0)    # Global rate limit
        func.unblockable = getattr(func, 'unblockable', False)  # Bypass ignore/block lists

    # Early return for jobs: they don't need trigger-handling attributes
    if not is_triggerable(func) and not is_url_callback(func):
        # Adding the remaining default attributes below is potentially
        # confusing to other code (and a waste of memory) for jobs.
        # Jobs execute on timers and don't respond to IRC triggers.
        return

    # Initialize attributes for triggerables and URL callbacks
    # These control how the callable responds to IRC messages
    func.echo = getattr(func, 'echo', False)  # Whether to respond to bot's own messages
    func.priority = getattr(func, 'priority', 'medium')  # Execution priority (high/medium/low)
    func.output_prefix = getattr(func, 'output_prefix', '')  # Prefix for bot responses

    # Normalize IRC event names to uppercase for consistent matching
    # Events like "privmsg" become "PRIVMSG" to match IRC protocol specification
    if not hasattr(func, 'event'):
        # Default to PRIVMSG (standard IRC message event) if not specified
        func.event = ['PRIVMSG']
    else:
        # Convert all event names to uppercase (e.g., ['privmsg', 'notice'] -> ['PRIVMSG', 'NOTICE'])
        func.event = [event.upper() for event in func.event]

    # Build documentation and examples for command-type callables
    # This processes help text that users see when they ask for help with a command
    if any(hasattr(func, attr) for attr in ['commands', 'nickname_commands', 'action_commands']):
        if hasattr(func, 'example'):
            # Example processing: Extract user-facing examples from the decorated function
            # If no examples are flagged as user-facing (with "help": True),
            # just show the first example like Sopel<7.0 did for backward compatibility
            examples = [rec["example"] for rec in func.example if rec["help"]] or [func.example[0]["example"]]

            # Process each example to substitute bot-specific values
            for i, example in enumerate(examples):
                # Replace $nickname placeholder with actual bot nickname
                # Example: "$nickname: hello" becomes "BotName: hello"
                example = example.replace('$nickname', nick)

                # Command prefix replacement for help text generation
                # If the example doesn't already start with the configured help_prefix
                # and doesn't start with a nickname, replace the default prefix with the configured one
                # This allows examples to be written with a standard prefix (like '.')
                # and automatically adapt to the bot's configured prefix (like '!')
                if example[0] != help_prefix and not example.startswith(nick):
                    # Replace only the first occurrence of the default help prefix
                    # Example: ".help command" becomes "!help command" if help_prefix is '!'
                    example = example.replace(
                        COMMAND_DEFAULT_HELP_PREFIX, help_prefix, 1)
                examples[i] = example

        # Store documentation for each command name
        # This creates a mapping from command name to (docstring, examples) tuple
        # so the help system can look up documentation by command name
        if doc or examples:
            cmds = []
            # Collect all command names (both regular commands and nickname commands)
            cmds.extend(getattr(func, 'commands', []))
            cmds.extend(getattr(func, 'nickname_commands', []))
            # Map each command name to its documentation tuple
            # This allows ".help <command>" to display the right documentation
            for command in cmds:
                func._docs[command] = (doc, examples)

    # Compile intent patterns for efficient matching
    # Intents are used for CTCP actions and other IRC message metadata
    if hasattr(func, 'intents'):
        # Determine the regex type (implementation-dependent across Python versions)
        _regex_type = type(re.compile(''))

        # Ensure all intents are compiled regex objects
        # This allows intent patterns to be specified as either strings or pre-compiled regexes
        # All patterns are compiled with IGNORECASE for flexible intent matching
        func.intents = [
            (intent
                if isinstance(intent, _regex_type)  # Already compiled, use as-is
                else re.compile(intent, re.IGNORECASE))  # String pattern, compile it
            for intent in func.intents
        ]


def is_limitable(obj):
    """Check if ``obj`` needs to carry attributes related to limits.

    :param obj: any :term:`function` to check
    :return: ``True`` if ``obj`` must have limit-related attributes
    :rtype: bool

    Limitable callables aren't necessarily triggerable directly, but they all
    must pass through Sopel's rate-limiting machinery during dispatching.
    Therefore, they must have the attributes checked by that machinery.

    **What makes a callable "limitable":**

    A callable is limitable if it has any of these attributes (indicating it
    responds to IRC messages or URLs) AND does not have forbidden attributes
    (like ``interval`` for jobs):

    * Pattern-based triggers: ``rule``, ``find_rules``, ``search_rules``
      (and their ``_lazy_loaders`` variants)
    * Event/intent triggers: ``event``, ``intents``
    * Command triggers: ``commands``, ``nickname_commands``, ``action_commands``
    * URL triggers: ``url_regex``, ``url_lazy_loaders``

    **Why this matters:**

    Limitable callables need rate-limiting attributes (``rate``,
    ``channel_rate``, ``global_rate``, ``unblockable``) to prevent abuse.
    Jobs (with ``interval``) don't need rate limiting since they're not
    user-triggered.

    This includes both triggerables (message handlers) and URL callbacks,
    but excludes jobs and other non-message-triggered callables.
    """
    forbidden_attrs = (
        'interval',  # Jobs are not rate-limited
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    allowed_attrs = (
        # Pattern-based rule attributes
        'rule',
        'rule_lazy_loaders',
        'find_rules',
        'find_rules_lazy_loaders',
        'search_rules',
        'search_rules_lazy_loaders',
        # Event and intent attributes
        'event',
        'intents',
        # Command attributes
        'commands',
        'nickname_commands',
        'action_commands',
        # URL callback attributes
        'url_regex',
        'url_lazy_loaders',
    )
    allowed = any(hasattr(obj, attr) for attr in allowed_attrs)

    return allowed and not forbidden


def is_triggerable(obj):
    """Check if ``obj`` can handle the bot's triggers.

    :param obj: any :term:`function` to check
    :return: ``True`` if ``obj`` can handle the bot's triggers
    :rtype: bool

    A triggerable is a callable that will be used by the bot to handle a
    particular trigger (i.e. an IRC message): it can be a regex rule, an
    event, an intent, a command, a nickname command, or an action command.
    However, it must not be a job or a URL callback.

    **What makes a callable "triggerable":**

    A callable is triggerable if it has IRC message handling attributes AND
    does not have attributes that indicate it's a different callable type:

    * **Allowed**: Pattern-based rules (``rule``, ``find_rules``,
      ``search_rules``), event/intent handlers (``event``, ``intents``),
      or commands (``commands``, ``nickname_commands``, ``action_commands``)
    * **Forbidden**: Jobs (``interval``), URL callbacks (``url_regex``)

    **Triggerable vs. Limitable vs. URL Callback:**

    * **Triggerable**: Handles IRC messages directly (commands, rules, events)
    * **Limitable**: Triggerable OR URL callback (anything rate-limited)
    * **URL Callback**: Handles URLs extracted from messages (not direct triggers)
    * **Job**: Timer-based execution (neither triggerable nor limitable)

    This distinction is critical because:

    * Triggerables get full message-handling attributes (``echo``, ``priority``, etc.)
    * URL callbacks get rate-limiting but not all triggerable attributes
    * Jobs get minimal attributes (no rate limiting, no trigger handling)

    .. seealso::

        Many of the decorators defined in :mod:`sopel.plugin` make the
        decorated function a triggerable object.

    """
    forbidden_attrs = (
        'interval',        # Jobs are not triggerables
        'url_regex',       # URL callbacks are not direct triggerables
        'url_lazy_loaders',
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    allowed_attrs = (
        # Pattern-based rule attributes
        'rule',
        'rule_lazy_loaders',
        'find_rules',
        'find_rules_lazy_loaders',
        'search_rules',
        'search_rules_lazy_loaders',
        # Event and intent attributes
        'event',
        'intents',
        # Command attributes
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
    :rtype: bool

    A URL callback handler is a callable that will be used by the bot to
    handle a particular URL in an IRC message.

    **What makes a callable a "URL callback":**

    A callable is a URL callback if it has URL pattern matching attributes
    (``url_regex`` or ``url_lazy_loaders``) AND is not a job (doesn't have
    ``interval``).

    **How URL callbacks differ from triggerables:**

    * **Triggerable**: Matches patterns against the full IRC message text
    * **URL callback**: Matches patterns against URLs extracted from messages

    URL callbacks are invoked after the bot's URL detection extracts URLs
    from an IRC message. The patterns match against individual URL strings,
    not the entire message. This allows specialized handling for specific
    domains or URL patterns (e.g., YouTube links, GitHub issues).

    **Processing flow:**

    1. Bot receives IRC message: ``"Check out https://example.com/path"``
    2. URL detection extracts: ``["https://example.com/path"]``
    3. Each URL is tested against URL callback patterns
    4. Matching callbacks execute with the URL as context

    .. seealso::

        Both :func:`sopel.plugin.url` :func:`sopel.plugin.url_lazy` make the
        decorated function a URL callback handler.

    """
    forbidden_attrs = (
        'interval',  # Jobs cannot be URL callbacks
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    allowed_attrs = (
        'url_regex',        # Static URL patterns
        'url_lazy_loaders', # Dynamic URL patterns loaded at runtime
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
