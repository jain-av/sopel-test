#!/usr/bin/env python3
"""
Quick validation script to test remove_sh_comments.py functionality.
This demonstrates the script's capabilities without requiring file modification.
"""

import sys
import os

# Add scripts directory to path
sys.path.insert(0, os.path.dirname(__file__))

from remove_sh_comments import ShellCommentRemover


def test_basic_functionality():
    """Test basic comment removal functionality."""
    remover = ShellCommentRemover(verbose=False)

    print("="*70)
    print("SHELL COMMENT REMOVAL VALIDATION")
    print("="*70)

    test_cases = [
        ("Shebang preservation", "#!/bin/bash\n", "#!/bin/bash\n"),
        ("Comment-only line", "# This is a comment\n", ""),
        ("Inline comment", "echo hello # comment\n", "echo hello\n"),
        ("Hash in single quotes", "echo '#test'\n", "echo '#test'\n"),
        ("Hash in double quotes", 'echo "#test"\n', 'echo "#test"\n'),
        ("Variable expansion", "file=${path#/tmp/}\n", "file=${path#/tmp/}\n"),
        ("Variable expansion with comment", "file=${path#/tmp/} # comment\n", "file=${path#/tmp/}\n"),
        ("Mixed case", 'echo "a#b" # comment\n', 'echo "a#b"\n'),
    ]

    passed = 0
    failed = 0

    for description, input_line, expected in test_cases:
        result, modified, is_comment_only = remover.remove_comments_from_line(input_line)

        if is_comment_only:
            result = ""  # Comment-only lines are removed

        status = "✓ PASS" if result == expected else "✗ FAIL"

        if result == expected:
            passed += 1
        else:
            failed += 1

        print(f"\n{status}: {description}")
        print(f"  Input:    {repr(input_line)}")
        print(f"  Expected: {repr(expected)}")
        print(f"  Got:      {repr(result)}")

    print("\n" + "="*70)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("="*70)

    return failed == 0


def demonstrate_on_sample():
    """Demonstrate on a sample shell script."""
    print("\n\n" + "="*70)
    print("DEMONSTRATION ON SAMPLE SHELL SCRIPT")
    print("="*70)

    sample_script = """#!/bin/bash
# This script demonstrates comment removal
# It has various types of comments

echo "Starting script" # inline comment

# Set variables
VAR="value"
PATH_VAR=${FULL_PATH#/tmp/} # Remove /tmp/ prefix

# Comments with special characters: #, ##, ###
echo "URL: http://example.com#anchor"
echo 'Single quote with # hash'

# Final comment
exit 0
"""

    print("\nORIGINAL SCRIPT:")
    print("-" * 70)
    print(sample_script)

    # Process line by line
    remover = ShellCommentRemover(verbose=False)
    result_lines = []

    for line in sample_script.split('\n'):
        if not line:
            continue
        line_with_newline = line + '\n'
        new_line, modified, is_comment_only = remover.remove_comments_from_line(line_with_newline)

        if not is_comment_only:
            result_lines.append(new_line)

    result = ''.join(result_lines)

    print("\nPROCESSED SCRIPT (COMMENTS REMOVED):")
    print("-" * 70)
    print(result)

    print("="*70)


def main():
    """Run validation tests."""
    success = test_basic_functionality()
    demonstrate_on_sample()

    if success:
        print("\n✓ All validation tests passed!")
        print("\nThe script is ready to use. Example commands:")
        print("  # Dry-run on a single file:")
        print("  python3 scripts/remove_sh_comments.py --dry-run ci_build.sh")
        print("\n  # Process with backup and validation:")
        print("  python3 scripts/remove_sh_comments.py --backup --validate ci_build.sh")
        print("\n  # Process all .sh files in a directory:")
        print("  python3 scripts/remove_sh_comments.py --directory . --backup --validate")
        return 0
    else:
        print("\n✗ Some validation tests failed!")
        return 1


if __name__ == '__main__':
    sys.exit(main())
