#!/usr/bin/env python3
"""
Unit tests for remove_sh_comments.py

Tests the shell comment removal functionality with various edge cases.
"""

import unittest
import sys
import os

# Add scripts directory to path
sys.path.insert(0, os.path.dirname(__file__))

from remove_sh_comments import ShellCommentRemover


class TestShellCommentRemover(unittest.TestCase):
    """Test cases for ShellCommentRemover class."""

    def setUp(self):
        """Set up test fixtures."""
        self.remover = ShellCommentRemover(verbose=False)

    def test_preserve_shebang(self):
        """Test that shebang lines are preserved."""
        test_cases = [
            "#!/bin/sh\n",
            "#!/bin/bash\n",
            "#!/usr/bin/env bash\n",
            "#!/bin/sh -x\n",
        ]
        for line in test_cases:
            result, modified, _ = self.remover.remove_comments_from_line(line)
            self.assertEqual(result, line, f"Shebang should be preserved: {line}")
            self.assertFalse(modified, f"Shebang should not be marked as modified: {line}")

    def test_remove_comment_only_lines(self):
        """Test removal of lines containing only comments."""
        test_cases = [
            ("# This is a comment\n", True, True),
            ("  # Indented comment\n", True, True),
            ("\t# Tab-indented comment\n", True, True),
            ("## Multiple hashes\n", True, True),
        ]
        for line, should_modify, should_be_comment_only in test_cases:
            result, modified, is_comment_only = self.remover.remove_comments_from_line(line)
            self.assertEqual(result, '', f"Comment-only line should be empty: {line}")
            self.assertTrue(modified, f"Should be marked as modified: {line}")
            self.assertTrue(is_comment_only, f"Should be marked as comment-only: {line}")

    def test_remove_inline_comments(self):
        """Test removal of inline comments after commands."""
        test_cases = [
            ("echo hello # comment\n", "echo hello\n"),
            ("exit 0 # exit with success\n", "exit 0\n"),
            ("command  # comment with spaces\n", "command\n"),
            ("cmd1 && cmd2 # inline comment\n", "cmd1 && cmd2\n"),
        ]
        for input_line, expected in test_cases:
            result, modified, _ = self.remover.remove_comments_from_line(input_line)
            self.assertEqual(result, expected, f"Inline comment removal failed for: {input_line}")
            self.assertTrue(modified, f"Should be marked as modified: {input_line}")

    def test_preserve_hash_in_single_quotes(self):
        """Test that # inside single quotes is preserved."""
        test_cases = [
            ("echo '#hello'\n", "echo '#hello'\n", False),
            ("echo 'test # comment'\n", "echo 'test # comment'\n", False),
            ("cmd='echo #test'\n", "cmd='echo #test'\n", False),
            ("echo '#' && echo test\n", "echo '#' && echo test\n", False),
        ]
        for input_line, expected, should_modify in test_cases:
            result, modified, _ = self.remover.remove_comments_from_line(input_line)
            self.assertEqual(result, expected, f"# in single quotes should be preserved: {input_line}")
            self.assertEqual(modified, should_modify, f"Modification flag incorrect for: {input_line}")

    def test_preserve_hash_in_double_quotes(self):
        """Test that # inside double quotes is preserved."""
        test_cases = [
            ('echo "#hello"\n', 'echo "#hello"\n', False),
            ('echo "test # comment"\n', 'echo "test # comment"\n', False),
            ('cmd="echo #test"\n', 'cmd="echo #test"\n', False),
            ('URL="http://example.com#anchor"\n', 'URL="http://example.com#anchor"\n', False),
        ]
        for input_line, expected, should_modify in test_cases:
            result, modified, _ = self.remover.remove_comments_from_line(input_line)
            self.assertEqual(result, expected, f"# in double quotes should be preserved: {input_line}")
            self.assertEqual(modified, should_modify, f"Modification flag incorrect for: {input_line}")

    def test_preserve_variable_expansion(self):
        """Test that # in variable expansions like ${var#pattern} is preserved."""
        test_cases = [
            ("file=${path#/tmp/}\n", "file=${path#/tmp/}\n", False),
            ("name=${filename##*/}\n", "name=${filename##*/}\n", False),
            ("result=${var#prefix}\n", "result=${var#prefix}\n", False),
            ("x=${y##*pattern}\n", "x=${y##*pattern}\n", False),
        ]
        for input_line, expected, should_modify in test_cases:
            result, modified, _ = self.remover.remove_comments_from_line(input_line)
            self.assertEqual(result, expected, f"# in variable expansion should be preserved: {input_line}")
            self.assertEqual(modified, should_modify, f"Modification flag incorrect for: {input_line}")

    def test_mixed_cases(self):
        """Test complex mixed cases."""
        test_cases = [
            # String with # followed by comment
            ('echo "test#value" # comment\n', 'echo "test#value"\n', True),
            # Variable expansion followed by comment
            ('file=${path#/tmp/} # get relative path\n', 'file=${path#/tmp/}\n', True),
            # Multiple strings and comment
            ('echo "a#b" "c#d" # comment\n', 'echo "a#b" "c#d"\n', True),
        ]
        for input_line, expected, should_modify in test_cases:
            result, modified, _ = self.remover.remove_comments_from_line(input_line)
            self.assertEqual(result, expected, f"Mixed case failed for: {input_line}")
            self.assertEqual(modified, should_modify, f"Modification flag incorrect for: {input_line}")

    def test_escaped_characters(self):
        """Test handling of escaped characters."""
        test_cases = [
            ('echo "test\\"quote" # comment\n', 'echo "test\\"quote"\n', True),
            ("echo 'can\\'t' # comment\n", "echo 'can\\'t'\n", True),
            ('cmd="path\\#value" # comment\n', 'cmd="path\\#value"\n', True),
        ]
        for input_line, expected, should_modify in test_cases:
            result, modified, _ = self.remover.remove_comments_from_line(input_line)
            self.assertEqual(result, expected, f"Escaped character handling failed for: {input_line}")

    def test_empty_and_whitespace_lines(self):
        """Test handling of empty lines and whitespace."""
        test_cases = [
            ("\n", "\n", False, False),
            ("  \n", "  \n", False, False),
            ("\t\n", "\t\n", False, False),
            ("", "", False, False),
        ]
        for input_line, expected, should_modify, should_be_comment_only in test_cases:
            result, modified, is_comment_only = self.remover.remove_comments_from_line(input_line)
            self.assertEqual(result, expected, f"Whitespace line handling failed for: {repr(input_line)}")
            self.assertEqual(modified, should_modify, f"Modification flag incorrect for: {repr(input_line)}")
            self.assertEqual(is_comment_only, should_be_comment_only, f"Comment-only flag incorrect for: {repr(input_line)}")

    def test_is_in_string(self):
        """Test the is_in_string helper method."""
        test_cases = [
            ('echo "test"', 6, True),   # 't' in "test"
            ('echo "test"', 0, False),  # 'e' before string
            ("echo 'test'", 6, True),   # 't' in 'test'
            ('echo "a" "b"', 9, True),  # 'b' in second string
            ('echo "a" "b"', 8, False), # space between strings
            ('"test\\"quote"', 10, True), # char after escaped quote
        ]
        for line, pos, expected in test_cases:
            result = self.remover.is_in_string(line, pos)
            self.assertEqual(result, expected,
                f"is_in_string({repr(line)}, {pos}) should be {expected}")

    def test_is_variable_expansion(self):
        """Test the is_variable_expansion helper method."""
        test_cases = [
            ('${var#pattern}', 5, True),   # # in ${var#pattern}
            ('${var##pattern}', 5, True),  # first # in ${var##pattern}
            ('${var##pattern}', 6, True),  # second # in ${var##pattern}
            ('echo # comment', 5, False),  # regular comment
            ('var#test', 3, False),        # # outside ${}
        ]
        for line, pos, expected in test_cases:
            result = self.remover.is_variable_expansion(line, pos)
            self.assertEqual(result, expected,
                f"is_variable_expansion({repr(line)}, {pos}) should be {expected}")


class TestFileProcessing(unittest.TestCase):
    """Test file processing functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.remover = ShellCommentRemover(verbose=False)

    def test_process_sample_script(self):
        """Test processing a sample shell script."""
        sample_script = """#!/bin/bash
# This is a comment
echo "Hello World" # inline comment
# Another comment
var=${path#/tmp/} # variable expansion comment
echo "test#value" # string with hash
"""
        expected = """#!/bin/bash
echo "Hello World"
var=${path#/tmp/}
echo "test#value"
"""
        # Create a temporary test
        lines = sample_script.split('\n')
        result_lines = []
        for line in lines:
            if not line and result_lines:  # Skip processing empty trailing line
                continue
            line_with_newline = line + '\n' if line else ''
            if not line_with_newline:
                continue
            new_line, _, was_comment_only = self.remover.remove_comments_from_line(line_with_newline)
            if not was_comment_only:
                result_lines.append(new_line)

        result = ''.join(result_lines)
        self.assertEqual(result.strip(), expected.strip(),
            "Sample script processing failed")


def main():
    """Run all tests."""
    # Run tests with verbose output
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Return appropriate exit code
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
