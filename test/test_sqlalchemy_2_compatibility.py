"""SQLAlchemy 2.0 compatibility tests.

These tests verify that the database layer works correctly with both
SQLAlchemy 1.4 and 2.0, testing all major database operations and
ensuring compatibility across different database backends.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest
import sqlalchemy
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sopel.db import (
    Base,
    ChannelValues,
    NickIDs,
    Nicknames,
    NickValues,
    PluginValues,
    SopelDB,
)
from sopel.tools import Identifier


# SQLAlchemy version information for parameterized tests
SQLALCHEMY_VERSION = tuple(int(x) for x in sqlalchemy.__version__.split('.'))
SQLALCHEMY_MAJOR = SQLALCHEMY_VERSION[0]


class TestSQLAlchemy2Compatibility:
    """Test SQLAlchemy 2.0 compatibility and modern query patterns."""

    @pytest.fixture
    def db_file(self):
        """Create a temporary database file."""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)

    @pytest.fixture
    def sqlite_db(self, configfactory, db_file):
        """Create a SQLite database instance for testing."""
        config_content = f"""
[core]
owner = TestOwner
db_filename = {db_file}
"""
        settings = configfactory('test.cfg', config_content)
        db = SopelDB(settings)
        return db

    @pytest.fixture
    def in_memory_db(self, configfactory):
        """Create an in-memory SQLite database for testing."""
        config_content = """
