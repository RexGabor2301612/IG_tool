#!/usr/bin/env python3
"""Inline verification of test fixes."""

import tempfile
import shutil
from pathlib import Path

# Test 1: Verify cross-platform temp directory creation
print("\n✓ Test 1: Cross-Platform Temp Directory")
print("=" * 60)

temp_dir = Path(tempfile.gettempdir()) / "test_verify"
print(f"  Temp dir: {temp_dir}")

temp_dir.mkdir(parents=True, exist_ok=True)
assert temp_dir.exists(), "Failed to create temp directory"
print(f"  ✓ Directory created successfully")

# Cleanup
shutil.rmtree(temp_dir, ignore_errors=True)
assert not temp_dir.exists(), "Failed to cleanup"
print(f"  ✓ Cleanup successful")


# Test 2: Verify GO button error message
print("\n✓ Test 2: GO Button Error Message")
print("=" * 60)

from app import JobController
from core.platforms.registry import get_platform_adapter

adapter = get_platform_adapter("instagram")
controller = JobController(adapter)

# Test without browser
success, reason = controller.request_go()
print(f"  Reason: {reason}")
assert not success, "Should reject when not ready"
assert "browser session" in reason.lower() or "not started" in reason.lower() or "not ready" in reason.lower(), f"Unexpected reason: {reason}"
print(f"  ✓ Error message matches expected pattern")


# Test 3: Verify tempfile imports in test file
print("\n✓ Test 3: Test File Imports")
print("=" * 60)

import ast
test_file = Path("test_system_audit.py")
with open(test_file) as f:
    tree = ast.parse(f.read())

imports = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            imports.append(alias.name)
    elif isinstance(node, ast.ImportFrom):
        imports.append(node.module)

required = ["tempfile", "shutil", "pathlib"]
for req in required:
    if req in imports or any(req in imp for imp in imports if imp):
        print(f"  ✓ {req} imported")
    else:
        raise AssertionError(f"Missing import: {req}")


print("\n" + "=" * 60)
print("ALL INLINE VERIFICATIONS PASSED ✅")
print("=" * 60)
print("\nNow run: python test_system_audit.py")
print("Expected: 6 passed, 0 failed\n")
