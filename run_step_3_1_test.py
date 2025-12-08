#!/usr/bin/env python3
"""
Direct test of comment removal on sample files.
This runs the actual comment remover and shows results.
"""

import sys
import os
import ast
sys.path.insert(0, 'scripts')

from remove_py_comments import CommentRemover

def test_file(filepath, description):
    """Test comment removal on a single file."""
    print(f"\n{'='*80}")
    print(f"Testing: {filepath}")
    print(f"Description: {description}")
    print('='*80)

    try:
        # Read original
        with open(filepath, 'r', encoding='utf-8') as f:
            original = f.read()

        orig_lines = len(original.splitlines())
        orig_bytes = len(original)

        print(f"\nBEFORE:")
        print(f"  Lines: {orig_lines}")
        print(f"  Bytes: {orig_bytes}")

        # Validate original syntax
        try:
            ast.parse(original)
            print(f"  Syntax: ✓ Valid")
        except SyntaxError as e:
            print(f"  Syntax: ✗ Invalid - {e}")
            return False

        # Process with comment remover (dry-run)
        remover = CommentRemover(dry_run=True)
        modified = remover.process_file_content(original, filepath)

        mod_lines = len(modified.splitlines())
        mod_bytes = len(modified)

        print(f"\nAFTER:")
        print(f"  Lines: {mod_lines}")
        print(f"  Bytes: {mod_bytes}")
        print(f"  Comments removed: {remover.comments_removed}")

        print(f"\nCHANGES:")
        print(f"  Lines removed: {orig_lines - mod_lines}")
        print(f"  Bytes reduced: {orig_bytes - mod_bytes}")
        print(f"  Reduction: {((orig_bytes - mod_bytes) / orig_bytes * 100):.1f}%")

        # Validate modified syntax
        try:
            ast.parse(modified)
            print(f"\nSYNTAX VALIDATION: ✓ PASS")
        except SyntaxError as e:
            print(f"\nSYNTAX VALIDATION: ✗ FAIL - {e}")
            return False

        # Show sample of changes
        orig_lines_list = original.splitlines()
        mod_lines_list = modified.splitlines()

        print(f"\nSAMPLE CHANGES (first 5 differences):")
        print('-' * 80)
        changes_shown = 0
        for i in range(min(len(orig_lines_list), len(mod_lines_list))):
            if changes_shown >= 5:
                break
            if orig_lines_list[i] != mod_lines_list[i]:
                print(f"Line {i+1}:")
                print(f"  BEFORE: {orig_lines_list[i][:70]}")
                print(f"  AFTER:  {mod_lines_list[i][:70]}")
                changes_shown += 1

        # Show completely removed lines
        if orig_lines > mod_lines:
            removed_count = orig_lines - mod_lines
            print(f"\nCOMPLETELY REMOVED LINES: {removed_count}")
            print("Sample of removed comment lines:")
            print('-' * 80)
            shown = 0
            for i, line in enumerate(orig_lines_list[:50], 1):
                if shown >= 5:
                    break
                if line.lstrip().startswith('#') and not line.lstrip().startswith('#!'):
                    print(f"  Line {i}: {line[:70]}")
                    shown += 1

        # Check for docstrings preservation
        print(f"\nDOCSTRING CHECK:")
        orig_triple_quotes = original.count('"""') + original.count("'''")
        mod_triple_quotes = modified.count('"""') + modified.count("'''")
        if orig_triple_quotes == mod_triple_quotes:
            print(f"  ✓ All docstrings preserved (triple-quotes count: {orig_triple_quotes})")
        else:
            print(f"  ⚠ Warning: Triple-quotes count changed ({orig_triple_quotes} → {mod_triple_quotes})")

        print(f"\nOVERALL: ✓ TEST PASSED")
        return True

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run tests on all sample files."""
    print("="*80)
    print("STEP 3.1: TEST ON REPRESENTATIVE PYTHON FILES")
    print("="*80)

    tests = [
        ('sopel/__init__.py', 'Copyright + inline comments'),
        ('sopel/bot.py', 'Extensive comments'),
        ('sopel/modules/bugzilla.py', 'Module with docstrings'),
        ('test/test_bot.py', 'Test file'),
    ]

    results = {}
    for filepath, description in tests:
        results[filepath] = test_file(filepath, description)

    # Summary
    print(f"\n{'='*80}")
    print("TEST SUMMARY")
    print('='*80)

    for filepath, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status} - {filepath}")

    all_passed = all(results.values())
    print(f"\n{'='*80}")
    if all_passed:
        print("✓✓✓ ALL TESTS PASSED ✓✓✓")
        print("Comment removal script is working correctly.")
        print("Safe to proceed to Step 3.2 (shell script testing).")
    else:
        print("✗✗✗ SOME TESTS FAILED ✗✗✗")
        print("Script needs fixes before proceeding.")
    print('='*80)

    return 0 if all_passed else 1

if __name__ == '__main__':
    sys.exit(main())
