#!/usr/bin/env python
"""
Verification script for SQLAlchemy 2.0 dependency updates.

This script tests the core database functionality to ensure SQLAlchemy 2.0
compatibility after the migration from SQLAlchemy 1.4.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

# Add the project directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Test that all SQLAlchemy 2.0 imports work correctly."""
    print("Testing SQLAlchemy 2.0 imports...")
    
    try:
        # Test core SQLAlchemy 2.0 imports
        from sqlalchemy import Column, create_engine, delete, ForeignKey, Integer, select, String, text, update
        from sqlalchemy.engine.url import make_url, URL
        from sqlalchemy.exc import OperationalError, SQLAlchemyError
        from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker
        print("  ✓ Core SQLAlchemy 2.0 imports successful")
        
        # Test that sopel.db imports work
        import sopel.db
        print("  ✓ sopel.db module imported successfully")
        
        # Test that the BASE class uses DeclarativeBase
        assert isinstance(sopel.db.BASE(), DeclarativeBase)
        print("  ✓ BASE class properly inherits from DeclarativeBase")
        
        # Test that all model classes are available
        models = ['NickIDs', 'Nicknames', 'NickValues', 'ChannelValues', 'PluginValues']
        for model_name in models:
            assert hasattr(sopel.db, model_name)
            model_class = getattr(sopel.db, model_name)
            assert issubclass(model_class, sopel.db.BASE)
        print(f"  ✓ All {len(models)} model classes inherit from BASE correctly")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Import test failed: {e}")
        traceback.print_exc()
        return False

def test_database_creation():
    """Test that database creation works with SQLAlchemy 2.0."""
    print("Testing database creation...")
    
    try:
        # Create a temporary database file
        db_fd, db_filename = tempfile.mkstemp()
        os.close(db_fd)
        
        # Create a minimal config for testing
        class MockConfig:
            def __init__(self, db_filename):
                self.db_type = 'sqlite'
                self.db_filename = db_filename
                self.db_host = None
                self.db_user = None
                self.db_pass = None
                self.db_name = None
                self.db_port = None
        
        # Import and test SopelDB
        from sopel.db import SopelDB
        config = MockConfig(db_filename)
        
        # Test database creation
        db = SopelDB(config)
        print("  ✓ SopelDB instance created successfully")
        
        # Test that we can get a session
        session = db.session()
        assert session is not None
        print("  ✓ Database session created successfully")
        
        # Test that tables were created
        engine = db.engine
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        expected_tables = ['nick_ids', 'nicknames', 'nick_values', 'channel_values', 'plugin_values']
        
        for table in expected_tables:
            assert table in tables, f"Table {table} not found"
        print(f"  ✓ All {len(expected_tables)} expected tables created")
        
        session.close()
        
        # Clean up
        os.unlink(db_filename)
        
        return True
        
    except Exception as e:
        print(f"  ❌ Database creation test failed: {e}")
        traceback.print_exc()
        return False

def test_basic_operations():
    """Test basic database operations work with SQLAlchemy 2.0."""
    print("Testing basic database operations...")
    
    try:
        # Create a temporary database
        db_fd, db_filename = tempfile.mkstemp()
        os.close(db_fd)
        
        class MockConfig:
            def __init__(self, db_filename):
                self.db_type = 'sqlite'
                self.db_filename = db_filename
                self.db_host = None
                self.db_user = None
                self.db_pass = None
                self.db_name = None
                self.db_port = None
        
        from sopel.db import SopelDB
        from sopel.tools import Identifier
        
        config = MockConfig(db_filename)
        db = SopelDB(config)
        
        # Test nick ID creation
        test_nick = Identifier('TestUser')
        nick_id = db.get_nick_id(test_nick, create=True)
        assert isinstance(nick_id, int)
        print("  ✓ Nick ID creation works")
        
        # Test nick ID retrieval
        retrieved_id = db.get_nick_id(test_nick)
        assert retrieved_id == nick_id
        print("  ✓ Nick ID retrieval works")
        
        # Test setting nick value
        db.set_nick_value(test_nick, 'test_key', 'test_value')
        print("  ✓ Setting nick value works")
        
        # Test getting nick value
        value = db.get_nick_value(test_nick, 'test_key')
        assert value == 'test_value'
        print("  ✓ Getting nick value works")
        
        # Clean up
        os.unlink(db_filename)
        
        return True
        
    except Exception as e:
        print(f"  ❌ Basic operations test failed: {e}")
        traceback.print_exc()
        return False

def main():
    """Run all verification tests."""
    print("🔍 Verifying SQLAlchemy 2.0 dependency updates...\n")
    
    tests = [
        ("Import Tests", test_imports),
        ("Database Creation Tests", test_database_creation),
        ("Basic Operations Tests", test_basic_operations),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        print(f"Running {test_name}:")
        if test_func():
            print(f"✅ {test_name} PASSED\n")
            passed += 1
        else:
            print(f"❌ {test_name} FAILED\n")
            failed += 1
    
    print("=" * 50)
    print(f"Test Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("🎉 All SQLAlchemy 2.0 dependency verification tests PASSED!")
        print("The dependency updates are working correctly.")
        return 0
    else:
        print("💥 Some SQLAlchemy 2.0 dependency verification tests FAILED!")
        print("Please check the error messages above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())