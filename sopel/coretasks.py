"""Core Sopel plugin that handles IRC protocol functions.

This plugin allows the bot to run without user-facing functionality:

* it handles client capability negotiation
* it handles client auth (both nick auth and server auth)
* it handles connection registration (RPL_WELCOME, RPL_LUSERCLIENT), dealing
  with error cases such as nick already in use
* it tracks known channels & users (join, quit, nick change and other events)
* it manages blocked (ignored) users

This is written as a plugin to make it easier to extend to support more
responses to standard IRC codes without having to shove them all into the
dispatch function in :class:`sopel.bot.Sopel` and making it easier to maintain.

Protocol Handling Overview
---------------------------

This module implements handlers for core IRC protocol events and numerics:

**Connection & Registration:**
    Handles the initial connection sequence (``RPL_WELCOME``, ``RPL_MYINFO``,
    ``RPL_ISUPPORT``), authentication (NickServ, SASL, AuthServ, Q, UserServ),
    and capability negotiation (CAP LS, CAP ACK, CAP NAK, CAP DEL, CAP NEW).

**Channel & User Tracking:**
    Maintains internal state for channels (modes, topics, user lists) and users
    (nicknames, accounts, privileges, away status) by processing JOIN, PART, KICK,
    QUIT, NICK, MODE, WHO, and NAMES messages.

**Throttling & Flood Protection:**
    Implements JOIN throttling to prevent flooding when connecting to many
    channels at startup. The ``throttle_join`` feature queues JOIN events and
    processes them in batches at a configurable rate.

**WHOX Support:**
    Uses WHOX (extended WHO format) when available to efficiently query user
    information including account names. Falls back to standard WHO replies
    on networks without WHOX support.

**Capability Negotiation:**
    Requests and manages IRCv3 capabilities including ``multi-prefix``,
    ``account-tag``, ``account-notify``, ``extended-join``, ``away-notify``,
    ``chghost``, ``cap-notify``, ``server-time``, ``userhost-in-names``,
    ``echo-message``, and ``message-tags``.
"""
# Copyright 2008-2011, Sean B. Palmer (inamidst.com) and Michael Yanovich
# (yanovich.net)
# Copyright © 2012, Elad Alfassa <elad@fedoraproject.org>
# Copyright 2012-2015, Elsie Powell embolalia.com
# Copyright 2019, Florian Strzelecki <florian.strzelecki@gmail.com>
#
# Licensed under the Eiffel Forum License 2.
from __future__ import annotations

import base64
import collections
import copy
import datetime
import functools
import logging
import re
import time

from sopel import config, plugin
from sopel.irc import isupport, utils
from sopel.tools import events, jobs, SopelMemory, target


LOGGER = logging.getLogger(__name__)

CORE_QUERYTYPE = '999'
"""WHOX querytype to indicate requests/responses from coretasks.

This is a unique identifier used in WHOX (extended WHO format) queries to
distinguish responses initiated by coretasks from those initiated by other
plugins or external sources.

When coretasks sends a WHO query with WHOX support, it includes this querytype
in the request (e.g., ``WHO #channel a%nuachtf,999``). The server echoes this
querytype in the response, allowing coretasks to:

1. **Identify its own queries**: Only process WHO responses with this querytype
2. **Ignore external queries**: Skip responses with different querytypes that
   may have different formats or be intended for other plugins
3. **Ensure format correctness**: Confirm the response has the requested fields
   in the expected order

Other plugins should use a different querytype to avoid conflicts with coretasks.

.. seealso::

    WHOX format specification: http://faerion.sourceforge.net/doc/irc/whox.var
"""

MODE_PREFIX_PRIVILEGES = {
    # Maps mode prefix characters to privilege bit flags for user channel modes.
    # These mappings are used when parsing MODE commands and NAMES replies to
    # track which users have which privileges in a channel.
    #
    # RELATIONSHIP TO ISUPPORT PREFIX:
    # The IRC server advertises its supported prefix modes via the ISUPPORT
    # PREFIX parameter (e.g., PREFIX=(qaohv)~&@%+). This tells us:
    #   - Which mode letters exist (q, a, o, h, v)
    #   - Which prefix symbols correspond to each mode (~, &, @, %, +)
    #   - The privilege order (leftmost = highest privilege)
    #
    # This dictionary provides a default mapping from mode letters to Sopel's
    # internal privilege bit flags. When the bot receives ISUPPORT PREFIX, the
    # modeparser is updated with the server's actual supported modes, and this
    # dictionary is used to map those modes to privilege values.
    #
    # PRIVILEGE BIT FLAGS:
    # Each privilege is represented by a bit flag (power of 2) so multiple
    # privileges can be combined using bitwise OR (|) and checked using bitwise
    # AND (&). For example, a user with both OP and VOICE would have:
    #   priv = plugin.OP | plugin.VOICE
    #
    "v": plugin.VOICE,     # +v (voice) - can speak in moderated channels
    "h": plugin.HALFOP,    # +h (halfop) - moderator with limited powers
    "o": plugin.OP,        # +o (op/chanop) - channel operator, full control
    "a": plugin.ADMIN,     # +a (admin/protect) - protected op, can't be kicked by ops
    "q": plugin.OWNER,     # +q (owner/founder) - channel owner, highest privilege
    "y": plugin.OPER,      # +y (oper) - IRC server operator (InspIRCd)
    "Y": plugin.OPER,      # +Y (oper) - IRC server operator (alternative)
}


batched_caps = {}


def setup(bot):
    """Set up the coretasks plugin.

    The setup phase is used to activate the throttle feature to prevent a flood
    of JOIN commands when there are too many channels to join.

    This function initializes:
        * The JOIN events queue for throttling
        * A periodic job to process queued JOINs (if throttling is enabled)
    """
    # Initialize the JOIN events queue as a deque for efficient FIFO operations
    # deque.popleft() is O(1), making it ideal for queue processing
    bot.memory['join_events_queue'] = collections.deque()

    # Manage JOIN flood protection
    if bot.settings.core.throttle_join:
        # Ensure wait interval is at least 1 second to prevent excessive scheduler load
        wait_interval = max(bot.settings.core.throttle_wait, 1)

        # Create a periodic job that runs every wait_interval seconds
        # This job will process up to throttle_join channels per execution
        job = jobs.Job(
            [wait_interval],              # Run every wait_interval seconds
            plugin='coretasks',            # Associate job with this plugin
            label='throttle_join',         # Unique label for job identification
            handler=_join_event_processing,  # Function to call on each execution
            threaded=True,                 # Run in separate thread to avoid blocking
            doc=None,                      # No user-facing documentation needed
        )

        # Register the job with the bot's scheduler to start execution
        bot.scheduler.register(job)


def shutdown(bot):
    """Clean up coretasks-related values in the bot's memory."""
    try:
        bot.memory['join_events_queue'].clear()
    except KeyError:
        pass


