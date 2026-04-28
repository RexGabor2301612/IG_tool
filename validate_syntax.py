#!/usr/bin/env python
"""Quick syntax validation for Facebook scraper fixes."""
import sys

errors = []

# Test 1: facebook_to_excel syntax
try:
    import py_compile
    py_compile.compile('facebook_to_excel.py', doraise=True)
    print("✓ facebook_to_excel.py - SYNTAX OK")
except Exception as e:
    errors.append(f"facebook_to_excel.py: {str(e)}")
    print(f"✗ facebook_to_excel.py - SYNTAX ERROR: {e}")

# Test 2: app.py syntax
try:
    py_compile.compile('app.py', doraise=True)
    print("✓ app.py - SYNTAX OK")
except Exception as e:
    errors.append(f"app.py: {str(e)}")
    print(f"✗ app.py - SYNTAX ERROR: {e}")

# Test 3: facebook platform adapter
try:
    py_compile.compile('core/platforms/facebook.py', doraise=True)
    print("✓ core/platforms/facebook.py - SYNTAX OK")
except Exception as e:
    errors.append(f"core/platforms/facebook.py: {str(e)}")
    print(f"✗ core/platforms/facebook.py - SYNTAX ERROR: {e}")

# Test 4: Import facebook_to_excel module
try:
    import facebook_to_excel
    print("✓ facebook_to_excel module imports successfully")
except Exception as e:
    errors.append(f"Import facebook_to_excel: {str(e)}")
    print(f"✗ facebook_to_excel import failed: {e}")

# Test 5: Check key functions exist
try:
    from facebook_to_excel import (
        wait_for_scroll_stabilization,
        validate_facebook_feed_ready,
        sanitize_facebook_dataset,
        collect_post_links,
    )
    print("✓ All key functions exist in facebook_to_excel")
except Exception as e:
    errors.append(f"Key functions: {str(e)}")
    print(f"✗ Key functions missing: {e}")

if errors:
    print(f"\n❌ {len(errors)} ERROR(S) FOUND:")
    for err in errors:
        print(f"  - {err}")
    sys.exit(1)
else:
    print(f"\n✅ ALL SYNTAX CHECKS PASSED")
    sys.exit(0)
