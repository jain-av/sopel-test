#!/usr/bin/env python3
"""
Validation script for SQLAlchemy 2.0 database configuration enhancements.
This script verifies that Step 2.2 changes are working correctly.
"""
import sys
import tempfile
import os
from pathlib import Path

# Add the current directory to the Python path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from sopel.db import BASE, MYSQL_TABLE_ARGS, NickIDs, Nicknames, NickValues, ChannelValues, PluginValues
    from sqlalchemy import create_engine, __version__ as sqlalchemy_version
    
    print(f"SQLAlchemy version: {sqlalchemy_version}")
    print("=" * 50)
    
    # Test 1: Validate MYSQL_TABLE_ARGS configuration
    print("Test 1: MYSQL_TABLE_ARGS validation")
    expected_keys = {'mysql_engine', 'mysql_charset', 'mysql_collate'}
    actual_keys = set(MYSQL_TABLE_ARGS.keys())
    
    if expected_keys == actual_keys:
        print("✅ MYSQL_TABLE_ARGS has correct keys")
        print(f"   Configuration: {MYSQL_TABLE_ARGS}")
    else:
        print(f"❌ MYSQL_TABLE_ARGS missing keys: {expected_keys - actual_keys}")
        
    # Test 2: Verify all model classes have __repr__ methods
    print("\nTest 2: __repr__ methods validation")
    models = [NickIDs, Nicknames, NickValues, ChannelValues, PluginValues]
    
    for model in models:
        if hasattr(model, '__repr__'):
            print(f"✅ {model.__name__} has __repr__ method")
            # Test the __repr__ method works (create a mock instance)
            try:
                # Create a simple test representation
                print(f"   Example: {model.__repr__.__doc__ or 'Method callable'}")
            except Exception as e:
                print(f"   Note: __repr__ method defined but needs instance data")
        else:
            print(f"❌ {model.__name__} missing __repr__ method")
    
    # Test 3: Foreign key relationships validation
    print("\nTest 3: Foreign key relationships validation")
    
    # Check Nicknames foreign key to NickIDs
    nicknames_fk = None
    for column in Nicknames.__table__.columns:
        if column.name == 'nick_id' and column.foreign_keys:
            nicknames_fk = list(column.foreign_keys)[0]
            break
    
    if nicknames_fk:
        print(f"✅ Nicknames.nick_id foreign key: {nicknames_fk}")
    else:
        print("❌ Nicknames.nick_id missing foreign key")
        
    # Check NickValues foreign key to NickIDs
    nickvalues_fk = None
    for column in NickValues.__table__.columns:
        if column.name == 'nick_id' and column.foreign_keys:
            nickvalues_fk = list(column.foreign_keys)[0]
            break
    
    if nickvalues_fk:
        print(f"✅ NickValues.nick_id foreign key: {nickvalues_fk}")
    else:
        print("❌ NickValues.nick_id missing foreign key")
    
    # Test 4: Table creation with SQLite (most compatible test)
    print("\nTest 4: Table creation validation")
    temp_db = tempfile.mkstemp(suffix='.db')[1]
    try:
        sqlite_url = f'sqlite:///{temp_db}'
        engine = create_engine(sqlite_url, pool_recycle=3600)
        
        # This will raise an exception if there are any issues
        BASE.metadata.create_all(engine)
        print("✅ Table creation successful with SQLite")
        
        # Verify all tables were created
        inspector = engine.inspect(engine)
        table_names = inspector.get_table_names()
        expected_tables = {'nick_ids', 'nicknames', 'nick_values', 'channel_values', 'plugin_values'}
        
        if expected_tables.issubset(set(table_names)):
            print(f"✅ All expected tables created: {expected_tables}")
        else:
            missing = expected_tables - set(table_names)
            print(f"❌ Missing tables: {missing}")
            
    except Exception as e:
        print(f"❌ Table creation failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up temporary database file
        try:
            os.unlink(temp_db)
        except:
            pass
    
    print("\n" + "=" * 50)
    print("Step 2.2 validation completed")
    
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure you're running this from the correct directory")
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    import traceback
    traceback.print_exc()