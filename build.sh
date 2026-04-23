#!/usr/bin/env bash
set -o errexit

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Playwright's Python package does not include browser binaries.
# Newer Playwright versions may launch chromium through chromium-headless-shell
# in headless mode, so install both explicitly.
python -m playwright install chromium chromium-headless-shell
python -m playwright install --list