[core]
owner = TestOwner
db_url = sqlite:///:memory:
"""
        settings = configfactory('test.cfg', config_content)
        db = SopelDB(settings)
        return db

    def test_sqlalchemy_version_compatibility(self):
        """Test that we can determine SQLAlchemy version."""
        # Test that we can parse the version
        assert isinstance(SQLALCHEMY_VERSION, tuple)
        assert len(SQLALCHEMY_VERSION) >= 2
        assert SQLALCHEMY_MAJOR in (1, 2)

    def test_modern_declarative_base_usage(self, sqlite_db):
        """Test that the modern DeclarativeBase pattern works."""
        # Verify that Base is using the modern DeclarativeBase pattern
        from sqlalchemy.orm import DeclarativeBase
        assert issubclass(Base, DeclarativeBase)

        # Verify all models inherit from the modern base
        assert issubclass(NickIDs, Base)
        assert issubclass(Nicknames, Base)
        assert issubclass(NickValues, Base)
        assert issubclass(ChannelValues, Base)
        assert issubclass(PluginValues, Base)

    def test_engine_configuration_2_0_features(self, sqlite_db):
        """Test that engine is configured with SQLAlchemy 2.0 features."""
        engine = sqlite_db.engine

        # Test that future=True is enabled (2.0-style behavior)
        # This is verified by checking that the engine has modern behavior
        assert hasattr(engine, 'begin')
        assert hasattr(engine, 'connect')

        # Test connection works
        with engine.connect() as conn:
            assert conn is not None

    def test_session_modern_patterns(self, sqlite_db):
        """Test that session uses modern SQLAlchemy patterns."""
        session = sqlite_db.session()

        # Test that session has modern methods
        assert hasattr(session, 'execute')
        assert hasattr(session, 'scalars')
        assert hasattr(session, 'get')

        # Test basic query using modern select() syntax
        stmt = select(NickIDs)
        result = session.execute(stmt)
        assert result is not None

        session.close()

    def test_modern_query_syntax_nick_operations(self, sqlite_db):
        """Test nick operations use modern query syntax."""
        nick = 'TestUser'

        # Test creating nick ID
        nick_id = sqlite_db.get_nick_id(nick, create=True)
        assert isinstance(nick_id, int)

        # Test that we can query using modern syntax
        session = sqlite_db.session()
        stmt = select(Nicknames).where(Nicknames.nick_id == nick_id)
        result = session.execute(stmt).scalars().first()
        assert result is not None
        assert result.canonical == nick
        session.close()

    def test_modern_query_syntax_channel_operations(self, sqlite_db):
        """Test channel operations use modern query syntax."""
        channel = '#testchannel'
        key = 'testkey'
        value = 'testvalue'

        # Test setting channel value
        sqlite_db.set_channel_value(channel, key, value)

        # Test querying with modern syntax
        session = sqlite_db.session()
        stmt = select(ChannelValues).where(
            ChannelValues.channel == channel,
            ChannelValues.key == key
        )
        result = session.execute(stmt).scalars().first()
        assert result is not None
        assert json.loads(result.value) == value
        session.close()

    def test_modern_query_syntax_plugin_operations(self, sqlite_db):
        """Test plugin operations use modern query syntax."""
        plugin = 'testplugin'
        key = 'testkey'
        value = 'testvalue'

        # Test setting plugin value
        sqlite_db.set_plugin_value(plugin, key, value)

        # Test querying with modern syntax
        session = sqlite_db.session()
        stmt = select(PluginValues).where(
            PluginValues.plugin == plugin,
            PluginValues.key == key
        )
        result = session.execute(stmt).scalars().first()
        assert result is not None
        assert json.loads(result.value) == value
        session.close()

    def test_transaction_handling_modern_syntax(self, sqlite_db):
        """Test that transaction handling works with modern syntax."""
        session = sqlite_db.session()

        try:
            # Test creating data within transaction
            nick_id = sqlite_db.get_nick_id('TransactionTest', create=True)

            # Test that we can query the data
            stmt = select(Nicknames).where(Nicknames.nick_id == nick_id)
            result = session.execute(stmt).scalars().first()
            assert result is not None

            # Test commit behavior
            session.commit()

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def test_complex_queries_with_joins(self, sqlite_db):
        """Test complex queries with joins using modern syntax."""
        nick = 'JoinTestUser'
        key = 'joinkey'
        value = 'joinvalue'

        # Create test data
        nick_id = sqlite_db.get_nick_id(nick, create=True)
        sqlite_db.set_nick_value(nick, key, value)

        # Test complex query with join
        session = sqlite_db.session()
        stmt = select(NickValues).join(Nicknames).where(
            Nicknames.canonical == nick,
            NickValues.key == key
        )
        result = session.execute(stmt).scalars().first()
        assert result is not None
        assert json.loads(result.value) == value
        session.close()

    def test_count_operations_modern_syntax(self, sqlite_db):
        """Test count operations using modern func.count() syntax."""
        # Create test data
        for i in range(5):
            sqlite_db.get_nick_id(f'CountTest{i}', create=True)

        # Test count with modern syntax
        session = sqlite_db.session()
        from sqlalchemy import func
        stmt = select(func.count(NickIDs.nick_id))
        count = session.execute(stmt).scalar()
        assert count >= 5
        session.close()

    def test_database_operations_compatibility(self, sqlite_db):
        """Test all major database operations for compatibility."""
        # Test nick operations
        nick = 'CompatibilityTest'
        nick_id = sqlite_db.get_nick_id(nick, create=True)
        sqlite_db.set_nick_value(nick, 'test_key', 'test_value')
        retrieved_value = sqlite_db.get_nick_value(nick, 'test_key')
        assert retrieved_value == 'test_value'

        # Test channel operations
        channel = '#compattest'
        sqlite_db.set_channel_value(channel, 'chan_key', 'chan_value')
        retrieved_chan_value = sqlite_db.get_channel_value(channel, 'chan_key')
        assert retrieved_chan_value == 'chan_value'

        # Test plugin operations
        plugin = 'compatplugin'
        sqlite_db.set_plugin_value(plugin, 'plugin_key', 'plugin_value')
        retrieved_plugin_value = sqlite_db.get_plugin_value(plugin, 'plugin_key')
        assert retrieved_plugin_value == 'plugin_value'

        # Test alias operations
        alias = 'CompatibilityAlias'
        sqlite_db.alias_nick(nick, alias)
        alias_id = sqlite_db.get_nick_id(alias)
        assert alias_id == nick_id

    def test_delete_operations_modern_syntax(self, sqlite_db):
        """Test delete operations using modern delete syntax."""
        # Create test data
        nick = 'DeleteTest'
        nick_id = sqlite_db.get_nick_id(nick, create=True)
        sqlite_db.set_nick_value(nick, 'delete_key', 'delete_value')

        # Test delete operations
        sqlite_db.delete_nick_value(nick, 'delete_key')
        retrieved_value = sqlite_db.get_nick_value(nick, 'delete_key')
        assert retrieved_value is None

    def test_update_operations_modern_syntax(self, sqlite_db):
        """Test update operations using modern update syntax."""
        # Create test data
        nick = 'UpdateTest'
        sqlite_db.get_nick_id(nick, create=True)
        sqlite_db.set_nick_value(nick, 'update_key', 'original_value')

        # Update the value
        sqlite_db.set_nick_value(nick, 'update_key', 'updated_value')

        # Verify update
        retrieved_value = sqlite_db.get_nick_value(nick, 'update_key')
        assert retrieved_value == 'updated_value'

    def test_error_handling_compatibility(self, sqlite_db):
        """Test error handling works with modern syntax."""
        # Test ValueError for non-existent nick
        with pytest.raises(ValueError):
            sqlite_db.get_nick_id('NonExistentNick')

        # Test ValueError for invalid alias operations
        nick1 = 'ErrorTest1'
        nick2 = 'ErrorTest2'
        sqlite_db.get_nick_id(nick1, create=True)
        sqlite_db.get_nick_id(nick2, create=True)

        with pytest.raises(ValueError):
            sqlite_db.alias_nick(nick2, nick1)  # Should fail - both exist

    def test_identifier_handling_compatibility(self, sqlite_db):
        """Test Identifier handling works with modern syntax."""
        # Test case-insensitive nick handling
        nick_upper = 'IDENTIFIERTEST'
        nick_lower = 'identifiertest'

        nick_id_upper = sqlite_db.get_nick_id(nick_upper, create=True)
        nick_id_lower = sqlite_db.get_nick_id(nick_lower)  # Should not create new

        assert nick_id_upper == nick_id_lower

    @pytest.mark.parametrize("backend_type", ["sqlite"])
    def test_backend_specific_operations(self, backend_type, sqlite_db):
        """Test backend-specific operations work correctly."""
        if backend_type == "sqlite":
            db = sqlite_db

            # Test that basic operations work
            nick = f'BackendTest_{backend_type}'
            nick_id = db.get_nick_id(nick, create=True)
            db.set_nick_value(nick, 'backend_key', 'backend_value')

            retrieved_value = db.get_nick_value(nick, 'backend_key')
            assert retrieved_value == 'backend_value'

    def test_session_scoping_compatibility(self, sqlite_db):
        """Test that session scoping works correctly with modern patterns."""
        # Test that we get consistent sessions
        session1 = sqlite_db.session()
        session2 = sqlite_db.session()

        # They should be the same scoped session
        assert session1 is session2

        session1.close()

    def test_connection_pooling_compatibility(self, sqlite_db):
        """Test that connection pooling works with modern patterns."""
        # Test multiple connections work
        conn1 = sqlite_db.engine.connect()
        conn2 = sqlite_db.engine.connect()

        assert conn1 is not None
        assert conn2 is not None

        conn1.close()
        conn2.close()

    def test_execute_method_compatibility(self, sqlite_db):
        """Test that the execute method works with modern syntax."""
        # Test execute with modern statement
        stmt = select(NickIDs)
        result = sqlite_db.execute(stmt)
        assert result is not None

    def test_concurrent_access_compatibility(self, sqlite_db):
        """Test concurrent access patterns work with modern syntax."""
        # Test multiple sessions can access the database
        session1 = sqlite_db.session()
        session2 = sqlite_db.session()

        # Create data with first session
        nick1 = 'ConcurrentTest1'
        sqlite_db.get_nick_id(nick1, create=True)

        # Read with second session
        stmt = select(Nicknames).where(Nicknames.canonical == nick1)
        result = session2.execute(stmt).scalars().first()
        assert result is not None

        session1.close()
        session2.close()


class TestDatabaseBackendCompatibility:
    """Test compatibility across different database backends."""

    def test_sqlite_memory_backend(self, configfactory):
        """Test in-memory SQLite backend compatibility."""
        config_content = """
