#!/usr/bin/env python3
"""Validate Python syntax using AST"""
import ast
import sys

def validate_file(filepath):
    """Validate Python file syntax"""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        ast.parse(content)
        print(f"✓ Syntax validation PASSED for {filepath}")
        return True
    except SyntaxError as e:
        print(f"✗ Syntax validation FAILED for {filepath}")
        print(f"  Error: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 validate_syntax.py <file>")
        sys.exit(1)

    success = validate_file(sys.argv[1])
    sys.exit(0 if success else 1)
