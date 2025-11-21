# Shell Script Comment Removal Tool

## Overview

`remove_sh_comments.py` is a Python script that safely removes comments from shell script files (`.sh`) while preserving critical elements and handling edge cases properly.

## Features

### What is Preserved
- **Shebang lines**: `#!/bin/bash`, `#!/bin/sh`, etc.
- **Hash in strings**: Both single (`'#test'`) and double quotes (`"#test"`)
- **Variable expansions**: `${var#pattern}` and `${var##pattern}` patterns
- **Escaped characters**: Properly handles `\"`, `\'`, and `\#`
- **Code structure**: All executable commands and logic remain intact

### What is Removed
- **Comment-only lines**: Lines containing only `#` comments
- **Inline comments**: Comments after commands (e.g., `echo hello # comment`)
- **Block comments**: Multiple consecutive comment lines

## Installation

No installation required. The script uses only Python standard library modules.

**Requirements:**
- Python 3.6 or higher
- `bash` command (optional, for validation)

## Usage

### Basic Usage

```bash
# Preview changes without modifying (dry-run)
python3 scripts/remove_sh_comments.py --dry-run script.sh

# Process a single file
python3 scripts/remove_sh_comments.py script.sh

# Process multiple files
python3 scripts/remove_sh_comments.py file1.sh file2.sh file3.sh
```

### Process Directory

```bash
# Process all .sh files in current directory (recursive)
python3 scripts/remove_sh_comments.py --directory .

# Process specific directory
python3 scripts/remove_sh_comments.py --directory ./contrib/githooks
```

### Advanced Options

```bash
# Create backups before modification
python3 scripts/remove_sh_comments.py --backup script.sh

# Validate syntax after processing
python3 scripts/remove_sh_comments.py --validate script.sh

# Combine options with verbose output
python3 scripts/remove_sh_comments.py --backup --validate --verbose --dry-run script.sh

# Process directory with all safety features
python3 scripts/remove_sh_comments.py --directory . --backup --validate --verbose
```

## Command-Line Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview changes without modifying files |
| `--directory DIR` | Process all .sh files in directory (recursive) |
| `--verbose` | Show detailed processing information |
| `--backup` | Create .bak backup files before modification |
| `--validate` | Validate syntax with `bash -n` after processing |
| `--help` | Show help message |

## Examples

### Example 1: Dry-run on ci_build.sh

```bash
python3 scripts/remove_sh_comments.py --dry-run --verbose ci_build.sh
```

**Before:**
```bash
#!/bin/sh -x
# This script performs most of the same steps as the Travis build.

clean () {
    find . -name '*.pyc' -exec rm {} \;  # Remove compiled Python files
    rm -rf build __pycache__
}

clean  # Run cleanup
```

**After:**
```bash
#!/bin/sh -x

clean () {
    find . -name '*.pyc' -exec rm {} \;
    rm -rf build __pycache__
}

clean
```

### Example 2: Process with Variable Expansion

**Before:**
```bash
#!/bin/bash
# Extract filename from path
filename=${fullpath##*/}  # Remove path, keep filename
basename=${filename%.*}    # Remove extension
```

**After:**
```bash
#!/bin/bash
filename=${fullpath##*/}
basename=${filename%.*}
```

Note: The `#` in `${fullpath##*/}` is correctly preserved as it's part of variable expansion syntax.

### Example 3: Preserve Hashes in Strings

**Before:**
```bash
#!/bin/bash
# Define URL and anchors
URL="http://example.com#section"  # URL with anchor
echo "Password: test#123"          # Hash in password
echo 'Comment char: #'             # Hash in single quotes
```

**After:**
```bash
#!/bin/bash
URL="http://example.com#section"
echo "Password: test#123"
echo 'Comment char: #'
```

## Edge Cases Handled

### 1. Hash in String Literals
```bash
echo "test#value"     # Preserved
echo 'test#value'     # Preserved
```

### 2. Variable Expansion Patterns
```bash
${var#pattern}        # Remove shortest match from beginning
${var##pattern}       # Remove longest match from beginning
```

### 3. Escaped Characters
```bash
echo "Quote: \"test\""  # Escaped quotes handled correctly
path="dir\\#file"       # Escaped hash in string
```

### 4. Mixed Cases
```bash
echo "a#b" && echo "c#d" # comment    # Both hashes in strings preserved
file=${path#/tmp/} # get basename     # Variable expansion preserved, comment removed
```

## Testing

Run the validation script to verify functionality:

```bash
python3 scripts/validate_sh_script.py
```

Run comprehensive unit tests:

```bash
python3 scripts/test_remove_sh_comments.py
```

## Safety Features

1. **Dry-run mode**: Preview all changes before applying
2. **Backup creation**: Automatically create `.bak` files
3. **Syntax validation**: Verify scripts with `bash -n` after processing
4. **Automatic rollback**: Restore from backup if validation fails
5. **Comprehensive logging**: Track all modifications and errors

## Recommended Workflow

For maximum safety, follow this workflow:

```bash
# Step 1: Dry-run to preview changes
python3 scripts/remove_sh_comments.py --dry-run --verbose ci_build.sh

# Step 2: Process with backup and validation
python3 scripts/remove_sh_comments.py --backup --validate ci_build.sh

# Step 3: Test the modified script
bash -n ci_build.sh  # Syntax check
./ci_build.sh        # Actual execution (in safe environment)

# Step 4: If issues occur, restore from backup
cp ci_build.sh.bak ci_build.sh
```

## Statistics and Reporting

The script provides a summary after processing:

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

## Troubleshooting

### Validation Fails
If `--validate` reports syntax errors:
- Check the error message from `bash -n`
- Restore from `.bak` file if available
- Review the changes with `--dry-run`
- Report issues with specific edge cases

### Hash Not Removed
If a comment is not removed:
- Verify the `#` is not inside quotes
- Check for variable expansion patterns
- Use `--verbose` to see detailed processing

### Hash Incorrectly Removed
If a `#` that should be preserved is removed:
- This indicates a bug in edge case handling
- Restore from backup
- Report the specific pattern for investigation

## Files Created by Script

| File | Purpose |
|------|---------|
| `*.sh.bak` | Backup copies (with `--backup` option) |

## Implementation Details

### Algorithm
1. Parse each line character by character
2. Track quote state (single, double, or none)
3. Handle escape sequences (`\`, `\"`, `\'`)
4. Detect variable expansion patterns (`${...}`)
5. Identify comment start position (first unquoted `#` not in `${}`)
6. Remove comment while preserving structure

### Limitations
- Does not handle heredocs specially (treats them as regular lines)
- Does not parse shell script AST (line-by-line processing only)
- Assumes UTF-8 encoding
- Requires `bash` command for validation feature

## Related Files

- `remove_py_comments.py` - Python comment removal script
- `test_remove_sh_comments.py` - Unit tests
- `validate_sh_script.py` - Quick validation script

## Version Information

**Created for**: Sopel IRC Bot codebase comment removal (Step 2.2)
**Language**: Python 3.6+
**Dependencies**: Standard library only
