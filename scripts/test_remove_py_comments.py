#!/usr/bin/env python3
"""Unit tests for remove_py_comments.py"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from remove_py_comments import CommentRemover


def test_basic_comment_removal():
    """Test basic comment removal."""
    content = '''
def foo():
    x = 1
    return x
'''
    remover = CommentRemover(dry_run=True)
    result = remover.process_file_content(content, 'test.py')

    assert 'def foo():' in result
    assert 'x = 1' in result


def test_inline_comment_removal():
    """Test inline comment removal."""
    content = '''def foo():
    x = 1
    return x
'''
    remover = CommentRemover(dry_run=True)
    result = remover.process_file_content(content, 'test.py')

    assert 'x = 1' in result
    assert 'return x' in result


def test_preserve_strings_with_hash():
    """Test that # inside strings is preserved."""
    content = '''url = "https://example.com#anchor"
text = 'This has # inside'
'''
    remover = CommentRemover(dry_run=True)
    result = remover.process_file_content(content, 'test.py')

    assert 'https://example.com#anchor' in result
    assert 'This has # inside' in result


def test_preserve_shebang():
    """Test that shebang lines are preserved."""
    content = '''#!/usr/bin/env python3
def foo():
    pass
'''
    remover = CommentRemover(dry_run=True)
    result = remover.process_file_content(content, 'test.py')

    assert '#!/usr/bin/env python3' in result
    assert 'def foo():' in result


def test_docstring_preservation():
    """Test that docstrings are preserved."""
    content = '''"""Module docstring."""

def foo():
    """Function docstring."""
    pass

class Bar:
    """Class docstring."""
    pass
'''
    remover = CommentRemover(dry_run=True)
    result = remover.process_file_content(content, 'test.py')

    assert '"""Module docstring."""' in result
    assert '"""Function docstring."""' in result
    assert '"""Class docstring."""' in result


def test_syntax_validation():
    """Test syntax validation works."""
    remover = CommentRemover(dry_run=True)

    valid_code = 'x = 1\ny = 2\n'
    assert remover.validate_syntax(valid_code, 'test.py')

    invalid_code = 'x = \ny = 2\n'
    assert not remover.validate_syntax(invalid_code, 'test.py')


def test_is_inside_string():
    """Test string detection logic."""
    remover = CommentRemover(dry_run=True)

    line = 'text = "hello # world"'
    hash_pos = line.find('#')
    assert remover.is_inside_string(line, hash_pos)

    line = 'x = 1'
    hash_pos = line.find('#')
    assert hash_pos == -1

    line = "text = 'hello # world'"
    hash_pos = line.find('#')
    assert remover.is_inside_string(line, hash_pos)


def test_triple_quote_handling():
    """Test handling of triple quotes."""
    remover = CommentRemover(dry_run=True)

    line = 'text = """This has # inside"""'
    hash_pos = line.find('#')
    assert remover.is_inside_string(line, hash_pos)

    line = "text = '''This has # inside'''"
    hash_pos = line.find('#')
    assert remover.is_inside_string(line, hash_pos)


def run_tests():
    """Run all tests."""
    tests = [
        test_basic_comment_removal,
        test_inline_comment_removal,
        test_preserve_strings_with_hash,
        test_preserve_shebang,
        test_docstring_preservation,
        test_syntax_validation,
        test_is_inside_string,
        test_triple_quote_handling,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            print(f"✓ {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__}: Unexpected error: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    return failed == 0


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