def _join_event_processing(bot):
    """Process a batch of JOIN event from the ``join_events_queue`` queue.

    This function implements JOIN flood protection by processing queued JOIN
    events in controlled batches. Every time this function is executed, it
    processes at most ``throttle_join`` JOIN events.

    **JOIN Flood Protection Mechanism:**

    When the bot connects and needs to join many channels, sending MODE and WHO
    commands for each channel immediately can trigger server flood protection,
    causing the bot to be disconnected or throttled. To prevent this:

    1. **Queuing**: When ``throttle_join`` is enabled, channel JOINs are added
       to ``join_events_queue`` instead of being processed immediately
    2. **Batching**: This function runs periodically (via scheduled job) and
       processes a fixed number of channels per execution
    3. **Rate Limiting**: The job interval (``throttle_wait``) controls how
       often batches are processed, effectively rate-limiting the MODE/WHO flood
    4. **Batch Sizing**: The batch size (``throttle_join``) controls how many
       channels are processed per interval

    For each JOIN event processed, this function sends:
        * **MODE request**: Queries the channel's modes (e.g., +nt, +k key, +l limit)
        * **WHO request**: Queries information about users in the channel
          (nicknames, privileges, accounts, hostmasks)

    **Example:** With ``throttle_join = 3`` and ``throttle_wait = 5``, the bot
    will process 3 channels every 5 seconds, allowing it to join 100 channels
    in ~167 seconds without triggering flood protection.

    :param bot: Sopel bot instance with ``join_events_queue`` in memory
    :type bot: :class:`sopel.bot.Sopel`
    """
    # Determine batch size: process at most throttle_join channels per call
    # Minimum of 1 ensures we always process at least one channel if available
    batch_size = max(bot.settings.core.throttle_join, 1)

    for _ in range(batch_size):
        try:
            # Pop the oldest channel from the queue (FIFO order)
            channel = bot.memory['join_events_queue'].popleft()
        except IndexError:
            # Queue is empty, no more channels to process in this batch
            break

        LOGGER.debug("Sending MODE and WHO after channel JOIN: %s", channel)

        # Request channel modes to populate bot.channels[channel].modes
        bot.write(["MODE", channel])

        # Request user information to populate user list and privileges
        _send_who(bot, channel)


def auth_after_register(bot):
    """Do NickServ/AuthServ auth.

    :param bot: a connected Sopel instance
    :type bot: :class:`sopel.bot.Sopel`

    This function can be used, **after** the bot is connected, to handle one of
    these auth methods:

    * ``nickserv``: send a private message to the NickServ service
    * ``authserv``: send an ``AUTHSERV`` command
    * ``Q``: send an ``AUTH`` command
    * ``userserv``: send a private message to the UserServ service

    .. important::

        If ``core.auth_method`` is set, then ``core.nick_auth_method`` will be
        ignored. If none is set, then this function does nothing.

    """
    if bot.config.core.auth_method:
        auth_method = bot.config.core.auth_method
        auth_username = bot.config.core.auth_username
        auth_password = bot.config.core.auth_password
        auth_target = bot.config.core.auth_target
    elif bot.config.core.nick_auth_method:
        auth_method = bot.config.core.nick_auth_method
        auth_username = (bot.config.core.nick_auth_username or
                         bot.config.core.nick)
        auth_password = bot.config.core.nick_auth_password
        auth_target = bot.config.core.nick_auth_target
    else:
        return

    # nickserv-based auth method needs to check for current nick
    if auth_method == 'nickserv':
        if bot.nick != bot.make_identifier(bot.settings.core.nick):
            LOGGER.warning("Sending nickserv GHOST command.")
            bot.say(
                'GHOST %s %s' % (bot.settings.core.nick, auth_password),
                auth_target or 'NickServ')
        else:
            bot.say('IDENTIFY %s' % auth_password, auth_target or 'NickServ')

    # other methods use account instead of nick
    elif auth_method == 'authserv':
        bot.write(('AUTHSERV', 'auth', auth_username, auth_password))
    elif auth_method == 'Q':
        bot.write(('AUTH', auth_username, auth_password))
    elif auth_method == 'userserv':
        bot.say("LOGIN %s %s" % (auth_username, auth_password),
                auth_target or 'UserServ')


def _execute_perform(bot):
    """Execute commands specified to perform on IRC server connect.

    This function executes the list of commands that can be found in the
    ``core.commands_on_connect`` setting. It automatically replaces any
    ``$nickname`` placeholder in the command with the bot's configured nick.
    """
    if not bot.connection_registered:
        # How did you even get this command, bot?
        raise Exception('Bot must be connected to server to perform commands.')

    commands = bot.config.core.commands_on_connect
    count = len(commands)

    if not count:
        LOGGER.info("No custom command to execute.")
        return

    LOGGER.info("Executing %d custom commands.", count)
    for i, command in enumerate(commands, 1):
        command = command.replace('$nickname', bot.config.core.nick)
        LOGGER.debug("Executing custom command [%d/%d]: %s", i, count, command)
        bot.write((command,))


@plugin.event(events.ERR_NICKNAMEINUSE)
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def on_nickname_in_use(bot, trigger):
    """Change the bot's nick when the current one is already in use.

    This can be triggered when the bot disconnects then reconnects before the
    server can notice a client timeout. Other reasons include mischief,
    trolling, and obviously, PEBKAC.

    This will change the current nick by adding a trailing ``_``. If the bot
    sees that a user with its configured nick disconnects (see ``QUIT`` event
    handling), the bot will try to regain it.
    """
    LOGGER.error(
        "Nickname already in use! (Nick: %s; Sender: %s; Args: %r)",
        trigger.nick,
        trigger.sender,
        trigger.args,
    )
    bot.change_current_nick(bot.nick + '_')


@plugin.require_privmsg("This command only works as a private message.")
@plugin.require_admin("This command requires admin privileges.")
@plugin.commands('execute')
def execute_perform(bot, trigger):
    """Execute commands specified to perform on IRC server connect.

    This allows a bot owner or admin to force the execution of commands
    that are automatically performed when the bot connects.
    """
    _execute_perform(bot)


@plugin.event(events.RPL_WELCOME, events.RPL_LUSERCLIENT)
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def startup(bot, trigger):
    """Do tasks related to connecting to the network.

    ``001 RPL_WELCOME`` is from RFC2812 and is the first message that is sent
    after the connection has been registered on the network.

    ``251 RPL_LUSERCLIENT`` is a mandatory message that is sent after the
    client connects to the server in RFC1459. RFC2812 does not require it and
    all networks might not send it. We support both.

    If ``sopel.irc.AbstractBot.connection_registered`` is set, this function
    does nothing and returns immediately. Otherwise, the flag is set and the
    function proceeds normally to:

    1. trigger auth method
    2. set bot's ``MODE`` (from ``core.modes``)
    3. join channels (or queue them to join later)
    4. check for security when the ``account-tag`` capability is enabled
    5. execute custom commands
    """
    if bot.connection_registered:
        return

    # nick shenanigans are serious business, but fortunately RPL_WELCOME
    # includes the actual nick used by the server after truncation, removal
    # of invalid characters, etc. so we can check for such shenanigans
    if trigger.event == events.RPL_WELCOME:
        if bot.nick != trigger.args[0]:
            # setting modes below is just one of the things that won't work
            # as expected if the conditions for running this block are met
            privmsg = (
                "Hi, I'm your bot, %s. The IRC server didn't assign me the "
                "nick you configured. This can cause problems for me, and "
                "make me do weird things. You'll probably want to stop me, "
                "figure out why my nick isn't acceptable, and fix that before "
                "starting me again." % bot.nick
            )
            debug_msg = (
                "RPL_WELCOME indicated the server did not accept the bot's "
                "configured nickname. Requested '%s'; got '%s'. This can "
                "cause unexpected behavior. Please modify the configuration "
                "and restart the bot." % (bot.nick, trigger.args[0])
            )
            LOGGER.critical(debug_msg)
            bot.say(privmsg, bot.config.core.owner)

    # set flag
    bot.connection_registered = True

    # handle auth method
    auth_after_register(bot)

    # set bot's MODE
    modes = bot.config.core.modes
    if modes:
        if not modes.startswith(('+', '-')):
            # Assume "+" by default.
            modes = '+' + modes
        bot.write(('MODE', bot.nick, modes))

    # join channels
    # Initialize retry_join memory to track failed +R channel join attempts
    bot.memory['retry_join'] = SopelMemory()

    channels = bot.config.core.channels
    if not channels:
        LOGGER.info("No initial channels to JOIN.")
    elif bot.config.core.throttle_join:
        # JOIN throttling is enabled: send JOINs in batches with delays
        # This prevents the initial burst of JOINs from triggering flood protection
        throttle_rate = int(bot.config.core.throttle_join)
        throttle_wait = max(bot.config.core.throttle_wait, 1)
        channels_joined = 0

        LOGGER.info(
            "Joining %d channels (with JOIN throttle ON); "
            "this may take a moment.",
            len(channels))

        for channel in channels:
            channels_joined += 1
            # Every throttle_rate JOINs, pause for throttle_wait seconds
            # Example: if throttle_rate=10 and throttle_wait=2, sleep after every 10th JOIN
            if not channels_joined % throttle_rate:
                LOGGER.debug(
                    "Waiting %ds before next JOIN batch.",
                    throttle_wait)
                time.sleep(throttle_wait)
            # Send the JOIN command to the server
            # The resulting JOIN event will be queued for processing by _join_event_processing()
            bot.join(channel)
    else:
        # JOIN throttling is disabled: send all JOINs immediately
        # MODE and WHO requests will be sent directly in track_join() handler
        LOGGER.info(
            "Joining %d channels (with JOIN throttle OFF); "
            "this may take a moment.",
            len(channels))

        for channel in bot.config.core.channels:
            bot.join(channel)

    # warn for insecure auth method if necessary
    if (not bot.config.core.owner_account and
            'account-tag' in bot.enabled_capabilities and
            '@' not in bot.config.core.owner):
        msg = (
            "This network supports using network services to identify you as "
            "my owner, rather than just matching your nickname. This is much "
            "more secure. If you'd like to do this, make sure you're logged in "
            "and reply with \"{}useserviceauth\""
        ).format(bot.config.core.help_prefix)
        bot.say(msg, bot.config.core.owner)

    # execute custom commands
    _execute_perform(bot)


