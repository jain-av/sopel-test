"""Administrative commands for bot management and control.

This module provides administrative commands that allow bot administrators and
owners to control bot behavior, manage channel membership, send messages on
behalf of the bot, and modify runtime configuration.

**Command Privilege Levels:**

* **Admin commands** (``@plugin.require_admin``): Available to users listed in
  ``core.admin_accounts`` or matching configured admin hostmasks. These commands
  must be sent via private message to the bot.

* **Owner commands** (``@plugin.require_owner``): Available only to the bot owner
  specified in ``core.owner`` or ``core.owner_account``. These commands provide
  full control over the bot and must be sent via private message.

**Core Functionality:**

* **Channel Management**: Join/part channels, with optional persistence to config
* **Bot Control**: Restart or quit the bot with custom quit messages
* **Messaging**: Send messages or actions to channels on behalf of the bot
* **Configuration**: View and modify bot configuration at runtime
* **Event Handling**: Auto-join on invite, auto-rejoin on kick

**Configuration Options:**

The ``[admin]`` section supports:

* ``hold_ground``: Auto-rejoin channels after being kicked (default: False)
* ``auto_accept_invite``: Auto-join channels when invited by anyone (default: True)

Copyright 2010-2011, Sean B. Palmer (inamidst.com) and Michael Yanovich
(yanovich.net)
Copyright © 2012, Elad Alfassa, <elad@fedoraproject.org>
Copyright 2013, Ari Koivula <ari@koivu.la>
Copyright 2019, Florian Strzelecki, https://github.com/Exirel
Licensed under the Eiffel Forum License 2.

https://sopel.chat
"""
from __future__ import annotations

import logging

from sopel import plugin
from sopel.config import types

LOGGER = logging.getLogger(__name__)

ERROR_JOIN_NO_CHANNEL = 'Which channel should I join?'
"""Error message when channel is missing from command arguments."""
ERROR_PART_NO_CHANNEL = 'Which channel should I quit?'
"""Error message when channel is missing from command arguments."""
ERROR_NOTHING_TO_SAY = 'I need a channel and a message to talk.'
"""Error message when channel and/or message are missing."""
ERROR_NOTHING_TO_RAW = 'I need an IRC message to send.'
"""Error message when no raw IRC message was given."""


class AdminSection(types.StaticSection):
    hold_ground = types.BooleanAttribute('hold_ground', default=False)
    """Auto re-join on kick"""
    auto_accept_invite = types.BooleanAttribute('auto_accept_invite', default=True)
    """Auto-join channels when invited"""


def configure(config):
    """
    | name | example | purpose |
    | ---- | ------- | ------- |
    | hold\\_ground | False | Auto-rejoin the channel after being kicked. |
    | auto\\_accept\\_invite | True | Auto-join channels when invited. |
    """
    config.define_section('admin', AdminSection)
    config.admin.configure_setting('hold_ground',
                                   "Automatically re-join after being kicked?")
    config.admin.configure_setting('auto_accept_invite',
                                   'Automatically join channels when invited?')


def setup(bot):
    bot.config.define_section('admin', AdminSection)


class InvalidSection(Exception):
    """Exception raised when attempting to access a non-existent config section.

    This exception is raised by :func:`parse_section_option_value` and related
    configuration commands when a user attempts to get/set options in a config
    section that does not exist.

    :param str section: The name of the section that does not exist
    """
    def __init__(self, section):
        super().__init__(self, 'Section [{}] does not exist.'.format(section))
        self.section = section


class InvalidSectionOption(Exception):
    """Exception raised when attempting to access a non-existent config option.

    This exception is raised by :func:`parse_section_option_value` and related
    configuration commands when a user attempts to get/set an option that does
    not exist within an otherwise valid config section.

    :param str section: The name of the section containing the option
    :param str option: The name of the option that does not exist
    """
    def __init__(self, section, option):
        super().__init__(self, 'Section [{}] does not have option \'{}\'.'.format(section, option))
        self.section = section
        self.option = option


def _get_config_channels(channels):
    """Parse channel list from config into (channel, key) tuples.

    Channels in the config are stored as strings, with optional keys separated
    by spaces. This function parses the channel list and yields tuples of
    (channel_name, key) for each channel, where key is ``None`` if not specified.

    :param list channels: List of channel strings from ``core.channels``
    :return: Generator yielding ``(channel, key)`` tuples
    :rtype: generator

    Example::

        # Config: channels = #foo, #bar secretkey, #baz
        list(_get_config_channels(config.core.channels))
        # Returns: [('#foo', None), ('#bar', 'secretkey'), ('#baz', None)]
    """
    for channel_info in channels:
        if ' ' in channel_info:
            yield channel_info.split(' ', 1)
        else:
            yield (channel_info, None)


