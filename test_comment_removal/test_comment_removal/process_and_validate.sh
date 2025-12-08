#!/bin/bash
# Process test files and validate results

echo "========================================="
echo "STEP 3.1: Testing Comment Removal Script"
echo "========================================="
echo ""

cd "$(dirname "$0")"

FILES="__init__.py bot.py bugzilla.py test_bot.py"

echo "Files to process:"
for f in $FILES; do
    lines=$(wc -l < "$f")
    size=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null)
    echo "  - $f: $lines lines, $size bytes"
done

echo ""
echo "========================================="
echo "Processing files..."
echo "========================================="
echo ""

# Process each file
for f in $FILES; do
    echo "Processing $f..."
    python3 ../scripts/remove_py_comments.py "$f" 2>&1 | tail -3
done

echo ""
echo "========================================="
echo "Results after processing:"
echo "========================================="
echo ""

for f in $FILES; do
    lines=$(wc -l < "$f")
    size=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null)
    echo "  - $f: $lines lines, $size bytes"
done

echo ""
echo "========================================="
echo "Syntax Validation:"
echo "========================================="
echo ""

all_passed=true
for f in $FILES; do
    echo -n "  $f: "
    if python3 -m py_compile "$f" 2>/dev/null; then
        echo "✓ PASS"
    else
        echo "✗ FAIL"
        all_passed=false
    fi
done

echo ""
if [ "$all_passed" = true ]; then
    echo "✓✓✓ ALL TESTS PASSED ✓✓✓"
else
    echo "✗✗✗ SOME TESTS FAILED ✗✗✗"
fi