@plugin.event(events.RPL_ISUPPORT)
@plugin.thread(False)
@plugin.unblockable
@plugin.rule('are supported by this server')
@plugin.priority('medium')
def handle_isupport(bot, trigger):
    """Handle ``RPL_ISUPPORT`` events."""
    # remember if certain actionable tokens are known to be supported,
    # before parsing RPL_ISUPPORT
    botmode_support = 'BOT' in bot.isupport
    namesx_support = 'NAMESX' in bot.isupport
    uhnames_support = 'UHNAMES' in bot.isupport
    casemapping_support = 'CASEMAPPING' in bot.isupport
    chantypes_support = 'CHANTYPES' in bot.isupport

    # parse ISUPPORT message from server
    parameters = {}
    for arg in trigger.args:
        try:
            key, value = isupport.parse_parameter(arg)
            parameters[key] = value
        except ValueError:
            # ignore malformed parameter: log a warning and continue
            LOGGER.warning("Unable to parse ISUPPORT parameter: %r", arg)

    bot._isupport = bot._isupport.apply(**parameters)

    # update bot's mode parser
    if 'CHANMODES' in bot.isupport:
        bot.modeparser.chanmodes = bot.isupport.CHANMODES

    if 'PREFIX' in bot.isupport:
        bot.modeparser.privileges = set(bot.isupport.PREFIX.keys())

    # rebuild nick when CASEMAPPING and/or CHANTYPES are set
    if any((
        # was CASEMAPPING support status updated?
        not casemapping_support and 'CASEMAPPING' in bot.isupport,
        # was CHANTYPES support status updated?
        not chantypes_support and 'CHANTYPES' in bot.isupport,
    )):
        # these parameters change how the bot makes Identifiers
        # since bot.nick is an Identifier, it must be rebuilt
        bot.rebuild_nick()

    # was BOT mode support status updated?
    if not botmode_support and 'BOT' in bot.isupport:
        # yes it was! set our mode unless the config overrides it
        botmode = bot.isupport['BOT']
        if botmode not in bot.config.core.modes:
            bot.write(('MODE', bot.nick, '+' + botmode))
    # was NAMESX support status updated?
    if not namesx_support and 'NAMESX' in bot.isupport:
        # yes it was!
        if 'multi-prefix' not in bot.server_capabilities:
            # and the server doesn't have the multi-prefix capability
            # so we can ask the server to use the NAMESX feature
            bot.write(('PROTOCTL', 'NAMESX'))
    # was UHNAMES support status updated?
    if not uhnames_support and 'UHNAMES' in bot.isupport:
        # yes it was!
        if 'userhost-in-names' not in bot.server_capabilities:
            # and the server doesn't have the userhost-in-names capability
            # so we should ask for UHNAMES instead
            bot.write(('PROTOCTL', 'UHNAMES'))


@plugin.event(events.RPL_MYINFO)
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def parse_reply_myinfo(bot, trigger):
    """Handle ``RPL_MYINFO`` events."""
    # keep <client> <servername> <version> only
    # the trailing parameters (mode types) should be read from ISUPPORT
    bot._myinfo = utils.MyInfo(*trigger.args[0:3])

    LOGGER.info(
        "Received RPL_MYINFO from server: %s, %s, %s",
        bot._myinfo.client,
        bot._myinfo.servername,
        bot._myinfo.version,
    )


@plugin.require_privmsg()
@plugin.require_owner()
@plugin.commands('useserviceauth')
def enable_service_auth(bot, trigger):
    """Set owner's account from an authenticated owner.

    This command can be used to automatically configure ``core.owner_account``
    when the owner is known and has a registered account, but the bot doesn't
    have ``core.owner_account`` configured.

    This doesn't work if the ``account-tag`` capability is not available.
    """
    if bot.config.core.owner_account:
        return
    if 'account-tag' not in bot.enabled_capabilities:
        bot.say('This server does not fully support services auth, so this '
                'command is not available.')
        return
    if not trigger.account:
        bot.say('You must be logged in to network services before using this '
                'command.')
        return
    bot.config.core.owner_account = trigger.account
    bot.config.save()
    bot.say('Success! I will now use network services to identify you as my '
            'owner.')
    LOGGER.info(
        "User %s set %s as owner account.",
        trigger.nick,
        trigger.account,
    )


@plugin.event(events.ERR_NOCHANMODES)
@plugin.priority('medium')
def retry_join(bot, trigger):
    """Give NickServ enough time to identify on a +R channel.

    Give NickServ enough time to identify, and retry rejoining an
    identified-only (+R) channel. Maximum of ten rejoin attempts.
    """
    channel = trigger.args[1]
    if channel in bot.memory['retry_join'].keys():
        bot.memory['retry_join'][channel] += 1
        if bot.memory['retry_join'][channel] > 10:
            LOGGER.warning("Failed to join %s after 10 attempts.", channel)
            return
        LOGGER.info(
            "Rejoining channel %r failed, will retry in 6s.",
            str(channel))
        time.sleep(6)
    else:
        bot.memory['retry_join'][channel] = 0

    attempt = bot.memory['retry_join'][channel] + 1
    LOGGER.info(
        "Trying to rejoin channel %r (attempt %d/10)",
        str(channel), attempt)
    bot.join(channel)


@plugin.rule('(.*)')
@plugin.event(events.RPL_NAMREPLY)
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def handle_names(bot, trigger):
    """Handle NAMES responses.

    This function keeps track of users' privileges when Sopel joins channels.
    """
    # TODO specific to one channel type. See issue 281.
    channels = re.search(r'(#\S*)', trigger.raw)
    if not channels:
        return
    channel = bot.make_identifier(channels.group(1))
    if channel not in bot.channels:
        bot.channels[channel] = target.Channel(
            channel,
            identifier_factory=bot.make_identifier,
        )

    # This could probably be made flexible in the future, but I don't think
    # it'd be worth it.
    # If this ever needs to be updated, remember to change the mode handling in
    # the WHO-handler functions below, too.
    mapping = {
        "+": plugin.VOICE,
        "%": plugin.HALFOP,
        "@": plugin.OP,
        "&": plugin.ADMIN,
        "~": plugin.OWNER,
        "!": plugin.OPER,
    }

    uhnames = 'UHNAMES' in bot.isupport
    userhost_in_names = 'userhost-in-names' in bot.enabled_capabilities

    names = trigger.split()
    for name in names:
        if uhnames or userhost_in_names:
            name, mask = name.rsplit('!', 1)
            username, hostname = mask.split('@', 1)
        else:
            username = hostname = None

        priv = 0
        for prefix, value in mapping.items():
            if prefix in name:
                priv = priv | value

        nick = bot.make_identifier(name.lstrip(''.join(mapping.keys())))
        user = bot.users.get(nick)
        if user is None:
            # The username/hostname will be included in a NAMES reply only if
            # userhost-in-names is available. We can use them if present.
            # Fortunately, the user should already exist in bot.users by the
            # time this code runs, so this is 99.9% ass-covering.
            user = target.User(nick, username, hostname)
            bot.users[nick] = user
        bot.channels[channel].add_user(user, privs=priv)


