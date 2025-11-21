# Comment Removal Scripts

This directory contains scripts for removing comments from Python and shell script files in the sopel-test codebase.

## Overview

Two complementary scripts handle comment removal for different file types:
- **remove_py_comments.py** - For Python files (.py)
- **remove_sh_comments.py** - For shell scripts (.sh)

Both scripts follow the same design principles: safety, correctness, and transparency.

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

## remove_sh_comments.py

Shell script comment removal tool that safely removes comments from .sh files while preserving shell syntax elements.

### Features

- **Preserves shebang lines**: `#!/bin/bash`, `#!/bin/sh` lines at the top of files are kept
- **Preserves variable expansions**: `${var#pattern}` and `${var##pattern}` syntax
- **Safe string handling**: `#` inside single and double quotes is preserved
- **Syntax validation**: Uses `bash -n` to verify modified scripts still parse correctly
- **Dry-run mode**: Preview changes without modifying files
- **Backup support**: Create .bak files before modification
- **Comprehensive testing**: Unit tests cover 40+ edge cases

### Usage

```bash
# Dry run on a single file
python scripts/remove_sh_comments.py --dry-run ci_build.sh

# Process with backup and validation
python scripts/remove_sh_comments.py --backup --validate ci_build.sh

# Process all .sh files in a directory
python scripts/remove_sh_comments.py --directory . --backup --validate
```

See [README_SH_COMMENTS.md](README_SH_COMMENTS.md) for detailed documentation.

## Example Output

### Python Script
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

### Shell Script
```
============================================================
PROCESSING SUMMARY
============================================================
Files processed:     5
Files modified:      4
Comments removed:    23
Lines removed:       15
============================================================
```
