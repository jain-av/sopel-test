from __future__ import annotations

import errno
import json
import logging
import os.path
import traceback
import typing

from sqlalchemy import Column, create_engine, ForeignKey, Integer, String
from sqlalchemy.engine.url import make_url, URL
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

from sopel.tools import deprecated
from sopel.tools.identifiers import Identifier


LOGGER = logging.getLogger(__name__)
IdentifierFactory = typing.Callable[[str], Identifier]


def _deserialize(value):
    """Deserialize a database value from JSON.

    :param value: the raw value from the database
    :type value: str or None
    :return: the deserialized Python object
    :rtype: typing.Any

    This helper handles JSON deserialization for values stored in the database.
    SQLite has quirks with type coercion, so we normalize to string first.
    If the value isn't valid JSON, the original string is returned.
    """
    if value is None:
        return None
    # sqlite likes to return ints for strings that look like ints, even though
    # the column type is string. That's how you do dynamic typing wrong.
    value = str(value)
    # Just in case someone's mucking with the DB in a way we can't account for,
    # ignore json parsing errors
    try:
        value = json.loads(value)
    except ValueError:
        pass
    return value


BASE = declarative_base()
MYSQL_TABLE_ARGS = {'mysql_engine': 'InnoDB',
                    'mysql_charset': 'utf8mb4',
                    'mysql_collate': 'utf8mb4_unicode_ci'}


class NickIDs(BASE):
    """Nick IDs table SQLAlchemy class."""
    __tablename__ = 'nick_ids'
    nick_id = Column(Integer, primary_key=True)


class Nicknames(BASE):
    """Nicknames table SQLAlchemy class."""
    __tablename__ = 'nicknames'
    __table_args__ = MYSQL_TABLE_ARGS
    nick_id = Column(Integer, ForeignKey('nick_ids.nick_id'), primary_key=True)
    slug = Column(String(255), primary_key=True)
    canonical = Column(String(255))


class NickValues(BASE):
    """Nick values table SQLAlchemy class."""
    __tablename__ = 'nick_values'
    __table_args__ = MYSQL_TABLE_ARGS
    nick_id = Column(Integer, ForeignKey('nick_ids.nick_id'), primary_key=True)
    key = Column(String(255), primary_key=True)
    value = Column(String(255))


class ChannelValues(BASE):
    """Channel values table SQLAlchemy class."""
    __tablename__ = 'channel_values'
    __table_args__ = MYSQL_TABLE_ARGS
    channel = Column(String(255), primary_key=True)
    key = Column(String(255), primary_key=True)
    value = Column(String(255))


class PluginValues(BASE):
    """Plugin values table SQLAlchemy class."""
    __tablename__ = 'plugin_values'
    __table_args__ = MYSQL_TABLE_ARGS
    plugin = Column(String(255), primary_key=True)
    key = Column(String(255), primary_key=True)
    value = Column(String(255))


