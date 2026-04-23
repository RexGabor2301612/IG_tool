#!/usr/bin/env bash
set -o errexit

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Playwright's Python package does not include browser binaries.
# Render must download Chromium during build, otherwise runtime launch fails with:
# "Executable doesn't exist at /opt/render/.cache/ms-playwright/..."
python -m playwright install chromium
