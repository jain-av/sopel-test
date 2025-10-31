"""Utility functions to manage plugin callables from a Python module.

This module provides the plugin loader infrastructure that processes Python
functions decorated with :mod:`sopel.plugin` decorators and prepares them for
registration with the bot. It handles attribute initialization, documentation
generation, rate limiting setup, and callable classification.

Loader's Role in Plugin Initialization
---------------------------------------

The loader acts as a bridge between raw Python functions and the bot's plugin
management system. When a plugin module is loaded, the loader:

1. **Identifies callables**: Scans the module for decorated functions
2. **Classifies callables**: Determines if each is a trigger handler, job,
   URL callback, or shutdown function
3. **Initializes attributes**: Sets default values for threading, rate limiting,
   priority, and event handling
4. **Processes documentation**: Extracts docstrings and examples, performs
   placeholder substitution for bot nickname and command prefix
5. **Prepares for registration**: Ensures all required attributes are present
   for the rule management system

Callable Classification
-----------------------

The loader distinguishes between several types of callables:

* **Triggerable**: Handlers for IRC events, commands, or pattern matches.
  These can be triggered by incoming IRC messages and pass through rate
  limiting. Includes: rules, commands, events, intents.

* **Limitable**: Callables that require rate-limiting attributes even if not
  directly triggerable. This is a superset of triggerables that also includes
  certain special handlers.

* **URL Callback**: Handlers specifically for URLs detected in messages.
  These are triggered when URLs matching specific patterns appear in IRC.

* **Job**: Scheduled tasks that run on intervals. These are not triggered by
  IRC messages and don't pass through rate limiting.

* **Shutdown**: Special handlers called when the bot shuts down. Used for
  cleanup operations.

Attribute Initialization
------------------------

The :func:`clean_callable` function ensures each callable has all required
attributes with appropriate defaults:

* **Threading**: ``thread`` (default: ``True``)
* **Rate limiting**: ``rate``, ``channel_rate``, ``global_rate`` (for limitable)
* **Priority**: ``priority`` (default: ``'medium'``)
* **Events**: ``event`` (default: ``['PRIVMSG']``)
* **Documentation**: ``_docs`` dictionary mapping commands to (doc, examples)
* **Output**: ``output_prefix``, ``echo`` settings

Documentation Processing
------------------------

For command handlers, the loader generates help documentation by:

1. Extracting the function's docstring
2. Finding examples marked for user-facing help (``is_help=True``)
3. Performing placeholder substitution:

   * ``$nickname`` → bot's actual nickname
   * Default prefix → configured help prefix (e.g., ``.`` → ``!``)

4. Mapping documentation to each command name and alias

This ensures that ``!help command`` displays accurate, personalized help text.

Example::

    @plugin.command('greet')
    @plugin.example('.greet', 'Greets the user')
    def greet_command(bot, trigger):
        '''Say hello to a user'''
        bot.say('Hello!')

After loading, ``.greet`` in help will show as the configured prefix (e.g.,
``!greet`` if ``help_prefix='!'``), and ``$nickname`` placeholders will be
replaced with the bot's actual name.

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

    This function initializes all attributes expected for a Sopel callable:

    1. **Basic attributes**: Threading settings for all callables
    2. **Rate limiting**: Attributes for callables that pass through rate
       limiting machinery (limitable callables only)
    3. **Trigger handling**: Priority, events, echo settings for triggerables
       and URL callbacks
    4. **Documentation**: Help text generation with placeholder substitution
       for commands
    5. **Intent matching**: Compile intent patterns for intent-based handlers

    The function sets sensible defaults and performs necessary transformations
    to prepare the callable for the bot's plugin system.
    """
    # Get bot configuration for placeholder substitution
    nick = config.core.nick
    help_prefix = config.core.help_prefix

    # Initialize documentation storage: maps command names to (docstring, examples)
    func._docs = {}
    doc = []
    examples = []

    # Extract and process the function's docstring
    docstring = inspect.getdoc(func)
    if docstring:
        # Split into lines for help display
        doc = docstring.splitlines()

    # Set threading attribute (default: True for parallel execution)
    func.thread = getattr(func, 'thread', True)

    # Initialize rate limiting attributes for callables that need them
    if is_limitable(func):
        # Rate limiting attributes are only needed for callables that pass
        # through Sopel's rate-limiting machinery (triggerables and URL callbacks)
        # Jobs don't need these as they run on fixed schedules
        func.rate = getattr(func, 'rate', 0)              # Per-user rate limit (seconds)
        func.channel_rate = getattr(func, 'channel_rate', 0)  # Per-channel rate limit
        func.global_rate = getattr(func, 'global_rate', 0)    # Global rate limit
        func.unblockable = getattr(func, 'unblockable', False)  # Bypass ignore list

    # Early return for jobs: they don't need trigger-related attributes
    if not is_triggerable(func) and not is_url_callback(func):
        # Jobs run on intervals and don't respond to triggers, so adding
        # trigger-related attributes would be confusing and wasteful
        return

    # Initialize trigger-related attributes for triggerables and URL callbacks
    func.echo = getattr(func, 'echo', False)           # Handle echo-message
    func.priority = getattr(func, 'priority', 'medium')  # Execution priority
    func.output_prefix = getattr(func, 'output_prefix', '')  # Message prefix

    # Initialize and normalize event types
    if not hasattr(func, 'event'):
        # Default to PRIVMSG (channel messages and private messages)
        func.event = ['PRIVMSG']
    else:
        # Normalize event names to uppercase for consistent matching
        func.event = [event.upper() for event in func.event]

    # Process documentation for command-type handlers
    # Commands, nickname commands, and action commands all need help text
    if any(hasattr(func, attr) for attr in ['commands', 'nickname_commands', 'action_commands']):
        if hasattr(func, 'example'):
            # Extract examples marked for help display
            # Backward compatibility: if no examples are marked with is_help=True,
            # fall back to showing the first example (Sopel <7.0 behavior)
            examples = [rec["example"] for rec in func.example if rec["help"]] or [func.example[0]["example"]]

            # Perform placeholder substitution in each example
            for i, example in enumerate(examples):
                # Replace $nickname placeholder with actual bot nickname
                example = example.replace('$nickname', nick)

                # Replace default command prefix with configured help prefix
                # Only replace if the example doesn't already start with the
                # configured prefix or the bot's nickname
                if example[0] != help_prefix and not example.startswith(nick):
                    # Replace the default prefix (.) with the configured one
                    # Only replace the first occurrence to avoid breaking examples
                    # that intentionally include the default prefix elsewhere
                    example = example.replace(
                        COMMAND_DEFAULT_HELP_PREFIX, help_prefix, 1)
                examples[i] = example

        # Build documentation mapping for help system
        if doc or examples:
            # Collect all command names (primary commands and nickname commands)
            cmds = []
            cmds.extend(getattr(func, 'commands', []))
            cmds.extend(getattr(func, 'nickname_commands', []))

            # Map each command name to its documentation
            # This allows !help <command> to find the appropriate help text
            for command in cmds:
                func._docs[command] = (doc, examples)

    # Compile intent regex patterns for intent-based handlers
    if hasattr(func, 'intents'):
        # Get regex type (implementation-dependent)
        _regex_type = type(re.compile(''))

        # Compile string intents to regex, leave compiled patterns as-is
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
    :rtype: bool

    A **limitable** callable is one that passes through Sopel's rate-limiting
    machinery during message dispatching. This includes:

    * **Triggerables**: Rules, commands, events, intents
    * **URL callbacks**: Handlers for URLs in messages

    But explicitly excludes:

    * **Jobs**: Scheduled tasks with ``interval`` attribute

    Limitable callables need these attributes:

    * ``rate``: Per-user rate limit (seconds between triggers)
    * ``channel_rate``: Per-channel rate limit
    * ``global_rate``: Global rate limit across all users/channels
    * ``unblockable``: Whether to bypass user ignore lists

    This is a broader category than :func:`is_triggerable`, as it includes
    URL callbacks which have different invocation semantics but still require
    rate limiting.

    .. seealso::

        The rate-limiting attributes are initialized by :func:`clean_callable`
        only for limitable callables to avoid wasting memory on jobs.
    """
    # Jobs have interval attribute and should never be rate-limited
    # They run on fixed schedules regardless of IRC activity
    forbidden_attrs = (
        'interval',  # Marks scheduled jobs
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    # Attributes that indicate the callable responds to IRC messages
    # and should be subject to rate limiting
    allowed_attrs = (
        # Generic pattern matching rules
        'rule',                      # @plugin.rule() patterns
        'rule_lazy_loaders',         # Lazy-loaded @plugin.rule_lazy()
        'find_rules',                # @plugin.find() patterns (multiple matches)
        'find_rules_lazy_loaders',   # Lazy-loaded @plugin.find_lazy()
        'search_rules',              # @plugin.search() patterns
        'search_rules_lazy_loaders', # Lazy-loaded @plugin.search_lazy()

        # Event and intent handlers
        'event',    # @plugin.event() IRC event types
        'intents',  # @plugin.intent() IRCv3 intent matching

        # Named command handlers
        'commands',          # @plugin.command() with prefix
        'nickname_commands', # @plugin.nickname_command() addressing bot
        'action_commands',   # @plugin.action_command() for /me actions

        # URL processing
        'url_regex',         # @plugin.url() URL pattern matching
        'url_lazy_loaders',  # Lazy-loaded @plugin.url_lazy()
    )
    allowed = any(hasattr(obj, attr) for attr in allowed_attrs)

    return allowed and not forbidden


def is_triggerable(obj):
    """Check if ``obj`` can handle the bot's triggers.

    :param obj: any :term:`function` to check
    :return: ``True`` if ``obj`` can handle the bot's triggers
    :rtype: bool

    A **triggerable** is a callable that responds directly to IRC messages
    (triggers). This includes:

    * **Rules**: Pattern matching with ``@plugin.rule()``, ``@plugin.find()``,
      ``@plugin.search()``
    * **Commands**: Named commands with ``@plugin.command()``
    * **Nick commands**: Bot-addressed commands with ``@plugin.nickname_command()``
    * **Action commands**: CTCP ACTION handlers with ``@plugin.action_command()``
    * **Events**: IRC event handlers with ``@plugin.event()``
    * **Intents**: IRCv3 intent handlers with ``@plugin.intent()``

    But explicitly excludes:

    * **Jobs**: Scheduled tasks (``interval`` attribute) - run on timers, not
      in response to IRC messages
    * **URL callbacks**: URL handlers (``url_regex`` attribute) - have special
      invocation with URL extraction; checked separately by
      :func:`is_url_callback`

    **Key distinction from** :func:`is_limitable`:

    * ``is_limitable`` = triggerables + URL callbacks (both need rate limiting)
    * ``is_triggerable`` = only direct IRC message handlers (excludes URLs)

    Triggerables receive ``(bot, trigger)`` arguments, while URL callbacks
    receive ``(bot, trigger, match)`` arguments, making them incompatible.

    .. seealso::

        Many of the decorators defined in :mod:`sopel.plugin` make the
        decorated function a triggerable object.

    """
    # Callables with these attributes are NOT triggerables
    forbidden_attrs = (
        'interval',           # Jobs run on schedules, not triggers
        'url_regex',          # URL callbacks have different invocation
        'url_lazy_loaders',   # Lazy-loaded URL callbacks
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    # Attributes that make a callable triggerable by IRC messages
    allowed_attrs = (
        # Pattern matching rules (generic triggers)
        'rule',                      # @plugin.rule() - match from start
        'rule_lazy_loaders',         # Lazy-loaded patterns
        'find_rules',                # @plugin.find() - multiple matches
        'find_rules_lazy_loaders',   # Lazy-loaded find patterns
        'search_rules',              # @plugin.search() - search anywhere
        'search_rules_lazy_loaders', # Lazy-loaded search patterns

        # IRC protocol handlers
        'event',    # @plugin.event() - IRC event types (PRIVMSG, JOIN, etc.)
        'intents',  # @plugin.intent() - IRCv3 message intents

        # Named command handlers
        'commands',          # @plugin.command() - prefix-based commands
        'nickname_commands', # @plugin.nickname_command() - bot addressing
        'action_commands',   # @plugin.action_command() - /me responses
    )
    allowed = any(hasattr(obj, attr) for attr in allowed_attrs)

    return allowed and not forbidden


def is_url_callback(obj):
    """Check if ``obj`` can handle a URL callback.

    :param obj: any :term:`function` to check
    :return: ``True`` if ``obj`` can handle a URL callback
    :rtype: bool

    A **URL callback** is a specialized handler triggered when URLs matching
    specific patterns are detected in IRC messages. URL callbacks:

    * Are triggered by URL detection, not direct message matching
    * Receive ``(bot, trigger, match)`` arguments (note the ``match`` parameter)
    * Match against URLs extracted from messages, not the full message text
    * Can be rate-limited like triggerables

    Created by:

    * ``@plugin.url()`` - immediate URL pattern registration
    * ``@plugin.url_lazy()`` - lazy-loaded URL pattern registration

    But explicitly excludes:

    * **Jobs**: Scheduled tasks (``interval`` attribute)

    **Key distinction from** :func:`is_triggerable`:

    URL callbacks are **not** triggerables because they:

    1. Have different invocation semantics (receive ``match`` parameter)
    2. Match against extracted URLs, not the IRC message text
    3. Are processed after URL extraction from the message

    However, URL callbacks **are** limitable (see :func:`is_limitable`) because
    they pass through rate-limiting machinery.

    .. seealso::

        Both :func:`sopel.plugin.url` and :func:`sopel.plugin.url_lazy` make
        the decorated function a URL callback handler.

    """
    # Jobs should never be URL callbacks
    forbidden_attrs = (
        'interval',  # Scheduled tasks don't process URLs
    )
    forbidden = any(hasattr(obj, attr) for attr in forbidden_attrs)

    # Attributes that identify URL callback handlers
    allowed_attrs = (
        'url_regex',         # @plugin.url() - URL pattern matching
        'url_lazy_loaders',  # @plugin.url_lazy() - lazy-loaded patterns
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