@plugin.rule('(.*)')
@plugin.event('MODE')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def track_modes(bot, trigger):
    """Track changes from received MODE commands."""
    _parse_modes(bot, trigger.args)


@plugin.priority('high')
@plugin.event(events.RPL_CHANNELMODEIS)
@plugin.thread(False)
@plugin.unblockable
def initial_modes(bot, trigger):
    """Populate channel modes from response to MODE request sent after JOIN."""
    _parse_modes(bot, trigger.args[1:], clear=True)


def _parse_modes(bot, args, clear=False):
    """Parse MODE message and apply changes to internal state.

    Sopel, by default, doesn't know how to parse other types than A, B, C, and
    D, and only a preset of privileges.

    .. seealso::

        Parsing mode messages can be tricky and complicated to understand. In
        any case it is better to read the IRC specifications about channel
        modes at https://modern.ircdocs.horse/#channel-mode

    """
    channel_name = bot.make_identifier(args[0])
    if channel_name.is_nick():
        # We don't do anything with user modes
        LOGGER.debug("Ignoring user modes: %r", args)
        return

    channel = bot.channels[channel_name]

    # Unreal 3 sometimes sends an extraneous trailing space. If we're short an
    # arg, we'll find out later.
    if args[-1] == "":
        args.pop()
    # If any args are still empty, that's something we may not be prepared for,
    # but let's continue anyway hoping they're trailing / not important.
    if len(args) < 2 or not all(args):
        LOGGER.debug(
            "The server sent a possibly malformed MODE message: %r", args)

    # parse the modestring with the parameters
    modeinfo = bot.modeparser.parse(args[1], tuple(args[2:]))

    # set, unset, or update channel's modes based on the mode type
    # modeinfo.modes contains only the valid parsed modes
    # coretask can handle type A, B, C, and D only
    modes = {} if clear else copy.deepcopy(channel.modes)
    for letter, mode, is_added, param in modeinfo.modes:
        if letter == 'A':
            # type A is a multi-value mode and always requires a parameter
            if mode not in modes:
                modes[mode] = set()
            if is_added:
                modes[mode].add(param)
            elif param in modes[mode]:
                modes[mode].remove(param)
                # remove mode if empty
                if not modes[mode]:
                    modes.pop(mode)
        elif letter == 'B':
            # type B is a single-value mode and always requires a parameter
            if is_added:
                modes[mode] = param
            elif mode in modes:
                modes.pop(mode)
        elif letter == 'C':
            # type C is a single-value mode and requires a parameter when added
            if is_added:
                modes[mode] = param
            elif mode in modes:
                modes.pop(mode)
        elif letter == 'D':
            # type D is a flag (True or False) and doesn't have a parameter
            if is_added:
                modes[mode] = True
            elif mode in modes:
                modes.pop(mode)

    # atomic change of channel's modes
    channel.modes = modes

    # update user privileges in channel
    # modeinfo.privileges contains only the valid parsed privileges
    # Format: [(privilege_letter, is_added, target_nick), ...]
    # Example: [('o', True, 'Alice'), ('v', False, 'Bob')]
    for privilege, is_added, param in modeinfo.privileges:
        # User privilege modes always have a parameter: the target user's nick
        nick = bot.make_identifier(param)

        # Get current privileges for this user (default to 0 = no privileges)
        priv = channel.privileges.get(nick, 0)

        # Look up the privilege bit flag for this mode letter
        # Example: 'o' -> plugin.OP (bit flag value)
        value = MODE_PREFIX_PRIVILEGES[privilege]

        if is_added:
            # Add privilege using bitwise OR
            # Example: If user has VOICE (0b001) and gains OP (0b100),
            #          result is 0b101 (both VOICE and OP)
            priv = priv | value
        else:
            # Remove privilege using bitwise AND with complement
            # Example: If user has OP|VOICE (0b101) and loses OP (0b100),
            #          result is 0b001 (only VOICE)
            priv = priv & ~value

        # Update the user's privilege value in the channel
        channel.privileges[nick] = priv

    # log ignored modes (modes Sopel doesn't know how to handle)
    if modeinfo.ignored_modes:
        LOGGER.warning(
            "Unknown MODE message, sending WHO. Message was: %r",
            args,
        )
        # send a WHO message to ensure we didn't miss anything
        _send_who(bot, channel_name)

    # log leftover parameters (too many arguments)
    if modeinfo.leftover_params:
        LOGGER.warning(
            "Too many arguments received for MODE: args=%r chanmodes=%r",
            args,
            bot.modeparser.chanmodes,
        )

    LOGGER.info("Updated mode for channel: %s", channel.name)
    LOGGER.debug("Channel %r mode: %r", str(channel.name), channel.modes)


@plugin.event('NICK')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def track_nicks(bot, trigger):
    """Track nickname changes and maintain channel/user state accordingly.

    When a user changes their nickname, the bot must update:
        * User object mapping in ``bot.users`` (old nick -> new nick)
        * Channel user lists in ``bot.channels[channel]`` for all shared channels
        * Privilege mappings for the user in each channel

    **Special case:** If the bot's own nick changes unexpectedly, this is
    usually a configuration error (e.g., nick protected by NickServ) and
    requires operator intervention.
    """
    old = trigger.nick
    new = bot.make_identifier(trigger)

    # Detect if the bot's own nickname was changed (usually by server/services)
    if old == bot.nick and new != bot.nick:
        # This is problematic because:
        # 1. The bot expects to use its configured nickname
        # 2. Some plugins may hardcode the expected nick
        # 3. Authentication may fail with the wrong nick
        # 4. This usually indicates NickServ protection or conflicting registration
        privmsg = (
            "Hi, I'm your bot, %s. Something has made my nick change. This "
            "can cause some problems for me, and make me do weird things. "
            "You'll probably want to restart me, and figure out what made "
            "that happen so you can stop it happening again. (Usually, it "
            "means you tried to give me a nick that's protected by NickServ.)"
        ) % bot.nick
        debug_msg = (
            "Nick changed by server. This can cause unexpected behavior. "
            "Please restart the bot."
        )
        LOGGER.critical(debug_msg)
        bot.say(privmsg, bot.config.core.owner)
        return

    # Update all channels where this user is present
    # Channel.rename_user() updates the user list and privilege mappings
    for channel in bot.channels.values():
        channel.rename_user(old, new)

    # Update the global user mapping: move User object from old nick to new nick
    # This preserves user metadata (account, away status, etc.)
    if old in bot.users:
        bot.users[new] = bot.users.pop(old)

    LOGGER.info("User named %r is now known as %r.", old, str(new))


@plugin.rule('(.*)')
@plugin.event('PART')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def track_part(bot, trigger):
    """Track users leaving channels."""
    nick = trigger.nick
    channel = trigger.sender
    _remove_from_channel(bot, nick, channel)
    LOGGER.info("User %r left a channel: %s", str(nick), channel)


@plugin.event('KICK')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def track_kick(bot, trigger):
    """Track users kicked from channels."""
    nick = bot.make_identifier(trigger.args[1])
    channel = trigger.sender
    _remove_from_channel(bot, nick, channel)
    LOGGER.info(
        "User %r got kicked by %r from a channel: %s",
        str(nick),
        str(trigger.nick),
        channel,
    )