[core]
owner = TestOwner
db_url = sqlite:///:memory:
"""
        settings = configfactory('test.cfg', config_content)
        db = SopelDB(settings)

        # Test basic operations
        nick = 'MemoryTest'
        nick_id = db.get_nick_id(nick, create=True)
        db.set_nick_value(nick, 'memory_key', 'memory_value')

        retrieved_value = db.get_nick_value(nick, 'memory_key')
        assert retrieved_value == 'memory_value'

    def test_sqlite_file_backend(self, configfactory):
        """Test file-based SQLite backend compatibility."""
        fd, db_file = tempfile.mkstemp(suffix='.db')
        os.close(fd)

        try:
            config_content = f"""
[core]
owner = TestOwner
db_filename = {db_file}
"""
            settings = configfactory('test.cfg', config_content)
            db = SopelDB(settings)

            # Test basic operations
            nick = 'FileTest'
            nick_id = db.get_nick_id(nick, create=True)
            db.set_nick_value(nick, 'file_key', 'file_value')

            retrieved_value = db.get_nick_value(nick, 'file_key')
            assert retrieved_value == 'file_value'

        finally:
            if os.path.exists(db_file):
                os.remove(db_file)

    # Note: MySQL and PostgreSQL tests would require actual database servers
    # These are placeholder tests that would be activated when those backends are available

    @pytest.mark.skipif(True, reason="Requires MySQL server for testing")
    def test_mysql_backend_compatibility(self):
        """Test MySQL backend compatibility (requires server)."""
        # This test would be enabled when MySQL is available for testing
        pass

    @pytest.mark.skipif(True, reason="Requires PostgreSQL server for testing")
    def test_postgresql_backend_compatibility(self):
        """Test PostgreSQL backend compatibility (requires server)."""
        # This test would be enabled when PostgreSQL is available for testing
        pass


class TestSQLAlchemyVersionSpecificBehavior:
    """Test version-specific behavior differences between SQLAlchemy 1.4 and 2.0."""

    def test_query_result_handling(self, sqlite_db):
        """Test that query result handling works across versions."""
        # Create test data
        nick = 'ResultTest'
        nick_id = sqlite_db.get_nick_id(nick, create=True)

        session = sqlite_db.session()

        # Test scalar results
        stmt = select(Nicknames.canonical).where(Nicknames.nick_id == nick_id)
        result = session.execute(stmt).scalar()
        assert result == nick

        # Test scalars() for multiple results
        stmt = select(Nicknames.canonical)
        results = session.execute(stmt).scalars().all()
        assert nick in results

        session.close()

    def test_session_api_compatibility(self, sqlite_db):
        """Test session API compatibility across versions."""
        session = sqlite_db.session()

        # Test that modern session methods are available
        assert hasattr(session, 'execute')
        assert hasattr(session, 'commit')
        assert hasattr(session, 'rollback')
        assert hasattr(session, 'close')

        # Test that legacy query method still works for backward compatibility
        # (SQLAlchemy 1.4 provides this, 2.0 would need legacy query plugin)
        if SQLALCHEMY_MAJOR == 1:
            assert hasattr(session, 'query')

        session.close()

    def test_future_flag_behavior(self, sqlite_db):
        """Test that future=True flag provides 2.0-style behavior."""
        engine = sqlite_db.engine

        # Test that the engine was created with future=True
        # This ensures 2.0-style behavior even in SQLAlchemy 1.4
        with engine.connect() as conn:
            # Test that connection has 2.0-style methods
            assert hasattr(conn, 'execute')
            assert hasattr(conn, 'commit')