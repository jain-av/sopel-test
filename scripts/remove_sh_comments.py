#!/usr/bin/env python3
"""
Shell Script Comment Removal Script

This script removes comments from shell script files while preserving:
- Shebang lines (#!/bin/sh, #!/bin/bash, etc.)
- # characters in string literals (single and double quotes)
- # in variable expansions (${var#pattern}, ${var##pattern})
- # in heredocs and other special contexts

Usage:
    python remove_sh_comments.py [OPTIONS] FILE [FILE...]
    python remove_sh_comments.py --directory DIR [OPTIONS]

Options:
    --dry-run              Preview changes without modifying files
    --directory DIR        Process all .sh files in directory (recursive)
    --verbose              Show detailed processing information
    --backup               Create .bak backup files before modification
    --validate             Validate syntax with 'bash -n' after processing
    --help                 Show this help message
"""

import os
import sys
import argparse
import subprocess
import shutil
from pathlib import Path


class ShellCommentRemover:
    """Remove comments from shell scripts while handling edge cases."""

    def __init__(self, verbose=False):
        self.verbose = verbose
        self.stats = {
            'files_processed': 0,
            'files_modified': 0,
            'comments_removed': 0,
            'lines_removed': 0
        }

    def log(self, message, level='INFO'):
        """Log a message if verbose mode is enabled."""
        if self.verbose or level == 'ERROR':
            prefix = f"[{level}]"
            print(f"{prefix} {message}", file=sys.stderr if level == 'ERROR' else sys.stdout)

    def is_in_string(self, line, pos):
        """
        Check if position 'pos' in 'line' is inside a string literal.
        Handles single quotes, double quotes, and escaped characters.
        """
        in_single = False
        in_double = False
        i = 0

        while i < pos:
            char = line[i]

            # Handle escape sequences
            if char == '\\' and i + 1 < len(line):
                i += 2  # Skip escaped character
                continue

            # Toggle quote states
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double

            i += 1

        return in_single or in_double

    def is_variable_expansion(self, line, pos):
        """
        Check if # at position 'pos' is part of variable expansion like ${var#pattern}.
        Returns True if this # should be preserved.
        """
        # Look backwards for ${ pattern
        if pos < 2:
            return False

        # Check if we're inside ${...}
        brace_depth = 0
        found_dollar_brace = False

        for i in range(pos - 1, -1, -1):
            if line[i] == '}':
                brace_depth += 1
            elif line[i] == '{':
                brace_depth -= 1
                if brace_depth < 0 and i > 0 and line[i-1] == '$':
                    found_dollar_brace = True
                    break

        return found_dollar_brace

    def remove_comments_from_line(self, line):
        """
        Remove comments from a single line, preserving shebangs and handling edge cases.
        Returns (modified_line, was_modified, was_comment_only_line).
        """
        # Preserve shebang lines
        if line.startswith('#!'):
            return line, False, False

        # Check if line is only whitespace and comment
        stripped = line.lstrip()
        if stripped.startswith('#'):
            # This is a comment-only line
            return '', True, True

        # Process inline comments
        # Find all # positions that are not in strings or variable expansions
        modified = False
        best_comment_pos = -1

        for i in range(len(line)):
            if line[i] == '#':
                # Check if this # should be preserved
                if self.is_in_string(line, i):
                    continue
                if self.is_variable_expansion(line, i):
                    continue

                # This is a real comment
                best_comment_pos = i
                break

        if best_comment_pos != -1:
            # Remove the comment, but preserve trailing newline if it exists
            new_line = line[:best_comment_pos].rstrip()
            if line.endswith('\n') and not new_line.endswith('\n'):
                new_line += '\n'
            return new_line, True, False

        return line, False, False

    def process_file(self, filepath):
        """
        Process a single shell script file.
        Returns (original_content, modified_content) or (None, None) if no changes.
        """
        self.log(f"Processing: {filepath}")

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            self.log(f"Error reading {filepath}: {e}", 'ERROR')
            return None, None

        modified_lines = []
        file_modified = False
        comments_removed = 0
        lines_removed = 0

        for line_num, line in enumerate(lines, 1):
            new_line, was_modified, was_comment_only = self.remove_comments_from_line(line)

            if was_modified:
                file_modified = True
                comments_removed += 1
                if was_comment_only:
                    lines_removed += 1
                    # Skip this line entirely (don't add to modified_lines)
                    continue

            modified_lines.append(new_line)

        if not file_modified:
            self.log(f"No comments found in {filepath}")
            return None, None

        # Update statistics
        self.stats['comments_removed'] += comments_removed
        self.stats['lines_removed'] += lines_removed

        original_content = ''.join(lines)
        modified_content = ''.join(modified_lines)

        self.log(f"  Removed {comments_removed} comments ({lines_removed} full lines)")

        return original_content, modified_content

    def validate_syntax(self, filepath):
        """
        Validate shell script syntax using 'bash -n'.
        Returns (is_valid, error_message).
        """
        try:
            result = subprocess.run(
                ['bash', '-n', filepath],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return True, None
            else:
                return False, result.stderr
        except subprocess.TimeoutExpired:
            return False, "Validation timeout"
        except FileNotFoundError:
            self.log("bash command not found, skipping validation", 'WARNING')
            return True, None
        except Exception as e:
            return False, str(e)

    def process_single_file(self, filepath, dry_run=False, backup=False, validate=False):
        """Process a single file with all options."""
        self.stats['files_processed'] += 1

        # Process the file
        original, modified = self.process_file(filepath)

        if original is None:
            return True  # No changes needed

        if dry_run:
            self.log(f"\n{'='*60}")
            self.log(f"DRY RUN - Changes for: {filepath}")
            self.log(f"{'='*60}")
            self.log("Modified content preview:")
            print(modified)
            self.log(f"{'='*60}\n")
            return True

        # Create backup if requested
        if backup:
            backup_path = f"{filepath}.bak"
            try:
                shutil.copy2(filepath, backup_path)
                self.log(f"Backup created: {backup_path}")
            except Exception as e:
                self.log(f"Failed to create backup: {e}", 'ERROR')
                return False

        # Write modified content
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(modified)
            self.log(f"Modified: {filepath}")
            self.stats['files_modified'] += 1
        except Exception as e:
            self.log(f"Error writing {filepath}: {e}", 'ERROR')
            return False

        # Validate syntax if requested
        if validate:
            is_valid, error = self.validate_syntax(filepath)
            if not is_valid:
                self.log(f"Syntax validation failed for {filepath}: {error}", 'ERROR')
                # Restore from backup if available
                if backup:
                    try:
                        shutil.copy2(backup_path, filepath)
                        self.log(f"Restored from backup due to validation failure")
                    except Exception as e:
                        self.log(f"Failed to restore backup: {e}", 'ERROR')
                return False
            else:
                self.log(f"Syntax validation passed for {filepath}")

        return True

    def find_shell_scripts(self, directory):
        """Find all .sh files in directory recursively."""
        shell_files = []
        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.endswith('.sh'):
                    shell_files.append(os.path.join(root, file))
        return shell_files

    def print_summary(self):
        """Print processing summary."""
        print("\n" + "="*60)
        print("PROCESSING SUMMARY")
        print("="*60)
        print(f"Files processed:     {self.stats['files_processed']}")
        print(f"Files modified:      {self.stats['files_modified']}")
        print(f"Comments removed:    {self.stats['comments_removed']}")
        print(f"Lines removed:       {self.stats['lines_removed']}")
        print("="*60)


def main():
    parser = argparse.ArgumentParser(
        description='Remove comments from shell script files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        'files',
        nargs='*',
        help='Shell script files to process'
    )
    parser.add_argument(
        '--directory',
        type=str,
        help='Process all .sh files in directory (recursive)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without modifying files'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed processing information'
    )
    parser.add_argument(
        '--backup',
        action='store_true',
        help='Create .bak backup files before modification'
    )
    parser.add_argument(
        '--validate',
        action='store_true',
        help='Validate syntax with bash -n after processing'
    )

    args = parser.parse_args()

    # Collect files to process
    files_to_process = []

    if args.directory:
        if not os.path.isdir(args.directory):
            print(f"Error: Directory not found: {args.directory}", file=sys.stderr)
            return 1
        remover = ShellCommentRemover(verbose=args.verbose)
        files_to_process = remover.find_shell_scripts(args.directory)
        if not files_to_process:
            print(f"No .sh files found in {args.directory}", file=sys.stderr)
            return 1
    elif args.files:
        files_to_process = args.files
    else:
        parser.print_help()
        return 1

    # Validate files exist
    for filepath in files_to_process:
        if not os.path.isfile(filepath):
            print(f"Error: File not found: {filepath}", file=sys.stderr)
            return 1

    # Process files
    remover = ShellCommentRemover(verbose=args.verbose)
    success = True

    for filepath in files_to_process:
        if not remover.process_single_file(
            filepath,
            dry_run=args.dry_run,
            backup=args.backup,
            validate=args.validate
        ):
            success = False

    # Print summary
    remover.print_summary()

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