def _remove_from_channel(bot, nick, channel):
    if nick == bot.nick:
        bot.channels.pop(channel, None)

        lost_users = []
        for nick_, user in bot.users.items():
            user.channels.pop(channel, None)
            if not user.channels:
                lost_users.append(nick_)
        for nick_ in lost_users:
            bot.users.pop(nick_, None)
    else:
        user = bot.users.get(nick)
        if user and channel in user.channels:
            bot.channels[channel].clear_user(nick)
            if not user.channels:
                bot.users.pop(nick, None)


def _send_who(bot, channel):
    """Send a WHO query for the specified channel using WHOX if available.

    WHOX (extended WHO format) allows clients to request specific fields in
    WHO replies and tag responses with a querytype for identification.

    **Standard WHO reply format (RFC 1459):**
        ``352 <client> <channel> <user> <host> <server> <nick> <flags> :<hopcount> <realname>``

    **WHOX reply format (with querytype 999):**
        ``354 <client> 999 <channel> <nick> <user> <account> <host> <flags>``

    The WHOX format provides:
        * **Querytype identification**: Distinguishes our queries from others
        * **Account information**: User's services account name (not in standard WHO)
        * **Efficient parsing**: Only requested fields are included
        * **Consistent field order**: Fields appear in the order we specify

    :param bot: Sopel bot instance
    :type bot: :class:`sopel.bot.Sopel`
    :param str channel: Channel name to query
    """
    if 'WHOX' in bot.isupport:
        # WHOX syntax: WHO <mask> <flags>%<fields>,<querytype>
        #
        # Flags: 'a' = request all matching users (not just ops)
        # Fields requested:
        #   n = channel name
        #   u = username (~user part of hostmask)
        #   a = account name (services account, '*' if not logged in, '0' if unknown)
        #   c = client type/querytype (echoes our CORE_QUERYTYPE for identification)
        #   h = hostname (host part of hostmask)
        #   t = querytype token (old format, included for compatibility)
        #   f = flags (G/H for away/here, plus privilege prefixes like @ for op)
        #
        # Querytype: CORE_QUERYTYPE ('999') - unique identifier to recognize our queries
        #
        # The server echoes this querytype in RPL_WHOSPCRPL (354) responses,
        # allowing us to distinguish our queries from those initiated by plugins
        # or other clients. Responses with different querytypes are ignored by
        # recv_whox() to prevent processing incorrectly formatted data.
        #
        # See: http://faerion.sourceforge.net/doc/irc/whox.var
        bot.write(['WHO', channel, 'a%nuachtf,' + CORE_QUERYTYPE])
    else:
        # Fallback to standard WHO for networks without WHOX support
        # Standard WHO replies (RPL_WHOREPLY, 352) have less information and
        # don't include account names, but we still need to track basic user info
        bot.write(['WHO', channel])

    # Update the channel's last WHO timestamp for periodic WHO scheduling
    # The _periodic_send_who() job uses this to determine which channel needs
    # an update most urgently (e.g., to detect away status changes)
    channel_id = bot.make_identifier(channel)
    bot.channels[channel_id].last_who = datetime.datetime.utcnow()


@plugin.interval(30)
def _periodic_send_who(bot):
    """Periodically send a WHO request to keep user information up-to-date."""
    if 'away-notify' in bot.enabled_capabilities:
        # WHO not needed to update 'away' status
        return

    # Loops through the channels to find the one that has the longest time since the last WHO
    # request, and issues a WHO request only if the last request for the channel was more than
    # 120 seconds ago.
    who_trigger_time = datetime.datetime.utcnow() - datetime.timedelta(seconds=120)
    selected_channel = None
    for channel_name, channel in bot.channels.items():
        if channel.last_who is None:
            # WHO was never sent yet to this channel: stop here
            selected_channel = channel_name
            break
        if channel.last_who < who_trigger_time:
            # this channel's last who request is the most outdated one at the moment
            selected_channel = channel_name
            who_trigger_time = channel.last_who

    if selected_channel is not None:
        # selected_channel's last who is either none or the oldest valid
        LOGGER.debug("Sending WHO for channel: %s", selected_channel)
        _send_who(bot, selected_channel)


@plugin.event('JOIN')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def track_join(bot, trigger):
    """Track users joining channels and initialize channel state.

    When a user joins a channel, the bot must:
        * Create a Channel object if this is a new channel
        * Create or update a User object for the joining user
        * Add the user to the channel's user list
        * Send WHO/MODE queries to populate channel state (if bot joined)
        * Process extended-join account information (if available)

    **Two scenarios:**

    1. **Bot joins channel**: Send MODE to query channel modes, and WHO to
       query all users in the channel (nicknames, accounts, privileges)
    2. **Other user joins**: Add user to channel's user list, extracting
       account from extended-join if available

    When a user joins a channel, the bot will send (or queue) a ``WHO`` command
    to know more about said user (privileges, modes, etc.).
    """
    channel = trigger.sender

    # Check if this is a new channel for the bot
    if channel not in bot.channels:
        # Create Channel object to track state (modes, topic, users, privileges)
        bot.channels[channel] = target.Channel(
            channel,
            identifier_factory=bot.make_identifier,
        )

    # Determine if the bot itself just joined this channel
    if trigger.nick == bot.nick:
        LOGGER.info("Channel joined: %s", channel)

        # Record when we joined for tracking purposes
        bot.channels[channel].join_time = trigger.time

        # Handle JOIN flood protection
        if bot.settings.core.throttle_join:
            # Queue this channel for later MODE/WHO processing
            # The _join_event_processing() job will process it in batches
            LOGGER.debug("JOIN event added to queue for channel: %s", channel)
            bot.memory['join_events_queue'].append(channel)
        else:
            # No throttling: immediately send MODE and WHO
            LOGGER.debug("Send MODE and direct WHO for channel: %s", channel)
            # MODE query populates channel modes (+nt, +k key, +l limit, etc.)
            bot.write(["MODE", channel])
            # WHO query populates user list with accounts, privileges, etc.
            _send_who(bot, channel)
    else:
        # Another user joined a channel we're in
        LOGGER.info(
            "Channel %r joined by user: %s",
            str(channel), trigger.nick)

    # Create or update User object for the joining user
    user = bot.users.get(trigger.nick)
    if user is None:
        # New user we haven't seen before: create User object
        # trigger.user and trigger.host come from the JOIN hostmask
        user = target.User(trigger.nick, trigger.user, trigger.host)
        bot.users[trigger.nick] = user

    # Add user to the channel's user list (with no privileges initially)
    # Privileges will be populated by WHO response or NAMES reply
    bot.channels[channel].add_user(user)

    # Process extended-join account information if available
    # Extended-join format: JOIN #channel accountname :realname
    # Standard join format: JOIN #channel
    if len(trigger.args) > 1 and trigger.args[1] != '*' and (
            'account-notify' in bot.enabled_capabilities and
            'extended-join' in bot.enabled_capabilities):
        # Extended-join provides the user's services account in trigger.args[1]
        # '*' means not logged in, otherwise it's the account name
        user.account = trigger.args[1]


@plugin.event('QUIT')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def track_quit(bot, trigger):
    """Track when users quit channels."""
    for channel in bot.channels.values():
        channel.clear_user(trigger.nick)
    bot.users.pop(trigger.nick, None)

    LOGGER.info("User quit: %s", trigger.nick)

    configured_nick = bot.make_identifier(bot.settings.core.nick)
    if trigger.nick == configured_nick and trigger.nick != bot.nick:
        # old nick is now available, let's change nick again
        bot.change_current_nick(bot.settings.core.nick)
        auth_after_register(bot)


