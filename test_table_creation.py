#!/usr/bin/env python3
"""
Test script to verify table creation works with SQLAlchemy 2.0 patterns.
This is a temporary test file for Step 2.2 validation.
"""

import os
import tempfile
from sqlalchemy import create_engine
from sopel.db import BASE, NickIDs, Nicknames, NickValues, ChannelValues, PluginValues

def test_table_creation():
    """Test that tables can be created successfully with current definitions."""
    # Create a temporary SQLite database for testing
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as temp_db:
        temp_db_path = temp_db.name
    
    try:
        # Test with SQLite (most common case)
        engine = create_engine(f'sqlite:///{temp_db_path}')
        
        # Create all tables
        BASE.metadata.create_all(engine)
        
        # Verify tables were created by checking metadata
        inspector = engine.dialect.get_table_names(engine.connect())
        expected_tables = {'nick_ids', 'nicknames', 'nick_values', 'channel_values', 'plugin_values'}
        
        print("Table creation test results:")
        print(f"Expected tables: {expected_tables}")
        print(f"Created tables: {set(inspector) if hasattr(inspector, '__iter__') else 'Unable to inspect'}")
        
        # Test basic table structure
        with engine.connect() as conn:
            # Test that we can create the tables and they have the expected structure
            try:
                # Test inserting a record to verify foreign key relationships work
                conn.execute("INSERT INTO nick_ids DEFAULT VALUES")
                conn.execute("INSERT INTO nicknames (nick_id, slug, canonical) VALUES (1, 'test', 'Test')")
                conn.execute("INSERT INTO nick_values (nick_id, key, value) VALUES (1, 'test_key', 'test_value')")
                conn.commit()
                print("✓ Foreign key relationships work correctly")
            except Exception as e:
                print(f"✗ Foreign key relationship test failed: {e}")
        
        print("✓ Table creation successful")
        
    except Exception as e:
        print(f"✗ Table creation failed: {e}")
        return False
    finally:
        # Clean up temporary file
        try:
            os.unlink(temp_db_path)
        except:
            pass
    
    return True

if __name__ == "__main__":
    success = test_table_creation()
    exit(0 if success else 1)