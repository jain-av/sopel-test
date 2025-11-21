#!/usr/bin/env python3
"""
Script to safely remove # comments from Python files while preserving docstrings.

This script uses the ast module to parse Python files and identify comments that can
be safely removed without affecting code functionality. It preserves:
- Docstrings (module, class, function, and attribute docstrings)
- Shebang lines (#!/usr/bin/env python)
- Code structure and functionality

It removes:
- Single-line comments (lines starting with # after whitespace)
- Inline comments (text after # on code lines)
"""

import argparse
import ast
import logging
import os
import re
import sys
import tokenize
from io import StringIO
from pathlib import Path
from typing import List, Tuple


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CommentRemover:
    """Removes comments from Python source code while preserving docstrings."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.files_modified = 0
        self.comments_removed = 0
        self.files_with_errors = []

    def is_inside_string(self, line: str, pos: int) -> bool:
        """
        Check if position is inside a string literal.
        Handles single quotes, double quotes, and triple quotes.
        """
        in_single = False
        in_double = False
        in_triple_single = False
        in_triple_double = False
        i = 0
        escaped = False

        while i < pos:
            char = line[i]

            if escaped:
                escaped = False
                i += 1
                continue

            if char == '\\':
                escaped = True
                i += 1
                continue

            if i + 2 < len(line):
                three_chars = line[i:i+3]
                if three_chars == '"""':
                    if not (in_single or in_triple_single):
                        in_triple_double = not in_triple_double
                        i += 3
                        continue
                elif three_chars == "'''":
                    if not (in_double or in_triple_double):
                        in_triple_single = not in_triple_single
                        i += 3
                        continue

            if char == '"' and not (in_single or in_triple_single or in_triple_double):
                in_double = not in_double
            elif char == "'" and not (in_double or in_triple_double or in_triple_single):
                in_single = not in_single

            i += 1

        return in_single or in_double or in_triple_single or in_triple_double

    def remove_inline_comment(self, line: str) -> str:
        """
        Remove inline comment from a line, handling strings correctly.
        Returns the line with inline comment removed.
        """
        hash_pos = line.find('#')

        while hash_pos != -1:
            if not self.is_inside_string(line, hash_pos):
                self.comments_removed += 1
                return line[:hash_pos].rstrip() + '\n' if line.endswith('\n') else line[:hash_pos].rstrip()

            hash_pos = line.find('#', hash_pos + 1)

        return line

    def is_comment_only_line(self, line: str) -> bool:
        """Check if a line contains only a comment (after stripping whitespace)."""
        stripped = line.lstrip()
        return stripped.startswith('#') and not stripped.startswith('#!')

    def process_file_content(self, content: str, filepath: str) -> str:
        """
        Process file content to remove comments while preserving structure.
        Returns the modified content.
        """
        lines = content.splitlines(keepends=True)
        if not lines:
            return content

        result_lines = []
        file_comments_removed = 0

        for i, line in enumerate(lines):
            if i == 0 and line.startswith('#!'):
                result_lines.append(line)
                continue

            if self.is_comment_only_line(line):
                file_comments_removed += 1
                continue

            if '#' in line:
                original_line = line
                processed_line = self.remove_inline_comment(line)

                if processed_line != original_line:
                    file_comments_removed += 1

                if processed_line.strip():
                    result_lines.append(processed_line)
            else:
                result_lines.append(line)

        logger.debug(f"Removed {file_comments_removed} comments from {filepath}")

        return ''.join(result_lines)

    def validate_syntax(self, content: str, filepath: str) -> bool:
        """Validate that the modified content is still valid Python syntax."""
        try:
            ast.parse(content)
            return True
        except SyntaxError as e:
            logger.error(f"Syntax error in {filepath} after comment removal: {e}")
            self.files_with_errors.append((filepath, str(e)))
            return False

    def process_file(self, filepath: Path) -> bool:
        """
        Process a single Python file to remove comments.
        Returns True if file was modified successfully.
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                original_content = f.read()
        except Exception as e:
            logger.error(f"Failed to read {filepath}: {e}")
            self.files_with_errors.append((str(filepath), str(e)))
            return False

        try:
            ast.parse(original_content)
        except SyntaxError as e:
            logger.warning(f"Skipping {filepath} - original file has syntax errors: {e}")
            self.files_with_errors.append((str(filepath), f"Original syntax error: {e}"))
            return False

        modified_content = self.process_file_content(original_content, str(filepath))

        if modified_content == original_content:
            logger.debug(f"No changes needed for {filepath}")
            return False

        if not self.validate_syntax(modified_content, str(filepath)):
            logger.error(f"Modified content for {filepath} failed syntax validation")
            return False

        if self.dry_run:
            logger.info(f"[DRY RUN] Would modify {filepath}")
            logger.debug(f"[DRY RUN] Preview of changes:\n{self._generate_diff_preview(original_content, modified_content)}")
            return True

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(modified_content)
            logger.info(f"Modified {filepath}")
            self.files_modified += 1
            return True
        except Exception as e:
            logger.error(f"Failed to write {filepath}: {e}")
            self.files_with_errors.append((str(filepath), str(e)))
            return False

    def _generate_diff_preview(self, original: str, modified: str, context_lines: int = 3) -> str:
        """Generate a simple preview of changes for dry-run mode."""
        orig_lines = original.splitlines()
        mod_lines = modified.splitlines()

        preview = []
        preview.append(f"Lines before: {len(orig_lines)}, Lines after: {len(mod_lines)}")
        preview.append(f"Lines removed: {len(orig_lines) - len(mod_lines)}")

        return '\n'.join(preview)

    def process_directory(self, directory: Path, recursive: bool = True) -> None:
        """Process all Python files in a directory."""
        pattern = "**/*.py" if recursive else "*.py"

        python_files = list(directory.glob(pattern))
        logger.info(f"Found {len(python_files)} Python files to process")

        for filepath in python_files:
            if filepath.is_file():
                self.process_file(filepath)

    def print_summary(self) -> None:
        """Print summary of operations performed."""
        logger.info("=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)

        if self.dry_run:
            logger.info(f"Mode: DRY RUN (no files were actually modified)")

        logger.info(f"Files that would be modified: {self.files_modified}" if self.dry_run else f"Files modified: {self.files_modified}")
        logger.info(f"Comments removed: {self.comments_removed}")

        if self.files_with_errors:
            logger.warning(f"Files with errors: {len(self.files_with_errors)}")
            for filepath, error in self.files_with_errors:
                logger.warning(f"  - {filepath}: {error}")

        logger.info("=" * 70)


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Remove # comments from Python files while preserving docstrings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run on a single file
  python remove_py_comments.py --dry-run file.py

  # Process a single file
  python remove_py_comments.py file.py

  # Process all Python files in a directory
  python remove_py_comments.py --directory ./sopel

  # Process directory recursively (default)
  python remove_py_comments.py --directory ./sopel --recursive

  # Dry run on entire directory
  python remove_py_comments.py --directory ./sopel --dry-run
        """
    )

    parser.add_argument(
        'files',
        nargs='*',
        help='Python files to process'
    )

    parser.add_argument(
        '--directory', '-d',
        type=str,
        help='Directory containing Python files to process'
    )

    parser.add_argument(
        '--recursive', '-r',
        action='store_true',
        default=True,
        help='Process directories recursively (default: True)'
    )

    parser.add_argument(
        '--no-recursive',
        action='store_false',
        dest='recursive',
        help='Do not process directories recursively'
    )

    parser.add_argument(
        '--dry-run', '-n',
        action='store_true',
        help='Preview changes without modifying files'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if not args.files and not args.directory:
        parser.error("Must specify either files or --directory")

    remover = CommentRemover(dry_run=args.dry_run)

    if args.directory:
        directory = Path(args.directory)
        if not directory.exists():
            logger.error(f"Directory does not exist: {directory}")
            sys.exit(1)

        if not directory.is_dir():
            logger.error(f"Not a directory: {directory}")
            sys.exit(1)

        remover.process_directory(directory, recursive=args.recursive)

    for filepath in args.files:
        path = Path(filepath)
        if not path.exists():
            logger.error(f"File does not exist: {filepath}")
            continue

        if not path.is_file():
            logger.error(f"Not a file: {filepath}")
            continue

        remover.process_file(path)

    remover.print_summary()

    if remover.files_with_errors:
        sys.exit(1)

    sys.exit(0)


if __name__ == '__main__':
    main()
