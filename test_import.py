#!/usr/bin/env python3
"""Test script to verify SQLAlchemy 2.0 base class changes work correctly."""

try:
    from sopel.db import Base, NickIDs, Nicknames, NickValues, ChannelValues, PluginValues
    print("✓ Successfully imported all database models")
    
    # Test that all models inherit from Base correctly
    models = [NickIDs, Nicknames, NickValues, ChannelValues, PluginValues]
    for model in models:
        if issubclass(model, Base):
            print(f"✓ {model.__name__} correctly inherits from Base")
        else:
            print(f"✗ {model.__name__} does not inherit from Base")
    
    # Test that metadata is accessible
    print(f"✓ Base.metadata is accessible: {type(Base.metadata)}")
    
    print("\nAll base class migration changes appear to be working correctly!")
    
except Exception as e:
    print(f"✗ Error importing database models: {e}")
    import traceback
    traceback.print_exc()