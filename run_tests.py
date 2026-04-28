#!/usr/bin/env python3
"""Quick test runner to verify all fixes."""

import subprocess
import sys

result = subprocess.run([sys.executable, "test_system_audit.py"], cwd=r"S:\IG_analyzer")
sys.exit(result.returncode)