class SopelDB:
    """Database object class.

    :param config: Sopel's configuration settings
    :type config: :class:`sopel.config.Config`
    :param identifier_factory: factory for
                               :class:`~sopel.tools.identifiers.Identifier`
    :type: Callable[[:class:`str`], :class:`str`]

    This defines a simplified interface for basic, common operations on the
    bot's database. Direct access to the database is also available, to serve
    more complex plugins' needs.

    When configured to use SQLite with a relative filename, the file is assumed
    to be in the directory named by the core setting ``homedir``.

    **Identifier Factory Pattern:**

    The ``identifier_factory`` parameter allows customization of how IRC nicknames
    and channel names are normalized for case-insensitive comparison. IRC networks
    may use different case-mapping rules (RFC1459, strict-rfc1459, or ASCII), and
    the identifier factory ensures that names are compared consistently.

    For example, with RFC1459 case mapping, the characters ``[]\\~`` are considered
    lowercase equivalents of ``{}|^``. The factory creates Identifier instances that
    handle this mapping correctly, ensuring that ``Alice[bot]`` and ``alice{bot}``
    are treated as the same nickname.

    This factory is used throughout the database operations whenever a nick or
    channel name needs to be converted to a "slug" (case-normalized form) for
    storage and lookup.

    **Thread Safety:**

    This class is designed to be thread-safe. It uses SQLAlchemy's
    ``scoped_session`` which provides a thread-local session registry. Each thread
    automatically gets its own session instance, preventing the sharing of sessions
    between threads (which would cause errors as SQLAlchemy sessions are not
    thread-safe by default).

    All public methods that interact with the database follow the pattern:
    1. Get a thread-local session via ``self.ssession()``
    2. Perform database operations within a try/except/finally block
    3. Commit on success, rollback on error
    4. Always remove the session from thread-local storage in the finally block

    **Transaction Management:**

    Database operations use explicit transaction management. Changes are committed
    only when operations complete successfully. If any error occurs during a
    transaction, all changes are rolled back to maintain data consistency.

    .. versionadded:: 5.0

    .. versionchanged:: 7.0

        Switched from direct SQLite access to :ref:`SQLAlchemy
        <sqlalchemy:overview>`, allowing users more flexibility around what type
        of database they use (especially on high-load Sopel instances, which may
        run up against SQLite's concurrent-access limitations).

    .. versionchanged:: 8.0

        An Identifier factory can be provided that will be used to instantiate
        :class:`~sopel.tools.identifiers.Identifier` when dealing with Nick or
        Channel names.

    """

    def __init__(
        self,
        config,
        identifier_factory: IdentifierFactory = Identifier,
    ) -> None:
        """Initialize the database connection and create tables.

        :param config: Sopel's configuration settings
        :type config: :class:`sopel.config.Config`
        :param identifier_factory: factory function for creating Identifier instances
        :type identifier_factory: IdentifierFactory
        :raise OSError: if the database directory does not exist (SQLite only)
        :raise OperationalError: if unable to connect to the database
        :raise Exception: if database configuration is invalid or incomplete

        This constructor handles database initialization for all supported database
        types (SQLite, MySQL, PostgreSQL, Oracle, MSSQL, Firebird, Sybase). It
        creates the database connection, initializes the schema, and sets up the
        session factory for thread-safe database access.

        The ``identifier_factory`` parameter allows custom Identifier implementations
        to be used for IRC name comparison, which is important for case-insensitive
        nick and channel name handling based on the network's case mapping rules.

        .. seealso::

            The :meth:`session` method for getting a SQLAlchemy session, which is
            the recommended way to interact with the database in modern plugins.
        """
        # Store the identifier factory for creating Identifier instances
        # This is used throughout the database operations to ensure consistent
        # case-insensitive comparison of nicks and channels
        self.make_identifier = identifier_factory

        if config.core.db_url is not None:
            self.url = make_url(config.core.db_url)

            # TODO: there's no way to get `config.core.db_type.choices`, but
            # it would be nice to validate this type name somehow. Shouldn't
            # affect anything, since the only thing it's ever used for is
            # checking whether the configured database is 'sqlite'.
            self.type = self.url.drivername.split('+', 1)[0]
        elif config.core.db_type == 'sqlite':
            self.type = 'sqlite'
            path = config.core.db_filename
            if path is None:
                path = os.path.join(config.core.homedir, config.basename + '.db')
            path = os.path.expanduser(path)
            if not os.path.isabs(path):
                path = os.path.normpath(os.path.join(config.core.homedir, path))
            if not os.path.isdir(os.path.dirname(path)):
                raise OSError(
                    errno.ENOENT,
                    'Cannot create database file. '
                    'No such directory: "{}". Check that configuration setting '
                    'core.db_filename is valid'.format(os.path.dirname(path)),
                    path
                )
            self.url = make_url('sqlite:///' + path)
        else:
            self.type = config.core.db_type

            query = {}
            if self.type == 'mysql':
                drivername = config.core.db_driver or 'mysql'
                query = {'charset': 'utf8mb4'}
            elif self.type == 'postgres':
                drivername = config.core.db_driver or 'postgresql'
            elif self.type == 'oracle':
                drivername = config.core.db_driver or 'oracle'
            elif self.type == 'mssql':
                drivername = config.core.db_driver or 'mssql+pymssql'
            elif self.type == 'firebird':
                drivername = config.core.db_driver or 'firebird+fdb'
            elif self.type == 'sybase':
                drivername = config.core.db_driver or 'sybase+pysybase'
            else:
                raise Exception('Unknown db_type')

            db_user = config.core.db_user  # Sometimes empty
            db_pass = config.core.db_pass  # Sometimes empty
            db_host = config.core.db_host  # Sometimes empty
            db_port = config.core.db_port  # Optional
            db_name = config.core.db_name  # Sometimes optional

            # Ensure we have all our variables defined
            if db_user is None or db_pass is None or db_host is None:
                raise Exception('Please make sure the following core '
                                'configuration values are defined: '
                                'db_user, db_pass, db_host')
            self.url = URL(drivername=drivername, username=db_user,
                           password=db_pass, host=db_host, port=db_port,
                           database=db_name, query=query)

        # Create SQLAlchemy engine with connection pooling
        # pool_recycle=3600 ensures connections are recycled every hour to avoid
        # stale connection issues with databases that timeout idle connections
        self.engine = create_engine(self.url, pool_recycle=3600)

        # Catch any errors connecting to database
        try:
            self.engine.connect()
        except OperationalError:
            print("OperationalError: Unable to connect to database.")
            raise

        # Create our tables if they don't exist
        # This is safe to call multiple times - it only creates missing tables
        BASE.metadata.create_all(self.engine)

        # Create a scoped session factory for thread-safe database access
        # scoped_session provides a thread-local session registry, ensuring each
        # thread gets its own session instance. This is critical for thread safety
        # as SQLAlchemy sessions are not thread-safe by default.
        self.ssession = scoped_session(sessionmaker(bind=self.engine))

    def connect(self):
        """Get a direct database connection.

        :return: a proxied DBAPI connection object; see
                 :meth:`sqlalchemy.engine.Engine.raw_connection()`

        .. important::

           The :attr:`~sopel.config.core_section.CoreSection.db_type` in use
           can change how the raw connection object behaves. You probably want
           to use :meth:`session` and the SQLAlchemy ORM in new plugins, and
           officially support only Sopel 7.0+.

           Note that :meth:`session` is not available in Sopel versions prior
           to 7.0. If your plugin needs to be compatible with older Sopel
           releases, your code *should* use SQLAlchemy via :meth:`session` if
           it is available (Sopel 7.0+) and fall back to direct SQLite access
           via :meth:`connect` if it is not (Sopel 6.x).

           We discourage *publishing* plugins that don't work with all
           supported databases, but you're obviously welcome to take shortcuts
           and support only the engine(s) you need in *private* plugins.

        """
        if self.type != 'sqlite':
            # log non-sqlite uses of raw connections for troubleshooting, since
            # unless the developer had a good reason to use this instead of
            # `session()`, it indicates the plugin was written before Sopel 7.0
            # and might not work right when connected to non-sqlite DBs
            LOGGER.info(
                "Raw connection requested when 'db_type' is not 'sqlite':\n"
                "Consider using 'db.session()' to get a SQLAlchemy session "
                "instead here:\n%s",
                traceback.format_list(traceback.extract_stack()[:-1])[-1][:-1])
        return self.engine.raw_connection()

    def session(self):
        """Get a SQLAlchemy Session object.

        :return: a thread-local SQLAlchemy session instance
        :rtype: :class:`sqlalchemy.orm.session.Session`

        .. versionadded:: 7.0

        .. note::

           If your plugin needs to remain compatible with Sopel versions prior
           to 7.0, you can use :meth:`connect` to get a raw connection. See
           its documentation for relevant warnings and compatibility caveats.

        Threading note:
            This method returns a thread-local session from the scoped session
            registry. Each thread will receive its own session instance, which
            ensures thread safety. Sessions should not be shared between threads.

            The session is automatically associated with the current thread and
            will be reused for subsequent calls within the same thread until it
            is removed via ``self.ssession.remove()``.
        """
        return self.ssession()

    def execute(self, *args, **kwargs):
        """Execute an arbitrary SQL query against the database.

        :return: the query results
        :rtype: :class:`sqlalchemy.engine.Result`

        The ``Result`` object returned is a wrapper around a ``Cursor`` object
        as specified by :pep:`249`.
        """
        return self.engine.execute(*args, **kwargs)

    def get_uri(self):
        """Return a direct URL for the database.

        :return: the database connection URI
        :rtype: str

        This can be used to connect from a plugin using another SQLAlchemy
        instance, for example, without sharing the bot's connection.
        """
        return self.url

    # NICK FUNCTIONS

    def get_nick_id(self, nick: str, create: bool = False) -> int:
        """Return the internal identifier for a given nick.

        :param nick: the nickname for which to fetch an ID
        :type nick: str
        :param create: whether to create an ID if one does not exist
                       (set to ``False`` by default)
        :type create: bool
        :return: the numeric identifier for this nick (shared across aliases)
        :rtype: int
        :raise ValueError: if no ID exists for the given ``nick`` and ``create``
                           is set to ``False``
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        The nick ID is shared across all of a user's aliases, assuming their
        nicks have been grouped together. This allows plugins to associate data
        with a user regardless of which nick they're currently using.

        Usage example::

            # Check if a nick exists without creating it
            try:
                nick_id = bot.db.get_nick_id('Alice')
                # Nick exists, do something with it
            except ValueError:
                # Nick doesn't exist in database
                pass

            # Get or create a nick ID
            nick_id = bot.db.get_nick_id('Bob', create=True)

        Threading note:
            This method is thread-safe. It obtains a thread-local session and
            ensures proper cleanup via the finally block, which removes the
            session from the thread-local registry.

        .. versionchanged:: 8.0

            The ``create`` parameter is now ``False`` by default.

        .. seealso::

            Alias/group management functions: :meth:`alias_nick`,
            :meth:`unalias_nick`, :meth:`merge_nick_groups`, and
            :meth:`forget_nick_group`.

        """
        # Get thread-local session for database operations
        session = self.ssession()
        # Convert nick to lowercase slug using the identifier factory
        # This ensures case-insensitive comparison based on network rules
        slug = self.make_identifier(nick).lower()
        try:
            # Try to find existing nickname entry by slug
            nickname = session.query(Nicknames) \
                .filter(Nicknames.slug == slug) \
                .one_or_none()

            if nickname is None:
                # Check if this nick needs case-mapping migration
                # Older entries may use the old case-mapping algorithm
                nickname = session.query(Nicknames) \
                    .filter(Nicknames.slug == Identifier._lower_swapped(nick)) \
                    .one_or_none()
                if nickname is not None:
                    # Migrate to new case mapping
                    nickname.slug = slug
                    session.commit()

            # If still None after migration check, handle creation or error
            if nickname is None:  # "is /* still */ None", if Python had inline comments
                if not create:
                    raise ValueError('No ID exists for the given nick')
                # Generate a new ID in the nick_ids table
                # This ID will be shared across all aliases for this user
                nick_id = NickIDs()
                session.add(nick_id)
                session.commit()

                # Create a new Nickname entry linking the slug to the ID
                # The canonical field stores the nick with original casing
                nickname = Nicknames(
                    nick_id=nick_id.nick_id,
                    slug=slug,
                    canonical=nick,
                )
                session.add(nickname)
                session.commit()
            return nickname.nick_id
        except SQLAlchemyError:
            # Rollback transaction on any database error
            session.rollback()
            raise
        finally:
            # Always remove the session from thread-local storage
            # This prevents session leakage and ensures cleanup
            self.ssession.remove()

    def alias_nick(self, nick: str, alias: str) -> None:
        """Create an alias for a nick.

        :param nick: an existing nickname
        :type nick: str
        :param alias: an alias by which ``nick`` should also be known
        :type alias: str
        :raise ValueError: if the ``alias`` already exists
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        This adds a new nickname to an existing nick group. After aliasing, both
        the original nick and the alias will share the same nick_id and all
        associated data.

        Usage example::

            # User "Alice" also uses the nick "AliceAFK"
            bot.db.alias_nick('Alice', 'AliceAFK')

            # Now both nicks share the same data
            bot.db.set_nick_value('Alice', 'timezone', 'UTC')
            # Can retrieve using either nick
            tz = bot.db.get_nick_value('AliceAFK', 'timezone')  # Returns 'UTC'

        Threading note:
            This method is thread-safe and uses transaction management to ensure
            atomicity. The alias is only created if it doesn't already exist.

        .. seealso::

            To merge two *existing* nick groups, use :meth:`merge_nick_groups`.

            To remove an alias created with this function, use
            :meth:`unalias_nick`.

        """
        # Convert alias to slug using identifier factory for case-insensitive lookup
        slug = self.make_identifier(alias).lower()
        # Get or create the nick_id for the primary nick
        nick_id = self.get_nick_id(nick, create=True)
        session = self.ssession()
        try:
            # Check if this alias already exists
            result = session.query(Nicknames) \
                .filter(Nicknames.slug == slug) \
                .filter(Nicknames.canonical == alias) \
                .one_or_none()
            if result:
                raise ValueError('Alias already exists.')
            # Create new nickname entry with the same nick_id
            nickname = Nicknames(
                nick_id=nick_id,
                slug=slug,
                canonical=alias,
            )
            session.add(nickname)
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def set_nick_value(self, nick: str, key: str, value: typing.Any) -> None:
        """Set or update a value in the key-value store for ``nick``.

        :param nick: the nickname with which to associate the ``value``
        :type nick: str
        :param key: the name by which this ``value`` may be accessed later
        :type key: str
        :param value: the value to set for this ``key`` under ``nick``
        :type value: typing.Any
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        The ``value`` can be any of a range of types; it need not be a string.
        It will be serialized to JSON before being stored and decoded
        transparently upon retrieval. This means you can store lists, dicts,
        numbers, booleans, etc.

        Threading note:
            This method is thread-safe. If the key already exists, it will be
            updated atomically; otherwise, a new entry will be created.

        .. seealso::

            To retrieve a value set with this method, use
            :meth:`get_nick_value`.

            To delete a value set with this method, use
            :meth:`delete_nick_value`.

        """
        # Serialize value to JSON for storage
        value = json.dumps(value, ensure_ascii=False)
        # Get or create nick_id (creates if doesn't exist)
        nick_id = self.get_nick_id(nick, create=True)
        session = self.ssession()
        try:
            # Check if this key already exists for this nick
            result = session.query(NickValues) \
                .filter(NickValues.nick_id == nick_id) \
                .filter(NickValues.key == key) \
                .one_or_none()
            # NickValue exists, update it
            if result:
                result.value = value
                session.commit()
            # Does not exist - insert new entry
            else:
                new_nickvalue = NickValues(
                    nick_id=nick_id,
                    key=key,
                    value=value,
                )
                session.add(new_nickvalue)
                session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def delete_nick_value(self, nick: str, key: str) -> None:
        """Delete a value from the key-value store for ``nick``.

        :param nick: the nickname whose values to modify
        :param key: the name of the value to delete
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. seealso::

            To set a value in the first place, use :meth:`set_nick_value`.

            To retrieve a value instead of deleting it, use
            :meth:`get_nick_value`.

        """
        try:
            nick_id = self.get_nick_id(nick)
        except ValueError:
            # there's nothing to do if the nick doesn't exist
            return

        session = self.ssession()
        try:
            result = session.query(NickValues) \
                .filter(NickValues.nick_id == nick_id) \
                .filter(NickValues.key == key) \
                .one_or_none()
            # NickValue exists, delete
            if result:
                session.delete(result)
                session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def get_nick_value(
        self,
        nick: str,
        key: str,
        default: typing.Optional[typing.Any] = None
    ) -> typing.Optional[typing.Any]:
        """Get a value from the key-value store for ``nick``.

        :param nick: the nickname whose values to access
        :param key: the name by which the desired value was saved
        :param default: value to return if ``key`` does not have a value set
                        (optional)
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. versionadded:: 7.0

            The ``default`` parameter.

        .. seealso::

            To set a value for later retrieval with this method, use
            :meth:`set_nick_value`.

            To delete a value instead of retrieving it, use
            :meth:`delete_nick_value`.

        """
        slug = self.make_identifier(nick).lower()
        session = self.ssession()
        try:
            result = session.query(NickValues) \
                .filter(Nicknames.nick_id == NickValues.nick_id) \
                .filter(Nicknames.slug == slug) \
                .filter(NickValues.key == key) \
                .one_or_none()
            if result is not None:
                result = result.value
            elif default is not None:
                result = default
            return _deserialize(result)
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def unalias_nick(self, alias: str) -> None:
        """Remove an alias.

        :param alias: an alias with at least one other nick in its group
        :raise ValueError: if there is not at least one other nick in the
                           group, or the ``alias`` is not known
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. seealso::

            To delete an entire group, use :meth:`forget_nick_group`.

            To *add* an alias for a nick, use :meth:`alias_nick`.

        """
        slug = self.make_identifier(alias).lower()
        nick_id = self.get_nick_id(alias)
        session = self.ssession()
        try:
            count = session.query(Nicknames) \
                .filter(Nicknames.nick_id == nick_id) \
                .count()
            if count <= 1:
                raise ValueError('Given alias is the only entry in its group.')
            session.query(Nicknames).filter(Nicknames.slug == slug).delete()
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def forget_nick_group(self, nick: str) -> None:
        """Remove a nickname, all of its aliases, and all of its stored values.

        :param nick: one of the nicknames in the group to be deleted
        :raise ValueError: if the ``nick`` does not exist in the database
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. important::

            This is otherwise known as The Nuclear Option. Be *very* sure that
            you want to do this.

        """
        nick_id = self.get_nick_id(nick)
        session = self.ssession()
        try:
            session.query(Nicknames).filter(Nicknames.nick_id == nick_id).delete()
            session.query(NickValues).filter(NickValues.nick_id == nick_id).delete()
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    @deprecated(
        version='8.0',
        removed_in='9.0',
        reason="Renamed to `forget_nick_group`",
    )
    def delete_nick_group(self, nick: str) -> None:  # pragma: nocover
        self.forget_nick_group(nick)

    def merge_nick_groups(self, first_nick: str, second_nick: str):
        """Merge two nick groups.

        :param first_nick: one nick in the first group to merge
        :type first_nick: str
        :param second_nick: one nick in the second group to merge
        :type second_nick: str
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        Takes two nicks, which may or may not be registered. Unregistered nicks
        will be registered. Keys which are set for only one of the given nicks
        will be preserved. Where both nicks have values for a given key, the
        value set for the ``first_nick`` will be used.

        A nick group can contain one or many nicknames. Groups containing more
        than one nickname can be created with this function, or by using
        :meth:`alias_nick` to add aliases.

        Note that merging of data only applies to the native key-value store.
        Plugins which define their own tables relying on the nick table will
        need to handle their own merging separately.

        Usage example::

            # User "Alice" and "Alice_" are the same person
            # Merge their data together
            bot.db.merge_nick_groups('Alice', 'Alice_')

            # After merging:
            # - Both nicks share the same nick_id
            # - All nicknames from second group now belong to first group
            # - Values from both groups are merged (first_nick takes precedence)
            # - Future data stored under either nick will be shared

        Threading note:
            This method is thread-safe and uses proper transaction management.
            If any error occurs during the merge, all changes are rolled back.
        """
        # Get or create nick IDs for both nicks
        first_id = self.get_nick_id(first_nick, create=True)
        second_id = self.get_nick_id(second_nick, create=True)
        session = self.ssession()
        try:
            # Retrieve all key-value pairs associated with the second nick
            res = session.query(NickValues).filter(NickValues.nick_id == second_id).all()
            # Merge values: only copy keys that don't exist in first_id
            # This gives precedence to first_nick's existing values
            for row in res:
                first_res = session.query(NickValues) \
                    .filter(NickValues.nick_id == first_id) \
                    .filter(NickValues.key == row.key) \
                    .one_or_none()
                if not first_res:
                    # Key doesn't exist in first group, copy it over
                    self.set_nick_value(first_nick, row.key, _deserialize(row.value))
            # Delete all values for second_id (they've been merged)
            session.query(NickValues).filter(NickValues.nick_id == second_id).delete()
            # Update all Nicknames entries to point to first_id
            # This effectively moves all aliases from second group to first group
            session.query(Nicknames) \
                .filter(Nicknames.nick_id == second_id) \
                .update({'nick_id': first_id})
            # Commit all changes atomically
            session.commit()
        except SQLAlchemyError:
            # Rollback entire transaction on error
            session.rollback()
            raise
        finally:
            # Clean up thread-local session
            self.ssession.remove()

    # CHANNEL FUNCTIONS

    def get_channel_slug(self, chan: str) -> str:
        """Return the case-normalized representation of ``channel``.

        :param chan: the channel name to normalize, with prefix (required)
        :type chan: str
        :return: the case-normalized channel name (or "slug" representation)
        :rtype: str
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        This is useful to make sure that a channel name is stored consistently
        in both the bot's own database and third-party plugins'
        databases/files, without regard for variation in case between
        different clients and/or servers on the network.

        The slug is created using the identifier factory, which applies the
        network's case-mapping rules (RFC1459 or ASCII) to ensure that channel
        names like ``#Sopel``, ``#SOPEL``, and ``#sopel`` are all normalized
        to the same slug representation.

        Threading note:
            This method is thread-safe and handles case-mapping migration for
            legacy data automatically.
        """
        # Use identifier factory to create case-normalized slug
        # This applies the network's case mapping rules (e.g., RFC1459 vs ASCII)
        slug = self.make_identifier(chan).lower()
        session = self.ssession()
        try:
            # Check if any entries exist for this channel
            count = session.query(ChannelValues) \
                .filter(ChannelValues.channel == slug) \
                .count()

            if count == 0:
                # No entries with new slug - check for old case-mapping format
                # Older versions may have used different case-mapping algorithm
                old_rows = session.query(ChannelValues) \
                    .filter(ChannelValues.channel == Identifier._lower_swapped(chan))
                old_count = old_rows.count()
                if old_count > 0:
                    # Migrate old entries to new case mapping
                    old_rows.update({ChannelValues.channel: slug})
                    session.commit()

            return slug
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def set_channel_value(
        self,
        channel: str,
        key: str,
        value: typing.Any,
    ) -> None:
        """Set or update a value in the key-value store for ``channel``.

        :param channel: the channel with which to associate the ``value``
        :type channel: str
        :param key: the name by which this ``value`` may be accessed later
        :type key: str
        :param value: the value to set for this ``key`` under ``channel``
        :type value: typing.Any
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        The ``value`` can be any of a range of types; it need not be a string.
        It will be serialized to JSON before being stored and decoded
        transparently upon retrieval.

        Threading note:
            This method is thread-safe. Updates and inserts are performed
            atomically within a transaction.

        .. seealso::

            To retrieve a value set with this method, use
            :meth:`get_channel_value`.

            To delete a value set with this method, use
            :meth:`delete_channel_value`.

        """
        # Normalize channel name to slug for consistent storage
        channel = self.get_channel_slug(channel)
        # Serialize value to JSON
        value = json.dumps(value, ensure_ascii=False)
        session = self.ssession()
        try:
            # Check if this key already exists for this channel
            result = session.query(ChannelValues) \
                .filter(ChannelValues.channel == channel)\
                .filter(ChannelValues.key == key) \
                .one_or_none()
            # ChannelValue exists, update it
            if result:
                result.value = value
                session.commit()
            # Does not exist - insert new entry
            else:
                new_channelvalue = ChannelValues(
                    channel=channel,
                    key=key,
                    value=value,
                )
                session.add(new_channelvalue)
                session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def delete_channel_value(self, channel: str, key: str) -> None:
        """Delete a value from the key-value store for ``channel``.

        :param channel: the channel whose values to modify
        :param key: the name of the value to delete
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. seealso::

            To set a value in the first place, use :meth:`set_channel_value`.

            To retrieve a value instead of deleting it, use
            :meth:`get_channel_value`.

        """
        channel = self.get_channel_slug(channel)
        session = self.ssession()
        try:
            result = session.query(ChannelValues) \
                .filter(ChannelValues.channel == channel)\
                .filter(ChannelValues.key == key) \
                .one_or_none()
            # ChannelValue exists, delete
            if result:
                session.delete(result)
                session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def get_channel_value(
        self,
        channel: str,
        key: str,
        default: typing.Optional[typing.Any] = None,
    ):
        """Get a value from the key-value store for ``channel``.

        :param channel: the channel whose values to access
        :param key: the name by which the desired value was saved
        :param default: value to return if ``key`` does not have a value set
                        (optional)
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. versionadded:: 7.0

            The ``default`` parameter.

        .. seealso::

            To set a value for later retrieval with this method, use
            :meth:`set_channel_value`.

            To delete a value instead of retrieving it, use
            :meth:`delete_channel_value`.

        """
        channel = self.get_channel_slug(channel)
        session = self.ssession()
        try:
            result = session.query(ChannelValues) \
                .filter(ChannelValues.channel == channel)\
                .filter(ChannelValues.key == key) \
                .one_or_none()
            if result is not None:
                result = result.value
            elif default is not None:
                result = default
            return _deserialize(result)
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def forget_channel(self, channel: str) -> None:
        """Remove all of a channel's stored values.

        :param channel: the name of the channel for which to delete values
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. important::

            This is a Nuclear Option. Be *very* sure that you want to do it.

        """
        channel = self.get_channel_slug(channel)
        session = self.ssession()
        try:
            session.query(ChannelValues).filter(ChannelValues.channel == channel).delete()
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    # PLUGIN FUNCTIONS

    def set_plugin_value(
        self,
        plugin: str,
        key: str,
        value: typing.Any,
    ) -> None:
        """Set or update a value in the key-value store for ``plugin``.

        :param plugin: the plugin name with which to associate the ``value``
        :param key: the name by which this ``value`` may be accessed later
        :param value: the value to set for this ``key`` under ``plugin``
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        The ``value`` can be any of a range of types; it need not be a string.
        It will be serialized to JSON before being stored and decoded
        transparently upon retrieval.

        .. seealso::

            To retrieve a value set with this method, use
            :meth:`get_plugin_value`.

            To delete a value set with this method, use
            :meth:`delete_plugin_value`.

        """
        plugin = plugin.lower()
        value = json.dumps(value, ensure_ascii=False)
        session = self.ssession()
        try:
            result = session.query(PluginValues) \
                .filter(PluginValues.plugin == plugin)\
                .filter(PluginValues.key == key) \
                .one_or_none()
            # PluginValue exists, update
            if result:
                result.value = value
                session.commit()
            # DNE - Insert
            else:
                new_pluginvalue = PluginValues(plugin=plugin, key=key, value=value)
                session.add(new_pluginvalue)
                session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def delete_plugin_value(self, plugin: str, key: str) -> None:
        """Delete a value from the key-value store for ``plugin``.

        :param plugin: the plugin name whose values to modify
        :param key: the name of the value to delete
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. seealso::

            To set a value in the first place, use :meth:`set_plugin_value`.

            To retrieve a value instead of deleting it, use
            :meth:`get_plugin_value`.

        """
        plugin = plugin.lower()
        session = self.ssession()
        try:
            result = session.query(PluginValues) \
                .filter(PluginValues.plugin == plugin)\
                .filter(PluginValues.key == key) \
                .one_or_none()
            # PluginValue exists, update
            if result:
                session.delete(result)
                session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def get_plugin_value(
        self,
        plugin: str,
        key: str,
        default: typing.Optional[typing.Any] = None,
    ) -> typing.Optional[typing.Any]:
        """Get a value from the key-value store for ``plugin``.

        :param plugin: the plugin name whose values to access
        :param key: the name by which the desired value was saved
        :param default: value to return if ``key`` does not have a value set
                        (optional)
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. versionadded:: 7.0

            The ``default`` parameter.

        .. seealso::

            To set a value for later retrieval with this method, use
            :meth:`set_plugin_value`.

            To delete a value instead of retrieving it, use
            :meth:`delete_plugin_value`.

        """
        plugin = plugin.lower()
        session = self.ssession()
        try:
            result = session.query(PluginValues) \
                .filter(PluginValues.plugin == plugin)\
                .filter(PluginValues.key == key) \
                .one_or_none()
            if result is not None:
                result = result.value
            elif default is not None:
                result = default
            return _deserialize(result)
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    def forget_plugin(self, plugin: str) -> None:
        """Remove all of a plugin's stored values.

        :param plugin: the name of the plugin for which to delete values
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. important::

            This is a Nuclear Option. Be *very* sure that you want to do it.

        """
        plugin = plugin.lower()
        session = self.ssession()
        try:
            session.query(PluginValues).filter(PluginValues.plugin == plugin).delete()
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            self.ssession.remove()

    # NICK AND CHANNEL FUNCTIONS

    def get_nick_or_channel_value(
        self,
        name: str,
        key: str,
        default=None
    ) -> typing.Optional[typing.Any]:
        """Get a value from the key-value store for ``name``.

        :param name: nick or channel whose values to access
        :type name: str or :class:`~sopel.tools.identifiers.Identifier`
        :param key: the name by which the desired value was saved
        :type key: str
        :param default: value to return if ``key`` does not have a value set
                        (optional)
        :type default: typing.Optional[typing.Any]
        :return: the stored value, or ``default`` if not set
        :rtype: typing.Optional[typing.Any]
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        .. versionadded:: 7.0

            The ``default`` parameter.

        This is useful for common logic that is shared between both users and
        channels, as it will fetch the appropriate value based on what type of
        ``name`` it is given. The method automatically determines whether the
        name is a nick or channel using the identifier's ``is_nick()`` method.

        Threading note:
            This method is thread-safe as it delegates to either
            :meth:`get_nick_value` or :meth:`get_channel_value`, both of which
            are thread-safe.

        .. seealso::

            To get a value for a nick specifically, use :meth:`get_nick_value`.

            To get a value for a channel specifically, use
            :meth:`get_channel_value`.

        """
        # Ensure we have an Identifier instance for type checking
        if not isinstance(name, Identifier):
            identifier = self.make_identifier(name)
        else:
            identifier = typing.cast(Identifier, name)

        # Dispatch to appropriate method based on identifier type
        if identifier.is_nick():
            return self.get_nick_value(identifier, key, default)
        else:
            return self.get_channel_value(identifier, key, default)

    def get_preferred_value(
        self,
        names: typing.Iterable[str],
        key: str,
    ) -> typing.Optional[typing.Any]:
        """Get a value for the first name which has it set.

        :param names: a list of channel names and/or nicknames
        :type names: typing.Iterable[str]
        :param key: the name by which the desired value was saved
        :type key: str
        :return: the value for ``key`` from the first ``name`` which has it set,
                 or ``None`` if none of the ``names`` has it set
        :rtype: typing.Optional[typing.Any]
        :raise ~sqlalchemy.exc.SQLAlchemyError: if there is a database error

        This is useful for logic that needs to customize its output based on
        settings stored in the database. For example, it can be used to fall
        back from the triggering user's setting to the current channel's setting
        in case the user has not configured their setting.

        Usage example::

            # Check user preference first, then channel preference, then default
            names = [trigger.nick, trigger.sender]
            timezone = bot.db.get_preferred_value(names, 'timezone')
            if timezone is None:
                timezone = 'UTC'  # fallback default

        Threading note:
            This method is thread-safe. It iterates through names and returns
            the first non-None value found.

        .. note::

            This is the only ``get_*_value()`` method that does not support
            passing a ``default``. Try to avoid using it on ``key``\\s which
            might have ``None`` as a valid value, to avoid ambiguous logic.

        """
        # Iterate through names in order, returning first non-None value
        for name in names:
            value = self.get_nick_or_channel_value(name, key)
            if value is not None:
                return value

        # Explicit return for type check
        return None
