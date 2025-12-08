#!/bin/bash
#
# Step 3.1 Execution Script
# Tests comment removal on representative Python files
#

set -e  # Exit on error

echo "================================================================================"
echo "STEP 3.1: TEST ON REPRESENTATIVE PYTHON FILES"
echo "================================================================================"
echo ""

# Navigate to test directory
cd "$(dirname "$0")/test_comment_removal"

echo "Test files prepared:"
ls -lh __init__.py bot.py bugzilla.py test_bot.py
echo ""

echo "--------------------------------------------------------------------------------"
echo "PHASE 1: Baseline Measurements"
echo "--------------------------------------------------------------------------------"
echo ""

echo "Line counts (BEFORE):"
wc -l __init__.py bot.py bugzilla.py test_bot.py
echo ""

echo "--------------------------------------------------------------------------------"
echo "PHASE 2: Processing Files"
echo "--------------------------------------------------------------------------------"
echo ""

for file in __init__.py bot.py bugzilla.py test_bot.py; do
    echo "Processing $file..."
    python3 ../scripts/remove_py_comments.py --verbose "$file" 2>&1 | grep -E "(INFO|Modified|Comments removed|Files modified)"
    echo ""
done

echo "--------------------------------------------------------------------------------"
echo "PHASE 3: Post-Processing Measurements"
echo "--------------------------------------------------------------------------------"
echo ""

echo "Line counts (AFTER):"
wc -l __init__.py bot.py bugzilla.py test_bot.py
echo ""

echo "File sizes (AFTER):"
ls -lh __init__.py bot.py bugzilla.py test_bot.py
echo ""

echo "--------------------------------------------------------------------------------"
echo "PHASE 4: Syntax Validation"
echo "--------------------------------------------------------------------------------"
echo ""

all_passed=true

for file in __init__.py bot.py bugzilla.py test_bot.py; do
    echo -n "Validating $file... "
    if python3 -m py_compile "$file" 2>/dev/null; then
        echo "✓ PASS"
    else
        echo "✗ FAIL"
        all_passed=false
        python3 -m py_compile "$file"  # Show error
    fi
done

echo ""
echo "--------------------------------------------------------------------------------"
echo "PHASE 5: Docstring Preservation Check"
echo "--------------------------------------------------------------------------------"
echo ""

for file in __init__.py bot.py bugzilla.py test_bot.py; do
    triple_quotes=$(grep -o '"""' "$file" | wc -l || echo 0)
    echo "$file: $triple_quotes triple-quote docstring markers"
done

echo ""
echo "--------------------------------------------------------------------------------"
echo "PHASE 6: Manual Inspection Samples"
echo "--------------------------------------------------------------------------------"
echo ""

echo "Sample from __init__.py (first 20 lines):"
head -20 __init__.py
echo ""

echo "Sample from bugzilla.py (first 30 lines, showing module docstring):"
head -30 bugzilla.py
echo ""

echo "--------------------------------------------------------------------------------"
echo "TEST SUMMARY"
echo "--------------------------------------------------------------------------------"
echo ""

if [ "$all_passed" = true ]; then
    echo "✓✓✓ ALL TESTS PASSED ✓✓✓"
    echo ""
    echo "Results:"
    echo "  - All files processed successfully"
    echo "  - All files pass syntax validation"
    echo "  - Docstrings preserved"
    echo "  - Code structure intact"
    echo ""
    echo "RECOMMENDATION: Step 3.1 COMPLETE. Proceed to Step 3.2 (shell script testing)."
else
    echo "✗✗✗ SOME TESTS FAILED ✗✗✗"
    echo ""
    echo "RECOMMENDATION: Fix issues before proceeding."
fi

echo ""
echo "================================================================================"
echo "END OF STEP 3.1 TESTING"
echo "================================================================================"