@plugin.event('CAP')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def receive_cap_list(bot, trigger):
    """Handle client capability negotiation.

    This function processes CAP (Client Capability) messages from the IRC server
    during IRCv3 capability negotiation. CAP negotiation allows the client and
    server to agree on protocol extensions beyond basic IRC.

    **CAP Protocol Flow:**

    1. Client sends: ``CAP LS 302`` (request capability list, version 3.2)
    2. Server replies: ``CAP * LS :multi-prefix sasl=PLAIN account-notify ...``
    3. Client sends: ``CAP REQ :multi-prefix account-notify``
    4. Server replies: ``CAP * ACK :multi-prefix account-notify`` (accepted)
                   or ``CAP * NAK :multi-prefix account-notify`` (rejected)
    5. Client sends: ``CAP END`` (finish negotiation)

    Additionally, during an active connection:
        * ``CAP NEW``: Server adds a new capability (e.g., after module load)
        * ``CAP DEL``: Server removes a capability (e.g., before module unload)

    .. seealso::

        IRCv3 CAP specification: https://ircv3.net/specs/extensions/capability-negotiation.html
    """
    # Strip capability modifiers: '-' (disable), '=' (sticky), '~' (ack)
    cap = trigger.strip('-=~')

    # Server is listing capabilities (CAP LS or CAP LS 302)
    if trigger.args[1] == 'LS':
        receive_cap_ls_reply(bot, trigger)

    # Server denied CAP REQ (capability not available or conflicts with another)
    elif trigger.args[1] == 'NAK':
        entry = bot._cap_reqs.get(cap, None)
        # Check if this capability was requested via bot.cap_req()
        if entry:
            for req in entry:
                # If the request was mandatory ('=' prefix) or prohibit ('-' prefix)
                # and a failure callback was provided, invoke it
                if req.prefix and req.failure:
                    # Call the failure callback to handle the rejection
                    req.failure(bot, req.prefix + cap)

    # Server is removing a capability (CAP DEL - IRCv3.2 cap-notify)
    # This can happen if server unloads a module providing the capability
    elif trigger.args[1] == 'DEL':
        entry = bot._cap_reqs.get(cap, None)
        # Check if this capability was requested via bot.cap_req()
        if entry:
            for req in entry:
                # If the request wasn't prohibit ('-') and has a failure callback
                if req.prefix != '-' and req.failure:
                    # Call the failure callback to handle capability removal
                    req.failure(bot, req.prefix + cap)

    # Server is adding new capability (CAP NEW - IRCv3.2 cap-notify)
    # This can happen if server loads a module providing new capabilities
    elif trigger.args[1] == 'NEW':
        entry = bot._cap_reqs.get(cap, None)
        # Check if this capability was requested via bot.cap_req()
        if entry:
            for req in entry:
                # If the request wasn't prohibit ('-'), request the new capability
                if req.prefix != '-':
                    # Send CAP REQ to enable the newly available capability
                    bot.write(('CAP', 'REQ', req.prefix + cap))

    # Server is acknowledging a capability (CAP ACK - capability enabled)
    elif trigger.args[1] == 'ACK':
        # Parse capability list (can be multiple caps in one ACK)
        caps = trigger.args[2].split()
        for cap in caps:
            # Clean up capability modifiers
            cap.strip('-~= ')

            # Add to the set of enabled capabilities for runtime checks
            bot.enabled_capabilities.add(cap)

            # Execute success callbacks for this capability
            entry = bot._cap_reqs.get(cap, [])
            for req in entry:
                if req.success:
                    req.success(bot, req.prefix + trigger)

            # Special handling for SASL capability
            # TODO: This should be migrated to use bot.cap_req() like other caps
            if cap == 'sasl':
                try:
                    # Initiate SASL authentication flow
                    receive_cap_ack_sasl(bot)
                except config.ConfigurationError as error:
                    LOGGER.error(str(error))
                    bot.quit('Wrong SASL configuration.')


def receive_cap_ls_reply(bot, trigger):
    """Process CAP LS (capability list) replies from the server.

    CAP LS is sent by the server to list available capabilities. The server may
    send multiple lines if the capability list is long (multi-line reply).

    **Capability Format:**
        * Simple: ``multi-prefix`` (no value)
        * With value: ``sasl=PLAIN,EXTERNAL`` (capability=value)

    This function accumulates capabilities across multi-line replies and stores
    them in ``bot.server_capabilities`` once the complete list is received.
    """
    # Check if capabilities were already processed
    if bot.server_capabilities:
        # We've already seen the results, so someone sent CAP LS from a plugin.
        # We're too late to do SASL, and we don't want to send CAP END before
        # the plugin has done what it needs to, so just return
        return

    # Parse each capability in this reply line
    for cap in trigger.split():
        # Split on '=' to separate capability name from value
        c = cap.split('=')
        if len(c) == 2:
            # Capability with value (e.g., "sasl=PLAIN,EXTERNAL")
            batched_caps[c[0]] = c[1]
        else:
            # Capability without value (e.g., "multi-prefix")
            batched_caps[c[0]] = None

    # Check if this is a multi-line reply
    # In multi-line CAP LS: "CAP * LS * :cap1 cap2 cap3" (more coming)
    # In final line: "CAP * LS :cap4 cap5" (no asterisk in args[2])
    if trigger.args[2] == '*':
        # Not the last in a multi-line reply, wait for more lines
        return

    # All capability lines received, log and store them
    LOGGER.info(
        "Client capability negotiation list: %s",
        ', '.join(batched_caps.keys()),
    )
    bot.server_capabilities = batched_caps

    # If some other plugin requests it, we don't need to add another request.
    # If some other plugin prohibits it, we shouldn't request it.
    core_caps = [
        'echo-message',
        'multi-prefix',
        'away-notify',
        'chghost',
        'cap-notify',
        'server-time',
        'userhost-in-names',
        'message-tags',
    ]
    for cap in core_caps:
        if cap not in bot._cap_reqs:
            bot._cap_reqs[cap] = [utils.CapReq('', 'coretasks')]

    def acct_warn(bot, cap):
        LOGGER.info("Server does not support %s, or it conflicts with a custom "
                    "plugin. User account validation unavailable or limited.",
                    cap[1:])
        if bot.config.core.owner_account or bot.config.core.admin_accounts:
            LOGGER.warning(
                "Owner or admin accounts are configured, but %s is not "
                "supported by the server. This may cause unexpected behavior.",
                cap[1:])
    auth_caps = ['account-notify', 'extended-join', 'account-tag']
    for cap in auth_caps:
        if cap not in bot._cap_reqs:
            bot._cap_reqs[cap] = [utils.CapReq('', 'coretasks', acct_warn)]

    for cap, reqs in bot._cap_reqs.items():
        # At this point, we know mandatory and prohibited don't co-exist, but
        # we need to call back for optionals if they're also prohibited
        prefix = ''
        for entry in reqs:
            if prefix == '-' and entry.prefix != '-':
                entry.failure(bot, entry.prefix + cap)
                continue
            if entry.prefix:
                prefix = entry.prefix

        # It's not required, or it's supported, so we can request it
        if prefix != '=' or cap in bot.server_capabilities:
            # REQs fail as a whole, so we send them one capability at a time
            bot.write(('CAP', 'REQ', entry.prefix + cap))
        # If it's required but not in server caps, we need to call all the
        # callbacks
        else:
            for entry in reqs:
                if entry.failure and entry.prefix == '=':
                    entry.failure(bot, entry.prefix + cap)

    # If we want to do SASL, we have to wait before we can send CAP END. So if
    # we are, wait on 903 (SASL successful) to send it.
    if bot.config.core.auth_method == 'sasl' or bot.config.core.server_auth_method == 'sasl':
        bot.write(('CAP', 'REQ', 'sasl'))
    else:
        bot.write(('CAP', 'END'))
        LOGGER.info("End of client capability negotiation requests.")


