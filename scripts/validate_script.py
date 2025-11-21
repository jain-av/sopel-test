#!/usr/bin/env python3
"""Quick validation that the script can be imported and basic functionality works."""

import sys
import ast
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from remove_py_comments import CommentRemover
    print("✓ Script imported successfully")
except Exception as e:
    print(f"✗ Failed to import script: {e}")
    sys.exit(1)

try:
    remover = CommentRemover(dry_run=True)
    print("✓ CommentRemover instantiated successfully")
except Exception as e:
    print(f"✗ Failed to instantiate CommentRemover: {e}")
    sys.exit(1)

test_content = '''#!/usr/bin/env python3
"""Module docstring."""

def foo():
    """Function docstring."""
    x = 1
    return x

class Bar:
    """Class docstring."""
    pass
'''

try:
    result = remover.process_file_content(test_content, 'test.py')
    print("✓ process_file_content executed successfully")

    assert '#!/usr/bin/env python3' in result, "Shebang not preserved"
    assert '"""Module docstring."""' in result, "Module docstring not preserved"
    assert '"""Function docstring."""' in result, "Function docstring not preserved"
    assert '"""Class docstring."""' in result, "Class docstring not preserved"

    print("✓ Docstrings and shebang preserved correctly")

    ast.parse(result)
    print("✓ Result is valid Python syntax")

except Exception as e:
    print(f"✗ Processing failed: {e}")
    sys.exit(1)

test_with_hash = 'url = "https://example.com#anchor"'
try:
    result = remover.process_file_content(test_with_hash, 'test.py')
    assert '#anchor' in result, "Hash in string not preserved"
    print("✓ Hash inside strings preserved correctly")
except Exception as e:
    print(f"✗ Hash in string test failed: {e}")
    sys.exit(1)

print("\n✅ All validation checks passed!")
print("The script is ready to use.")