def _set_config_channels(bot, channels):
    """Save channel dictionary to config and persist to disk.

    Takes a dictionary of {channel: key} pairs and formats them as strings
    for storage in ``core.channels``. Channels with keys are stored as
    "channel key", channels without keys are stored as just "channel".
    The config is saved to disk after updating.

    :param bot: Bot instance with config to update
    :type bot: :class:`sopel.bot.Sopel`
    :param dict channels: Dictionary mapping channel names to keys (or None)
    """
    bot.config.core.channels = [
        ' '.join([part for part in items if part])
        for items in channels.items()
    ]
    bot.config.save()


def _join(bot, channel, key=None, save=True):
    """Join a channel and optionally persist it to config.

    Sends an IRC JOIN command for the specified channel, with an optional key
    (channel password). If ``save`` is True, adds the channel to ``core.channels``
    and persists the config to disk, so the bot will rejoin on restart.

    :param bot: Bot instance to execute the join
    :type bot: :class:`sopel.bot.Sopel`
    :param str channel: Channel name to join (e.g., "#example")
    :param str key: Optional channel key/password
    :param bool save: Whether to persist the channel to config (default: True)
    """
    if not channel:
        return

    if not key:
        bot.join(channel)
    else:
        bot.join(channel, key)

    if save:
        channels = dict(_get_config_channels(bot.config.core.channels))
        # Save only if channel is new or key has been changed
        if channel not in channels or channels[channel] != key:
            channels[channel] = key
            _set_config_channels(bot, channels)
            LOGGER.info('Added "%s" to core.channels.', channel)


def _part(bot, channel, msg=None, save=True):
    """Leave a channel and optionally remove it from config.

    Sends an IRC PART command for the specified channel with an optional part
    message. If ``save`` is True, removes the channel from ``core.channels``
    and persists the config to disk, so the bot won't rejoin on restart.

    :param bot: Bot instance to execute the part
    :type bot: :class:`sopel.bot.Sopel`
    :param str channel: Channel name to leave (e.g., "#example")
    :param str msg: Optional part message to send
    :param bool save: Whether to remove the channel from config (default: True)
    """
    bot.part(channel, msg or None)

    if save:
        channels = dict(_get_config_channels(bot.config.core.channels))
        if channel in channels:
            del channels[channel]
            _set_config_channels(bot, channels)
            LOGGER.info('Removed "%s" from core.channels.', channel)


@plugin.require_privmsg
@plugin.require_admin
@plugin.command('join')
@plugin.priority('low')
@plugin.example('.join #example key', user_help=True)
@plugin.example('.join #example', user_help=True)
def join(bot, trigger):
    """Join a channel and save it to the config.

    This command makes the bot join the specified channel immediately and adds
    it to ``core.channels`` in the config file, so the bot will automatically
    rejoin the channel on restart.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:** ``.join #channel [key]``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``#channel`` - Channel name to join (required)
    * ``key`` - Channel password/key (optional)

    **Examples:**
        * ``.join #example`` - Join public channel #example
        * ``.join #secret mypassword`` - Join password-protected channel #secret

    **Error conditions:**

    * If no channel is specified, replies with an error message
    """
    channel, key = trigger.group(3), trigger.group(4)
    if not channel:
        bot.reply(ERROR_JOIN_NO_CHANNEL)
        return

    _join(bot, channel, key)


@plugin.require_privmsg
@plugin.require_admin
@plugin.command('tmpjoin')
@plugin.priority('low')
@plugin.example('.tmpjoin #example key', user_help=True)
@plugin.example('.tmpjoin #example', user_help=True)
def temporary_join(bot, trigger):
    """Join a channel temporarily without saving to config.

    This command makes the bot join the specified channel immediately, but does
    **not** add it to ``core.channels``. The bot will not automatically rejoin
    this channel after restarting. Use this for one-time visits or testing.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:** ``.tmpjoin #channel [key]``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``#channel`` - Channel name to join (required)
    * ``key`` - Channel password/key (optional)

    **Error conditions:**

    * If no channel is specified, replies with an error message
    """
    channel, key = trigger.group(3), trigger.group(4)
    if not channel:
        bot.reply(ERROR_JOIN_NO_CHANNEL)
        return

    _join(bot, channel, key, save=False)