def receive_cap_ack_sasl(bot):
    # Presumably we're only here if we said we actually *want* sasl, but still
    # check anyway in case the server glitched.
    password, mech = _get_sasl_pass_and_mech(bot)
    if not password:
        return

    available_mechs = bot.server_capabilities.get('sasl', '')
    available_mechs = available_mechs.split(',') if available_mechs else []

    if available_mechs and mech not in available_mechs:
        """
        Raise an error if configured to use an unsupported SASL mechanism,
        but only if the server actually advertised supported mechanisms,
        i.e. this network supports SASL 3.2

        SASL 3.1 failure is handled (when possible) by the sasl_mechs() function

        See https://github.com/sopel-irc/sopel/issues/1780 for background
        """
        raise config.ConfigurationError(
            "SASL mechanism '{}' is not advertised by this server.".format(mech))

    bot.write(('AUTHENTICATE', mech))


def send_authenticate(bot, token):
    """Send ``AUTHENTICATE`` command to server with the given ``token``.

    :param bot: instance of IRC bot that must authenticate
    :param str token: authentication token

    In case the ``token`` is more than 400 bytes, we need to split it and send
    as many ``AUTHENTICATE`` commands as needed. If the last chunk is 400 bytes
    long, we must also send a last empty command (`AUTHENTICATE +` is for empty
    line), so the server knows we are done with ``AUTHENTICATE``.

    .. seealso::

        https://ircv3.net/specs/extensions/sasl-3.1.html#the-authenticate-command

    """
    # payload is a base64 encoded token
    payload = base64.b64encode(token.encode('utf-8'))

    # split the payload into chunks of at most 400 bytes
    chunk_size = 400
    for i in range(0, len(payload), chunk_size):
        offset = i + chunk_size
        chunk = payload[i:offset]
        bot.write(('AUTHENTICATE', chunk))

    # send empty (+) AUTHENTICATE when payload's length is a multiple of 400
    if len(payload) % chunk_size == 0:
        bot.write(('AUTHENTICATE', '+'))


@plugin.event('AUTHENTICATE')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def auth_proceed(bot, trigger):
    """Handle client-initiated SASL auth.

    If the chosen mechanism is client-first, the server sends an empty
    response (``AUTHENTICATE +``). In that case, Sopel will handle SASL auth
    that uses a token.

    .. important::

        If ``core.auth_method`` is set, then ``core.server_auth_method`` will
        be ignored. If none is set, then this function does nothing.

    """
    if bot.config.core.auth_method == 'sasl':
        mech = bot.config.core.auth_target or 'PLAIN'
    elif bot.config.core.server_auth_method == 'sasl':
        mech = bot.config.core.server_auth_sasl_mech or 'PLAIN'
    else:
        return

    if mech == 'EXTERNAL':
        if trigger.args[0] != '+':
            # not an expected response from the server; abort SASL
            token = '*'
        else:
            token = '+'

        bot.write(('AUTHENTICATE', token))
        return

    if bot.config.core.auth_method == 'sasl':
        sasl_username = bot.config.core.auth_username
        sasl_password = bot.config.core.auth_password
    elif bot.config.core.server_auth_method == 'sasl':
        sasl_username = bot.config.core.server_auth_username
        sasl_password = bot.config.core.server_auth_password
    else:
        # How did we get here? I am not good with computer
        return

    sasl_username = sasl_username or bot.nick

    if mech == 'PLAIN':
        if trigger.args[0] == '+':
            sasl_token = _make_sasl_plain_token(sasl_username, sasl_password)
            LOGGER.info("Sending SASL Auth token.")
            send_authenticate(bot, sasl_token)
            return
        else:
            # Not an expected response from the server
            LOGGER.warning("Aborting SASL: unexpected server reply '%s'" % trigger)
            # Send `authenticate-abort` command
            # See https://ircv3.net/specs/extensions/sasl-3.1#the-authenticate-command
            bot.write(('AUTHENTICATE', '*'))
            return

    # TODO: Implement SCRAM challenges


def _make_sasl_plain_token(account, password):
    return '\x00'.join((account, account, password))


@plugin.event(events.RPL_SASLSUCCESS)
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def sasl_success(bot, trigger):
    """End CAP request on successful SASL auth.

    If SASL is configured, then the bot won't send ``CAP END`` once it gets
    all the capability responses; it will wait for SASL auth result.

    In this case, the SASL auth is a success, so we can close the negotiation.
    """
    LOGGER.info("Successful SASL Auth.")
    bot.write(('CAP', 'END'))
    LOGGER.info("End of client capability negotiation requests.")


@plugin.event(events.ERR_SASLFAIL)
@plugin.event(events.ERR_SASLTOOLONG)
@plugin.event(events.ERR_SASLABORTED)
@plugin.event(events.ERR_NICKLOCKED)
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def sasl_fail(bot, trigger):
    """SASL Auth Failed: log the error and quit."""
    LOGGER.error(
        "SASL Auth Failed; check your configuration: %s",
        str(trigger))
    bot.quit('SASL Auth Failed')


@plugin.event(events.RPL_SASLMECHS)
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('low')
def sasl_mechs(bot, trigger):
    # Presumably we're only here if we said we actually *want* sasl, but still
    # check anyway in case the server glitched.
    password, mech = _get_sasl_pass_and_mech(bot)
    if not password:
        return

    supported_mechs = trigger.args[1].split(',')
    if mech not in supported_mechs:
        """
        How we get here:

        1. Sopel connects to a network advertising SASL 3.1
        2. SASL 3.1 doesn't advertise supported mechanisms up front, so Sopel
           blindly goes ahead with whatever SASL config it's set to use
        3. The server doesn't support the mechanism Sopel used, and is a good
           IRC citizen, so it sends this optional numeric, 908 RPL_SASLMECHS

        Note that misconfigured SASL 3.1 will just silently fail when connected
        to an IRC server NOT implementing the optional 908 reply.

        A network with SASL 3.2 should theoretically never get this far because
        Sopel should catch the unadvertised mechanism in receive_cap_ack_sasl().

        See https://github.com/sopel-irc/sopel/issues/1780 for background
        """
        LOGGER.error(
            "Configured SASL mechanism '%s' is not advertised by this server. "
            "Advertised values: %s",
            mech,
            ', '.join(supported_mechs),
        )
        bot.quit('Wrong SASL configuration.')
    else:
        LOGGER.info(
            "Selected SASL mechanism is %s, advertised: %s",
            mech,
            ', '.join(supported_mechs),
        )


def _get_sasl_pass_and_mech(bot):
    password = None
    mech = None

    if bot.config.core.auth_method == 'sasl':
        password = bot.config.core.auth_password
        mech = bot.config.core.auth_target
    elif bot.config.core.server_auth_method == 'sasl':
        password = bot.config.core.server_auth_password
        mech = bot.config.core.server_auth_sasl_mech

    mech = 'PLAIN' if mech is None else mech.upper()

    return password, mech


# Live blocklist editing


