#!/usr/bin/env python3
"""
Verification script to test SQLAlchemy 2.0 dependency updates.
This script checks if the basic SQLAlchemy imports and database operations work.
"""

import sys
import tempfile
import os

def test_sqlalchemy_imports():
    """Test that SQLAlchemy 2.0 imports work correctly."""
    print("Testing SQLAlchemy 2.0 imports...")
    
    try:
        # Test core SQLAlchemy 2.0 imports
        from sqlalchemy import __version__
        print(f"SQLAlchemy version: {__version__}")
        
        if not __version__.startswith('2.'):
            print(f"ERROR: Expected SQLAlchemy 2.x, got {__version__}")
            return False
            
        # Test specific 2.0 imports used in the codebase
        from sqlalchemy import Column, create_engine, delete, ForeignKey, Integer, select, String, text, update
        from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker
        print("✓ All required SQLAlchemy 2.0 imports successful")
        return True
        
    except ImportError as e:
        print(f"ERROR: Failed to import SQLAlchemy components: {e}")
        return False

def test_database_basic_operations():
    """Test basic database operations with SQLAlchemy 2.0."""
    print("\nTesting basic database operations...")
    
    try:
        # Import the sopel db module
        from sopel.db import SopelDB, BASE
        from sopel.config import Config
        
        # Create a temporary database
        temp_db = tempfile.mkstemp()[1]
        
        # Mock a minimal config
        class MockConfig:
            def __init__(self, db_filename):
                self.core = MockCore(db_filename)
                
        class MockCore:
            def __init__(self, db_filename):
                self.db_filename = db_filename
                self.db_type = 'sqlite'
                self.db_host = None
                self.db_user = None
                self.db_pass = None
                self.db_name = None
                self.db_port = None
                
        config = MockConfig(temp_db)
        
        # Test database initialization
        db = SopelDB(config)
        print("✓ SopelDB initialization successful")
        
        # Test creating tables (this exercises the DeclarativeBase)
        BASE.metadata.create_all(db.engine)
        print("✓ Database table creation successful")
        
        # Clean up
        os.remove(temp_db)
        print("✓ Basic database operations successful")
        return True
        
    except Exception as e:
        print(f"ERROR: Database operations failed: {e}")
        try:
            os.remove(temp_db)
        except:
            pass
        return False

def test_legacy_patterns():
    """Check for legacy patterns that might cause issues."""
    print("\nChecking for potential compatibility issues...")
    
    # Read the db.py file to check for legacy patterns
    try:
        with open('/private/tmp/codemod/rnd-10c13d5b-c105-4d79-a749-195c711f22aa/repo-449/sopel/db.py', 'r') as f:
            content = f.read()
            
        # Count legacy query patterns
        legacy_query_count = content.count('session.query(')
        if legacy_query_count > 0:
            print(f"⚠️  Found {legacy_query_count} legacy session.query() calls")
            print("   These may cause deprecation warnings in SQLAlchemy 2.0")
        else:
            print("✓ No legacy session.query() patterns detected")
            
        # Check for engine.execute() (removed in 2.0)
        if 'engine.execute(' in content:
            print("❌ Found deprecated engine.execute() calls")
            return False
        else:
            print("✓ No deprecated engine.execute() calls found")
            
        return True
        
    except Exception as e:
        print(f"ERROR: Could not check for legacy patterns: {e}")
        return False

def main():
    """Run all verification tests."""
    print("=== SQLAlchemy 2.0 Dependency Verification ===\n")
    
    tests = [
        test_sqlalchemy_imports,
        test_database_basic_operations,
        test_legacy_patterns
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"ERROR: Test {test.__name__} failed with exception: {e}")
            results.append(False)
    
    print(f"\n=== Results ===")
    passed = sum(results)
    total = len(results)
    print(f"Tests passed: {passed}/{total}")
    
    if all(results):
        print("✅ All dependency verification tests passed!")
        print("SQLAlchemy 2.0 dependencies appear to be working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Review the output above for details.")
        return 1

if __name__ == '__main__':
    sys.exit(main())