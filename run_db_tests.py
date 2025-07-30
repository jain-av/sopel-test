#!/usr/bin/env python
"""
Run specific database tests to verify SQLAlchemy 2.0 compatibility.

This script runs the database tests without requiring pytest installation,
focusing on the core functionality that needs to work with SQLAlchemy 2.0.
"""
from __future__ import annotations

import os
import sys
import tempfile

# Add the project directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def create_test_config():
    """Create a test configuration for database tests."""
    class TestConfig:
        def __init__(self, db_filename):
            self.db_type = 'sqlite'
            self.db_filename = db_filename
            self.db_host = None
            self.db_user = None
            self.db_pass = None
            self.db_name = None
            self.db_port = None
    
    # Create temporary database file
    db_fd, db_filename = tempfile.mkstemp()
    os.close(db_fd)
    
    return TestConfig(db_filename), db_filename

def test_get_nick_id():
    """Test get_nick_id functionality (adapted from test_db.py)."""
    print("Testing get_nick_id functionality...")
    
    try:
        from sopel.db import SopelDB, Nicknames
        from sopel.tools import Identifier
        
        config, db_filename = create_test_config()
        db = SopelDB(config)
        
        nick = Identifier('Exirel')
        session = db.session()
        
        # Test that get_nick_id raises ValueError when nick doesn't exist
        try:
            db.get_nick_id(nick)
            assert False, "Expected ValueError for non-existent nick"
        except ValueError:
            pass  # Expected
        
        # Create the nick ID
        nick_id = db.get_nick_id(nick, create=True)
        assert isinstance(nick_id, int)
        
        # Check that the nickname was created correctly
        # Note: This uses legacy query syntax that should still work in SQLAlchemy 2.0
        nickname = session.query(Nicknames).filter(
            Nicknames.nick_id == nick_id,
        ).one()
        
        assert nickname.canonical == 'Exirel'
        assert nickname.slug == nick.lower()
        
        session.close()
        os.unlink(db_filename)
        
        print("  ✓ get_nick_id test passed")
        return True
        
    except Exception as e:
        print(f"  ❌ get_nick_id test failed: {e}")
        if 'db_filename' in locals():
            try:
                os.unlink(db_filename)
            except:
                pass
        return False

def test_set_nick_value():
    """Test set_nick_value functionality (adapted from test_db.py)."""
    print("Testing set_nick_value functionality...")
    
    try:
        from sopel.db import SopelDB
        from sopel.tools import Identifier
        
        config, db_filename = create_test_config()
        db = SopelDB(config)
        
        nick = 'Embolalia'
        test_data = {
            'key': 'value',
            'number_key': 1234,
            'unicode': 'EmbölaliÅ',
        }
        
        # Set nick values
        for key, value in test_data.items():
            db.set_nick_value(nick, key, value)
        
        # Get nick ID (should be created automatically)
        nick_id = db.get_nick_id(nick)
        assert isinstance(nick_id, int)
        
        # Verify values were set correctly
        for key, expected_value in test_data.items():
            actual_value = db.get_nick_value(nick, key)
            assert actual_value == expected_value, f"Value mismatch for key {key}: expected {expected_value}, got {actual_value}"
        
        os.unlink(db_filename)
        
        print("  ✓ set_nick_value test passed")
        return True
        
    except Exception as e:
        print(f"  ❌ set_nick_value test failed: {e}")
        if 'db_filename' in locals():
            try:
                os.unlink(db_filename)
            except:
                pass
        return False

def test_alias_nick():
    """Test alias_nick functionality (adapted from test_db.py)."""
    print("Testing alias_nick functionality...")
    
    try:
        from sopel.db import SopelDB
        
        config, db_filename = create_test_config()
        db = SopelDB(config)
        
        nick = 'Embolalia'
        aliases = ['EmbölaliÅ', 'Embo`work', 'Embo']
        
        # Create the main nick
        nick_id = db.get_nick_id(nick, create=True)
        
        # Add aliases
        for alias in aliases:
            db.alias_nick(nick, alias)
        
        # Verify all aliases point to the same nick ID
        for alias in aliases:
            alias_id = db.get_nick_id(alias)
            assert alias_id == nick_id, f"Alias {alias} has wrong nick_id: expected {nick_id}, got {alias_id}"
        
        # Test that alias_nick works with new nicks
        db.alias_nick('both', 'arenew')  # Should not fail
        
        # Test error conditions
        try:
            db.alias_nick('Eve', nick)  # Should fail - Eve doesn't exist
            assert False, "Expected ValueError for non-existent nick"
        except ValueError:
            pass  # Expected
        
        try:
            db.alias_nick(nick, nick)  # Should fail - can't alias to self
            assert False, "Expected ValueError for self-alias"
        except ValueError:
            pass  # Expected
        
        os.unlink(db_filename)
        
        print("  ✓ alias_nick test passed")
        return True
        
    except Exception as e:
        print(f"  ❌ alias_nick test failed: {e}")
        if 'db_filename' in locals():
            try:
                os.unlink(db_filename)
            except:
                pass
        return False

def main():
    """Run all database tests."""
    print("🧪 Running database tests to verify SQLAlchemy 2.0 compatibility...\n")
    
    tests = [
        test_get_nick_id,
        test_set_nick_value,
        test_alias_nick,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        if test_func():
            passed += 1
        else:
            failed += 1
        print()  # Add spacing between tests
    
    print("=" * 60)
    print(f"Database Test Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("🎉 All database tests PASSED!")
        print("SQLAlchemy 2.0 dependency updates are working correctly.")
        return 0
    else:
        print("💥 Some database tests FAILED!")
        print("There may be issues with the SQLAlchemy 2.0 dependency updates.")
        return 1

if __name__ == "__main__":
    sys.exit(main())