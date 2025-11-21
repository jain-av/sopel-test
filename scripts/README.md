# Comment Removal Scripts

This directory contains scripts for removing comments from Python and shell script files in the sopel-test codebase.

## remove_py_comments.py

Python script that safely removes `#`-style comments from Python files while preserving docstrings and code structure.

### Features

- **Preserves docstrings**: Module, class, function, and attribute docstrings remain intact
- **Preserves shebang lines**: `#!/usr/bin/env python` lines at the top of files are kept
- **Safe inline comment removal**: Correctly handles `#` inside string literals
- **Syntax validation**: Uses `ast.parse()` to ensure modified files are still valid Python
- **Dry-run mode**: Preview changes without modifying files
- **Comprehensive logging**: Track which files are modified and how many comments are removed

### Usage

```bash
# Dry run on a single file
python scripts/remove_py_comments.py --dry-run path/to/file.py

# Process a single file
python scripts/remove_py_comments.py path/to/file.py

# Process all Python files in a directory recursively
python scripts/remove_py_comments.py --directory ./sopel

# Dry run on entire directory
python scripts/remove_py_comments.py --directory ./sopel --dry-run

# Process with verbose logging
python scripts/remove_py_comments.py --directory ./sopel --verbose
```

### Options

- `--directory`, `-d`: Directory containing Python files to process
- `--recursive`, `-r`: Process directories recursively (default: True)
- `--no-recursive`: Do not process directories recursively
- `--dry-run`, `-n`: Preview changes without modifying files
- `--verbose`, `-v`: Enable verbose logging

### Edge Cases Handled

- `#` inside string literals (single, double, and triple quotes)
- URLs containing `#` (e.g., `https://example.com#anchor`)
- Escaped `#` characters
- Shebang lines at the start of executable scripts
- Comments on the same line as code (inline comments)
- Comment-only lines

### Testing

Run the test suite to verify functionality:

```bash
python scripts/test_remove_py_comments.py
```

Run quick validation:

```bash
python scripts/validate_script.py
```

## Safety Features

1. **Syntax validation**: Each modified file is validated with `ast.parse()` before writing
2. **Original file validation**: Files with syntax errors are skipped
3. **Error tracking**: Files that fail processing are logged with error details
4. **Dry-run mode**: Test changes before applying them
5. **Detailed logging**: All operations are logged for audit purposes

## Example Output

```
2025-11-21 10:30:15 - INFO - Found 124 Python files to process
2025-11-21 10:30:15 - INFO - [DRY RUN] Would modify sopel/__init__.py
2025-11-21 10:30:15 - INFO - [DRY RUN] Would modify sopel/bot.py
...
======================================================================
SUMMARY
======================================================================
Mode: DRY RUN (no files were actually modified)
Files that would be modified: 98
Comments removed: 1817
======================================================================
```
