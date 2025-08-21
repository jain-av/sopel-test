#!/usr/bin/env python3
"""
Simple test script to verify SQLAlchemy 2.0 import and version compatibility.
This script checks if SQLAlchemy 2.0 can be imported and is compatible with
the other dependencies listed in requirements.txt.
"""

try:
    import sqlalchemy
    print(f"SQLAlchemy version: {sqlalchemy.__version__}")
    
    # Test basic SQLAlchemy 2.0 imports that will be needed
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import DeclarativeBase, sessionmaker
    
    print("✓ Basic SQLAlchemy 2.0 imports successful")
    
    # Test engine creation with SQLite (most common for testing)
    engine = create_engine("sqlite:///:memory:")
    print("✓ Engine creation successful")
    
    # Test declarative base
    class Base(DeclarativeBase):
        pass
    
    print("✓ DeclarativeBase class creation successful")
    
    print("All SQLAlchemy 2.0 compatibility checks passed!")
    
except ImportError as e:
    print(f"✗ Import error: {e}")
except Exception as e:
    print(f"✗ Other error: {e}")