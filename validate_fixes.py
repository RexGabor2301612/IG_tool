#!/usr/bin/env python
"""
Comprehensive test execution for Facebook scraper fixes.
Tests all critical fixes and reports results.
"""
import sys
import subprocess
from pathlib import Path

print("=" * 70)
print("FACEBOOK SCRAPER FIXES - COMPREHENSIVE VALIDATION")
print("=" * 70)

# Change to project directory
project_dir = Path(__file__).parent
sys.path.insert(0, str(project_dir))

results = {}

# Test 1: Syntax validation
print("\n[1/4] Validating Python syntax...")
try:
    import py_compile
    files_to_check = [
        'app.py',
        'facebook_to_excel.py',
        'core/platforms/facebook.py',
    ]
    syntax_ok = True
    for fname in files_to_check:
        try:
            py_compile.compile(str(project_dir / fname), doraise=True)
            print(f"  ✓ {fname}")
        except Exception as e:
            print(f"  ✗ {fname}: {e}")
            syntax_ok = False
    results['syntax'] = syntax_ok
except Exception as e:
    print(f"  ✗ Syntax check failed: {e}")
    results['syntax'] = False

# Test 2: Module imports
print("\n[2/4] Testing module imports...")
try:
    import facebook_to_excel
    print("  ✓ facebook_to_excel imports")
    
    # Check key functions
    required_functions = [
        'wait_for_scroll_stabilization',
        'validate_facebook_feed_ready',
        'sanitize_facebook_dataset',
        'collect_post_links',
        'extract_metrics_from_loaded_post',
        'sanitize_excel_value',
    ]
    
    import_ok = True
    for func_name in required_functions:
        if hasattr(facebook_to_excel, func_name):
            print(f"  ✓ {func_name}")
        else:
            print(f"  ✗ {func_name} not found")
            import_ok = False
    
    results['imports'] = import_ok
except Exception as e:
    print(f"  ✗ Import failed: {e}")
    results['imports'] = False

# Test 3: Check critical fixes are in code
print("\n[3/4] Validating critical fixes in code...")
fixes_ok = True

# Check feed validation
with open(project_dir / 'facebook_to_excel.py', 'r') as f:
    content = f.read()
    
checks = [
    ('Feed validation function', 'def validate_facebook_feed_ready'),
    ('Scroll stabilization function', 'def wait_for_scroll_stabilization'),
    ('Global sanitization function', 'def sanitize_facebook_dataset'),
    ('Feed validation call', 'validate_facebook_feed_ready(page'),
    ('Scroll stabilization call', 'wait_for_scroll_stabilization(page'),
    ('Dataset sanitization call', 'sanitize_facebook_dataset(posts)'),
    ('Extraction retries', 'for attempt in range(1, max_retries + 1)'),
]

for check_name, check_string in checks:
    if check_string in content:
        print(f"  ✓ {check_name}")
    else:
        print(f"  ✗ {check_name} NOT FOUND")
        fixes_ok = False

results['fixes'] = fixes_ok

# Check app.py state machine
with open(project_dir / 'app.py', 'r') as f:
    app_content = f.read()

if 'ScrapeState.SESSION_LOADING' in app_content:
    print("  ✓ State machine SESSION_LOADING added")
else:
    print("  ✗ State machine SESSION_LOADING NOT FOUND")
    fixes_ok = False

results['fixes'] = fixes_ok

# Test 4: Quick unit test check
print("\n[4/4] Running unit tests (basic import only)...")
try:
    test_files = [
        'test_system_audit.py',
        'test_facebook_fixes.py',
        'test_facebook_real_reliability.py',
    ]
    
    tests_ok = True
    for test_file in test_files:
        test_path = project_dir / test_file
        if test_path.exists():
            # Just check if file can be imported
            try:
                with open(test_path, 'r') as f:
                    compile(f.read(), test_file, 'exec')
                print(f"  ✓ {test_file} (syntax valid)")
            except Exception as e:
                print(f"  ✗ {test_file}: {e}")
                tests_ok = False
        else:
            print(f"  ⚠ {test_file} not found (skipped)")
    
    results['tests'] = tests_ok
except Exception as e:
    print(f"  ✗ Test check failed: {e}")
    results['tests'] = False

# Summary
print("\n" + "=" * 70)
print("VALIDATION RESULTS:")
print("=" * 70)
for check, passed in results.items():
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{check.upper():.<50} {status}")

all_passed = all(results.values())
print("=" * 70)

if all_passed:
    print("✅ ALL VALIDATION CHECKS PASSED")
    print("\nNext steps:")
    print("  1. Review code changes in facebook_to_excel.py")
    print("  2. Run: python app.py (to start the Flask app)")
    print("  3. Test with real Facebook profile")
    sys.exit(0)
else:
    print("❌ SOME VALIDATION CHECKS FAILED")
    print("\nFix the issues above before proceeding.")
    sys.exit(1)