@plugin.require_privmsg
@plugin.require_admin
@plugin.command('part')
@plugin.priority('low')
@plugin.example('.part #example')
def part(bot, trigger):
    """Leave a channel and remove it from the config.

    This command makes the bot leave the specified channel immediately and
    removes it from ``core.channels`` in the config file, so the bot will not
    rejoin the channel on restart.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:** ``.part #channel [message]``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``#channel`` - Channel name to leave (required)
    * ``message`` - Part message to display (optional)

    **Examples:**
        * ``.part #example`` - Leave #example with no message
        * ``.part #example Goodbye everyone!`` - Leave with custom message

    **Error conditions:**

    * If no channel is specified, replies with an error message
    """
    channel, _sep, part_msg = trigger.group(2).partition(' ')
    if not channel:
        bot.reply(ERROR_PART_NO_CHANNEL)
        return

    _part(bot, channel, part_msg)


@plugin.require_privmsg
@plugin.require_admin
@plugin.command('tmppart')
@plugin.priority('low')
@plugin.example('.tmppart #example')
def temporary_part(bot, trigger):
    """Leave a channel temporarily without removing from config.

    This command makes the bot leave the specified channel immediately, but does
    **not** remove it from ``core.channels``. The bot will automatically rejoin
    this channel after restarting. Use this for temporary absences or testing.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:** ``.tmppart #channel [message]``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``#channel`` - Channel name to leave (required)
    * ``message`` - Part message to display (optional)

    **Examples:**
        * ``.tmppart #example`` - Temporarily leave #example
        * ``.tmppart #example Be right back!`` - Leave with custom message

    **Error conditions:**

    * If no channel is specified, replies with an error message
    """
    channel, _sep, part_msg = trigger.group(2).partition(' ')
    if not channel:
        bot.reply(ERROR_PART_NO_CHANNEL)
        return

    _part(bot, channel, part_msg, save=False)


