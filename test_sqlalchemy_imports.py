#!/usr/bin/env python
"""Test script to verify SQLAlchemy 2.0 imports work correctly."""

import sys
import os

# Add the project directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_sqlalchemy_imports():
    """Test that SQLAlchemy 2.0 imports work correctly."""
    try:
        # Test importing the db module with SQLAlchemy 2.0 imports
        import sopel.db
        print("✓ Successfully imported sopel.db with SQLAlchemy 2.0")
        
        # Test that the specific SQLAlchemy 2.0 imports are available
        from sqlalchemy import select, text, update, delete
        print("✓ Successfully imported SQLAlchemy 2.0 functions: select, text, update, delete")
        
        # Test that DeclarativeBase is available
        from sqlalchemy.orm import DeclarativeBase
        print("✓ Successfully imported DeclarativeBase")
        
        # Test that the BASE class is properly defined
        assert hasattr(sopel.db, 'BASE')
        assert issubclass(sopel.db.BASE, DeclarativeBase)
        print("✓ BASE class properly inherits from DeclarativeBase")
        
        # Test that the model classes are available
        assert hasattr(sopel.db, 'NickIDs')
        assert hasattr(sopel.db, 'Nicknames')
        assert hasattr(sopel.db, 'NickValues')
        assert hasattr(sopel.db, 'ChannelValues')
        assert hasattr(sopel.db, 'PluginValues')
        print("✓ All SQLAlchemy model classes are available")
        
        print("\n🎉 All SQLAlchemy 2.0 imports and basic functionality verified successfully!")
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    success = test_sqlalchemy_imports()
    sys.exit(0 if success else 1)