@plugin.commands('blocks')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('low')
@plugin.require_admin
def blocks(bot, trigger):
    """
    Manage Sopel's blocking features.\
    See [ignore system documentation]({% link _usage/ignoring-people.md %}).
    """
    STRINGS = {
        "success_del": "Successfully deleted block: %s",
        "success_add": "Successfully added block: %s",
        "no_nick": "No matching nick block found for: %s",
        "no_host": "No matching hostmask block found for: %s",
        "invalid": "Invalid format for %s a block. Try: .blocks add (nick|hostmask) sopel",
        "invalid_display": "Invalid input for displaying blocks.",
        "nonelisted": "No %s listed in the blocklist.",
        'huh': "I could not figure out what you wanted to do.",
    }

    masks = set(s for s in bot.config.core.host_blocks if s != '')
    nicks = set(bot.make_identifier(nick)
                for nick in bot.config.core.nick_blocks
                if nick != '')
    text = trigger.group().split()

    if len(text) == 3 and text[1] == "list":
        if text[2] == "hostmask":
            if len(masks) > 0:
                blocked = ', '.join(str(mask) for mask in masks)
                bot.say("Blocked hostmasks: {}".format(blocked))
            else:
                bot.reply(STRINGS['nonelisted'] % ('hostmasks'))
        elif text[2] == "nick":
            if len(nicks) > 0:
                blocked = ', '.join(str(nick) for nick in nicks)
                bot.say("Blocked nicks: {}".format(blocked))
            else:
                bot.reply(STRINGS['nonelisted'] % ('nicks'))
        else:
            bot.reply(STRINGS['invalid_display'])

    elif len(text) == 4 and text[1] == "add":
        if text[2] == "nick":
            nicks.add(text[3])
            bot.config.core.nick_blocks = nicks
            bot.config.save()
        elif text[2] == "hostmask":
            masks.add(text[3].lower())
            bot.config.core.host_blocks = list(masks)
        else:
            bot.reply(STRINGS['invalid'] % ("adding"))
            return

        bot.reply(STRINGS['success_add'] % (text[3]))

    elif len(text) == 4 and text[1] == "del":
        if text[2] == "nick":
            nick = bot.make_identifier(text[3])
            if nick not in nicks:
                bot.reply(STRINGS['no_nick'] % (text[3]))
                return
            nicks.remove(nick)
            bot.config.core.nick_blocks = [str(n) for n in nicks]
            bot.config.save()
            bot.reply(STRINGS['success_del'] % (text[3]))
        elif text[2] == "hostmask":
            mask = text[3].lower()
            if mask not in masks:
                bot.reply(STRINGS['no_host'] % (text[3]))
                return
            masks.remove(mask)
            bot.config.core.host_blocks = [str(m) for m in masks]
            bot.config.save()
            bot.reply(STRINGS['success_del'] % (text[3]))
        else:
            bot.reply(STRINGS['invalid'] % ("deleting"))
            return
    else:
        bot.reply(STRINGS['huh'])


@plugin.event('CHGHOST')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def recv_chghost(bot, trigger):
    """Track user/host changes."""
    if trigger.nick not in bot.users:
        bot.users[trigger.nick] = target.User(
            trigger.nick, trigger.user, trigger.host)

    try:
        new_user, new_host = trigger.args
    except ValueError:
        LOGGER.warning(
            "Ignoring CHGHOST command with %s arguments: %r",
            'extra' if len(trigger.args) > 2 else 'insufficient',
            trigger.args)
        return

    bot.users[trigger.nick].user = new_user
    bot.users[trigger.nick].host = new_host
    LOGGER.info(
        "Update user@host for nick %r: %s@%s",
        trigger.nick, new_user, new_host)


@plugin.event('ACCOUNT')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def account_notify(bot, trigger):
    """Track users' accounts."""
    if trigger.nick not in bot.users:
        bot.users[trigger.nick] = target.User(
            trigger.nick, trigger.user, trigger.host)
    account = trigger.args[0]
    if account == '*':
        account = None
    bot.users[trigger.nick].account = account
    LOGGER.info("Update account for nick %r: %s", trigger.nick, account)


@plugin.event(events.RPL_WHOSPCRPL)
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def recv_whox(bot, trigger):
    """Track ``WHO`` responses when ``WHOX`` is enabled.

    RPL_WHOSPCRPL (354) is the WHOX response numeric sent by IRC servers that
    support extended WHO format. This allows more detailed user information
    including account names.

    **Expected format:**
        ``354 <client> <querytype> <channel> <nick> <user> <account> <host> <flags>``

    The querytype field is checked to ensure this response matches our request.
    Only responses with ``CORE_QUERYTYPE`` ('999') are processed by coretasks;
    others are assumed to be initiated by plugins and are ignored.

    .. seealso::

        Standard WHO response handler: :func:`recv_who`
    """
    # Validate querytype to ensure this is a response to our WHO query
    if len(trigger.args) < 2 or trigger.args[1] != CORE_QUERYTYPE:
        # Different querytype means this WHO was initiated by a plugin or
        # external source, and may have different field order/format
        LOGGER.debug("Ignoring WHO reply for channel '%s'; not queried by coretasks", trigger.args[1])
        return

    # Validate response has expected number of fields
    # Format: client, querytype, channel, nick, user, account, host, flags (8 fields)
    if len(trigger.args) != 8:
        LOGGER.warning(
            "While populating `bot.accounts` a WHO response was malformed.")
        return

    # Parse WHOX response fields
    # trigger.args[0] is the client nick (us), ignored
    _, _, channel, user, host, nick, status, account = trigger.args

    # Parse status field to extract away status and privilege modes
    # Status format: [H|G][*][@|%|+|~|&|!]...
    #   H/G = Here/Gone (away status)
    #   * = IRC operator (not channel privilege)
    #   @%+~&! = channel privilege prefixes
    away = 'G' in status  # 'G' = gone (away), 'H' = here (not away)

    # Extract privilege mode prefixes from status field
    # Filter for known privilege characters: ~ & @ % + !
    modes = ''.join([c for c in status if c in '~&@%+!'])

    # Update internal user and channel state with WHO information
    _record_who(bot, channel, user, host, nick, account, away, modes)


def _record_who(bot, channel, user, host, nick, account=None, away=None, modes=None):
    nick = bot.make_identifier(nick)
    channel = bot.make_identifier(channel)
    if nick not in bot.users:
        usr = target.User(nick, user, host)
        bot.users[nick] = usr
    else:
        usr = bot.users[nick]
        # check for & fill in sparse User added by handle_names()
        if usr.host is None and host:
            usr.host = host
        if usr.user is None and user:
            usr.user = user
    if account == '0':
        usr.account = None
    else:
        usr.account = account
    if away is not None:
        usr.away = away
    priv = 0
    if modes:
        mapping = {
            "+": plugin.VOICE,
            "%": plugin.HALFOP,
            "@": plugin.OP,
            "&": plugin.ADMIN,
            "~": plugin.OWNER,
            "!": plugin.OPER,
        }
        for c in modes:
            priv = priv | mapping[c]
    if channel not in bot.channels:
        bot.channels[channel] = target.Channel(
            channel,
            identifier_factory=bot.make_identifier,
        )

    bot.channels[channel].add_user(usr, privs=priv)


@plugin.event(events.RPL_WHOREPLY)
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def recv_who(bot, trigger):
    """Track ``WHO`` responses when ``WHOX`` is not enabled."""
    channel, user, host, _, nick, status = trigger.args[1:7]
    away = 'G' in status
    modes = ''.join([c for c in status if c in '~&@%+!'])
    _record_who(bot, channel, user, host, nick, away=away, modes=modes)


@plugin.event('AWAY')
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def track_notify(bot, trigger):
    """Track users going away or coming back."""
    if trigger.nick not in bot.users:
        bot.users[trigger.nick] = target.User(
            trigger.nick, trigger.user, trigger.host)
    user = bot.users[trigger.nick]
    user.away = bool(trigger.args)
    state_change = 'went away' if user.away else 'came back'
    LOGGER.info("User %s: %s", state_change, trigger.nick)


@plugin.event('TOPIC')
@plugin.event(events.RPL_TOPIC)
@plugin.thread(False)
@plugin.unblockable
@plugin.priority('medium')
def track_topic(bot, trigger):
    """Track channels' topics."""
    if trigger.event != 'TOPIC':
        channel = trigger.args[1]
    else:
        channel = trigger.args[0]
    if channel not in bot.channels:
        return
    bot.channels[channel].topic = trigger.args[-1]
    LOGGER.info("Channel's topic updated: %s", channel)


@plugin.rule(r'(?u).*(.+://\S+).*')
def handle_url_callbacks(bot, trigger):
    """Dispatch callbacks on URLs

    For each URL found in the trigger, trigger the URL callback registered by
    the ``@url`` decorator.
    """
    # find URLs in the trigger
    for url in trigger.urls:
        # find callbacks for said URL
        for function, match in bot.search_url_callbacks(url):
            # trigger callback defined by the `@url` decorator
            if hasattr(function, 'url_regex'):
                # bake the `match` argument in before passing the callback on
                @functools.wraps(function)
                def decorated(bot, trigger):
                    return function(bot, trigger, match=match)

                bot.call(decorated, bot, trigger)