@plugin.require_privmsg
@plugin.require_admin
@plugin.command('chanlist', 'channels')
@plugin.priority('low')
def channel_list(bot, trigger):
    """List all channels the bot is currently in.

    This command displays a comma-separated list of all channels the bot has
    joined. The list is sorted alphabetically for readability. If the list is
    very long, it will be split across multiple messages automatically.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:** ``.chanlist`` or ``.channels``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Example:**
        * ``.chanlist`` - Displays: "#lobby, #general, #help, #admin"
    """
    channels = ', '.join(sorted(bot.channels.keys()))

    # Conservative assumption about IRC line length limits to ensure
    # max_messages calculation prevents truncation of channel list
    bot.say(channels, max_messages=1 + len(channels) // 400)


@plugin.require_privmsg
@plugin.require_owner
@plugin.command('restart')
@plugin.priority('low')
def restart(bot, trigger):
    """Restart the bot process with an optional quit message.

    This command disconnects the bot from IRC with a quit message, then restarts
    the bot process. The bot will reconnect and rejoin all configured channels.
    This is useful for applying configuration changes or code updates.

    **Required privilege:** Owner (must be sent via private message)

    **Usage:** ``.restart [quit message]``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``quit message`` - Custom quit message (optional). If not provided,
      defaults to "Restarting on command from <nickname>."

    **Examples:**
        * ``.restart`` - Restart with default message
        * ``.restart Applying updates, be right back!`` - Restart with custom message

    .. warning::
        This command requires the bot to be running in a mode that supports
        restarts (not all deployment methods support automatic restart).
    """
    quit_message = trigger.group(2)
    default_message = 'Restarting on command from %s.' % trigger.nick
    if not quit_message:
        quit_message = default_message

    LOGGER.info(default_message)
    bot.restart(quit_message)


@plugin.require_privmsg
@plugin.require_owner
@plugin.command('quit')
@plugin.priority('low')
def quit(bot, trigger):
    """Shut down the bot with an optional quit message.

    This command disconnects the bot from IRC with a quit message and shuts
    down the bot process completely. Unlike ``restart``, the bot will not
    automatically reconnect. Use this to stop the bot gracefully.

    **Required privilege:** Owner (must be sent via private message)

    **Usage:** ``.quit [quit message]``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``quit message`` - Custom quit message (optional). If not provided,
      defaults to "Quitting on command from <nickname>."

    **Examples:**
        * ``.quit`` - Quit with default message
        * ``.quit Maintenance time, see you later!`` - Quit with custom message

    .. warning::
        The bot will not reconnect after this command. To restart the bot,
        you must manually start the bot process again.
    """
    quit_message = trigger.group(2)
    default_message = 'Quitting on command from %s.' % trigger.nick
    if not quit_message:
        quit_message = default_message

    LOGGER.info(default_message)
    bot.quit(quit_message)


@plugin.require_privmsg
@plugin.require_owner
@plugin.command('raw')
@plugin.priority('low')
@plugin.example('.raw PRIVMSG NickServ :CERT ADD')
def raw(bot, trigger):
    """Send a raw IRC protocol message to the server.

    This command allows sending arbitrary IRC protocol messages directly to
    the server without any processing. This is primarily useful for debugging,
    testing, or sending commands not otherwise supported by the bot.

    **Required privilege:** Owner (must be sent via private message)

    **Usage:** ``.raw <IRC command>``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``IRC command`` - Raw IRC protocol message (required)

    **Examples:**
        * ``.raw PRIVMSG NickServ :CERT ADD`` - Send certificate command to NickServ
        * ``.raw NAMES #channel`` - Request channel member list
        * ``.raw WHOIS SomeUser`` - Query user information

    **Error conditions:**

    * If no IRC command is specified, replies with an error message

    .. warning::
        This command sends raw IRC protocol messages without validation. Improper
        use can cause protocol violations, disconnection, or unexpected behavior.
        Use with caution and ensure you understand IRC protocol (:rfc:`2812`).
    """
    if trigger.group(2) is None:
        bot.reply(ERROR_NOTHING_TO_RAW)
        return

    bot.write([trigger.group(2)])


@plugin.require_privmsg
@plugin.require_admin
@plugin.command('say', 'msg')
@plugin.priority('low')
@plugin.example('.say #YourPants Does anyone else smell neurotoxin?')
def say(bot, trigger):
    """Send a message to a channel or user on behalf of the bot.

    This command makes the bot send a message to the specified channel or user.
    The message appears to come directly from the bot. This is useful for
    announcements, relaying messages, or communicating when the admin cannot
    join the target channel.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:** ``.say <target> <message>``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``target`` - Channel name or nickname to send message to (required)
    * ``message`` - Message content to send (required)

    **Examples:**
        * ``.say #lobby Server maintenance in 5 minutes`` - Announce to #lobby
        * ``.say Alice Please join #admin`` - Private message to Alice
        * ``.say #help !help schedule`` - Trigger another bot's command

    **Error conditions:**

    * If target or message is missing, replies with an error message
    """
    if trigger.group(2) is None:
        bot.reply(ERROR_NOTHING_TO_SAY)
        return

    channel, _sep, message = trigger.group(2).partition(' ')
    message = message.strip()
    if not channel or not message:
        bot.reply(ERROR_NOTHING_TO_SAY)
        return

    bot.say(message, channel)


@plugin.require_privmsg
@plugin.require_admin
@plugin.command('me')
@plugin.priority('low')
def me(bot, trigger):
    """Send an ACTION (/me) to a channel or user on behalf of the bot.

    This command makes the bot send an IRC ACTION message (equivalent to ``/me``)
    to the specified channel or user. Actions are displayed in third-person
    format (e.g., "* BotName does something"). This is useful for expressive
    announcements or roleplay.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:** ``.me <target> <action>``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``target`` - Channel name or nickname to send action to (required)
    * ``action`` - Action text to send (required)

    **Examples:**
        * ``.me #lobby is preparing for maintenance`` - Displays: "* BotName is preparing for maintenance"
        * ``.me #games rolls a 20!`` - Displays: "* BotName rolls a 20!"
        * ``.me Alice waves hello`` - Private action message to Alice

    **Error conditions:**

    * If target or action is missing, replies with an error message

    .. note::
        IRC ACTION messages are formatted as CTCP (Client-To-Client Protocol)
        messages and are typically displayed in italics or with special formatting.
    """
    if trigger.group(2) is None:
        bot.reply(ERROR_NOTHING_TO_SAY)
        return

    channel, _sep, action = trigger.group(2).partition(' ')
    action = action.strip()
    if not channel or not action:
        bot.reply(ERROR_NOTHING_TO_SAY)
        return

    bot.action(action, channel)


@plugin.event('INVITE')
@plugin.priority('low')
def invite_join(bot, trigger):
    """Automatically join channels when invited based on config and privileges.

    This event handler responds to IRC INVITE messages. The bot will automatically
    join the channel if either:

    1. The inviter is a bot admin (checked via ``trigger.admin``), OR
    2. The ``admin.auto_accept_invite`` config option is enabled

    If ``auto_accept_invite`` is disabled and the inviter is not an admin, the
    invitation is ignored and logged.

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the INVITE event
    :type trigger: :class:`sopel.trigger.Trigger`

    **Config requirements:**

    * ``admin.auto_accept_invite`` (default: True) - Accept invites from anyone

    **Behavior:**

    * Admin invites are **always** accepted regardless of config
    * Non-admin invites are accepted only if ``auto_accept_invite`` is True
    * All invitations are logged with the decision made

    **Security note:**

    Setting ``auto_accept_invite`` to True allows any user to invite the bot
    to any channel. Consider setting it to False on public networks.
    """
    channel = trigger.args[1]
    # Admin invites are always accepted
    if trigger.admin:
        LOGGER.info(
            'Got invited to "%s" by an admin.', channel)
        bot.join(channel)
    # Non-admin invites require auto_accept_invite enabled
    elif bot.config.admin.auto_accept_invite:
        LOGGER.info(
            'Got invited to "%s"; admin.auto_accept_invite is on', channel)
        bot.join(channel)
    else:
        LOGGER.info(
            'Got invited to "%s"; admin.auto_accept_invite is off.', channel)


@plugin.event('KICK')
@plugin.priority('low')
def hold_ground(bot, trigger):
    """Automatically rejoin channels when kicked based on config.

    This event handler monitors IRC KICK events across all channels. When the
    bot is kicked from a channel, it will automatically rejoin if the
    ``admin.hold_ground`` config option is enabled.

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the KICK event
    :type trigger: :class:`sopel.trigger.Trigger`

    **Config requirements:**

    * ``admin.hold_ground`` (default: False) - Rejoin channels when kicked

    **Behavior:**

    * Only responds to kicks where the bot is the target (ignores other users)
    * If ``hold_ground`` is True, immediately rejoins the channel
    * If ``hold_ground`` is False, stays out of the channel
    * All kick events targeting the bot are logged with the decision made

    .. warning::
        Enabling ``hold_ground`` may cause the bot to be perceived as annoying
        or disruptive if channel operators repeatedly kick it. The bot will
        continue rejoining indefinitely as long as this option is enabled.
        Use with caution and ensure the bot is welcome in configured channels.

    .. note::
        This does not prevent the channel from being removed from ``core.channels``.
        To permanently remove a channel, use the ``part`` command or disable
        ``hold_ground`` before being kicked.
    """
    # Check if the bot is the target of the kick (not another user)
    if bot.nick != trigger.args[1]:
        return

    channel = trigger.sender
    if bot.config.admin.hold_ground:
        LOGGER.info('Got kicked from "%s"; admin.hold_ground is on.', channel)
        bot.join(channel)
    else:
        LOGGER.info('Got kicked from "%s"; admin.hold_ground is off.', channel)


@plugin.require_privmsg
@plugin.require_admin
@plugin.command('mode')
@plugin.priority('low')
def mode(bot, trigger):
    """Set a user mode on the bot.

    This command sets IRC user modes on the bot itself (not channel modes).
    User modes control bot behavior like invisibility (+i), server notices
    (+s), wallops (+w), etc. The specific modes available depend on the IRC
    server.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:** ``.mode <mode>``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``mode`` - User mode string (required), e.g., "+i", "-s", "+iw"

    **Examples:**
        * ``.mode +i`` - Set invisible mode (hide from WHO queries)
        * ``.mode -s`` - Disable server notices
        * ``.mode +B`` - Mark as a bot (on servers that support +B)

    **Error conditions:**

    * If no mode is specified, replies with an error message

    .. note::
        This sets user modes on the bot, not channel modes. Use standard IRC
        commands or channel management tools for channel modes.
    """
    mode = trigger.group(3)
    if not mode:
        bot.reply('What mode should I set?')

    bot.write(('MODE', bot.nick, mode))


def parse_section_option_value(config, trigger):
    """Parse trigger to extract config section, option, and optional value.

    This helper function parses command arguments from ``set`` and ``unset``
    commands to extract the section name, option name, and value. It handles
    both static sections (defined with :class:`~sopel.config.types.StaticSection`)
    and dynamic sections (plain ConfigParser sections).

    :param config: Bot configuration object
    :type config: :class:`sopel.config.Config`
    :param trigger: IRC line trigger containing the command
    :type trigger: :class:`sopel.trigger.Trigger`
    :return: Tuple of ``(section, section_name, static_sec, option, value)``
    :rtype: tuple
    :raises ValueError: If command format is invalid
    :raises InvalidSection: If the specified section does not exist
    :raises InvalidSectionOption: If the specified option does not exist in the section

    **Return tuple elements:**

    * ``section`` - The config section object itself
    * ``section_name`` (str) - Name of the section (e.g., "core", "admin")
    * ``static_sec`` (bool) - True if section is a StaticSection
    * ``option`` (str) - Name of the option within the section
    * ``value`` (str or None) - The value to set, or None if not provided

    **Argument format:**

    * ``section.option`` - Specifies both section and option
    * ``option`` - Option only; assumes "core" section
    * Value is everything after the first space in the command arguments

    **Examples:**
        * Input: ``core.nick MyBot`` → Returns: ``(core_section, "core", True, "nick", "MyBot")``
        * Input: ``nick MyBot`` → Returns: ``(core_section, "core", True, "nick", "MyBot")``
        * Input: ``admin.hold_ground`` → Returns: ``(admin_section, "admin", True, "hold_ground", None)``
    """
    match = trigger.group(3)
    if match is None:
        raise ValueError  # Invalid command

    # Parse section.option format from first argument
    # Default to "core" section if no section specified
    arg1 = match.split('.')
    if len(arg1) == 1:
        section_name, option = "core", arg1[0]
    elif len(arg1) == 2:
        section_name, option = arg1
    else:
        raise ValueError  # invalid command format

    # Verify section exists
    section = getattr(config, section_name, False)
    if not section:
        raise InvalidSection(section_name)
    static_sec = isinstance(section, types.StaticSection)

    # Verify option exists in section (method differs for static vs dynamic sections)
    if static_sec and not hasattr(section, option):
        raise InvalidSectionOption(section_name, option)

    if not static_sec and not config.parser.has_option(section_name, option):
        raise InvalidSectionOption(section_name, option)

    # Extract value from command arguments (everything after first space)
    delim = trigger.group(2).find(' ')
    # Skip any additional whitespace after the first space
    while delim > 0 and delim < len(trigger.group(2)) and trigger.group(2)[delim] == ' ':
        delim = delim + 1

    value = trigger.group(2)[delim:]
    if delim == -1 or delim == len(trigger.group(2)):
        value = None

    return (section, section_name, static_sec, option, value)


@plugin.require_privmsg("This command only works as a private message.")
@plugin.require_admin("This command requires admin privileges.")
@plugin.command('set')
@plugin.example('.set core.owner MyNick')
def set_config(bot, trigger):
    """View or modify bot configuration at runtime.

    This command allows viewing the current value of a config option, or changing
    it to a new value. Changes take effect immediately but are not persisted to
    disk until the ``save`` command is used or the bot restarts.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:**
        * ``.set section.option`` - Display current value
        * ``.set section.option value`` - Set new value

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``section`` - Config section name (optional; defaults to "core")
    * ``option`` - Option name within the section (required)
    * ``value`` - New value to set (optional; displays current value if omitted)

    **Examples:**
        * ``.set nick`` - Display current value of ``core.nick``
        * ``.set core.nick MyBot`` - Change bot's nickname to "MyBot"
        * ``.set admin.hold_ground true`` - Enable auto-rejoin on kick
        * ``.set prefix .`` - Change command prefix to "."

    **Restrictions:**

    * ``core.owner`` and ``core.owner_account`` cannot be changed interactively
      (must edit config file directly for security)
    * Secret/password values are censored when displayed
    * Values must be valid for the option's type (bool, int, string, list, etc.)

    **Error conditions:**

    * Invalid section or option name - displays error message
    * Invalid value type - displays type error with details
    * Restricted option (owner settings) - displays restriction message

    .. note::
        Changes are in-memory only until persisted with the ``save`` command.
        The bot will lose unsaved changes on restart.
    """
    try:
        section, section_name, static_sec, option, value = parse_section_option_value(bot.config, trigger)
    except ValueError:
        bot.say('Usage: {}set section.option [value]'.format(bot.config.core.help_prefix))
        return
    except (InvalidSection, InvalidSectionOption) as exc:
        bot.say(exc.args[1])
        return

    # Get a descriptor class for the option if it's a static section
    descriptor = getattr(section.__class__, option) if static_sec else None

    # Display current value if no value is given
    if not value:
        value = getattr(section, option)

        # Censor secret values (passwords, API keys, etc.)
        if descriptor is not None:
            if getattr(descriptor, 'is_secret', False):
                value = "(secret value censored)"
        elif option.endswith("password") or option.endswith("pass"):
            # Fallback to name-based guessing for backward compatibility
            # TODO: consider a deprecation warning when loading settings
            value = "(password censored)"

        bot.say("%s.%s = %s (%s)" % (section_name, option, value, type(value).__name__))
        return

    # Owner-related settings cannot be modified interactively for security.
    # Any changes to these settings must be made directly in the config file.
    if section_name == 'core' and option in ['owner', 'owner_account']:
        bot.say("Changing '{}.{}' requires manually editing the configuration file."
                .format(section_name, option))
        return

    # Parse and set the new value
    if descriptor is not None:
        try:
            value = descriptor._parse(value, bot.config, section)
        except ValueError as exc:
            bot.say("Can't set attribute: " + str(exc))
            return
    setattr(section, option, value)
    LOGGER.info('%s.%s set successfully.', section_name, option)
    bot.say("OK. Set '{}.{}' successfully.".format(section_name, option))


@plugin.require_privmsg("This command only works as a private message.")
@plugin.require_admin("This command requires admin privileges.")
@plugin.command('unset')
@plugin.example('.unset core.owner')
def unset_config(bot, trigger):
    """Reset a config option to its default value.

    This command resets a config option to the default value specified in its
    definition. This is useful for reverting customizations or clearing optional
    settings. Required options cannot be unset.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:** ``.unset section.option``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Parameters:**

    * ``section`` - Config section name (optional; defaults to "core")
    * ``option`` - Option name within the section (required)

    **Examples:**
        * ``.unset core.prefix`` - Reset command prefix to default (.)
        * ``.unset admin.hold_ground`` - Reset hold_ground to default (False)
        * ``.unset nickname`` - Reset ``core.nickname`` to default

    **Restrictions:**

    * Required options (those without defaults) cannot be unset
    * No value should be provided - the command only takes section.option

    **Error conditions:**

    * Invalid section or option name - displays error message
    * Required option - displays error that option cannot be unset
    * Value provided - displays error about invalid usage

    .. note::
        Changes are in-memory only until persisted with the ``save`` command.
        The bot will lose unsaved changes on restart.
    """
    try:
        section, section_name, static_sec, option, value = parse_section_option_value(bot.config, trigger)
    except ValueError:
        bot.say('Usage: {}unset section.option [value]'.format(bot.config.core.help_prefix))
        return
    except (InvalidSection, InvalidSectionOption) as exc:
        bot.say(exc.args[1])
        return

    if value:
        bot.say('Invalid command; no value should be provided to unset.')
        return

    try:
        setattr(section, option, None)
        LOGGER.info('%s.%s unset.', section_name, option)
        bot.say("Unset '{}.{}' successfully.".format(section_name, option))
    except ValueError:
        bot.say('Cannot unset {}.{}; it is a required option.'.format(section_name, option))


@plugin.require_privmsg
@plugin.require_admin
@plugin.command('save')
@plugin.example('.save')
def save_config(bot, trigger):
    """Persist in-memory config changes to disk.

    This command writes the current state of the bot's configuration object to
    the config file on disk. Any changes made with ``set`` or ``unset`` commands
    are in-memory only until this command is used. This allows testing config
    changes before committing them.

    **Required privilege:** Admin (must be sent via private message)

    **Usage:** ``.save``

    :param bot: Bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param trigger: Trigger object containing the command
    :type trigger: :class:`sopel.trigger.Trigger`

    **Example:**
        * ``.save`` - Write current config to disk

    .. warning::
        This command overwrites the config file. Any comments or formatting in
        the config file may be lost. The config file will be rewritten in a
        standardized format.

    .. note::
        Some configuration changes (like nickname or server settings) may require
        a restart to fully take effect, even after saving.
    """
    bot.config.save()
    LOGGER.info('Configuration file saved.')
    bot.say('Configuration file saved.')
