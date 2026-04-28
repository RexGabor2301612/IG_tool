TEST & VERIFICATION COMMANDS
============================

QUICK START VERIFICATION
------------------------

1. Syntax Check (verify no Python errors):
   python -m py_compile app.py
   python -m py_compile core/state/machine.py
   python -m py_compile core/etl/etl_engine.py
   python -m py_compile core/platforms/instagram.py
   python -m py_compile core/platforms/facebook.py

2. Run Unit Tests:
   python test_system_audit.py

3. Start Flask Dev Server:
   python app.py
   # Then visit http://localhost:5000 (Instagram)
   # or http://localhost:5000/facebook (Facebook)

4. Manual Browser Test:
   • Open http://localhost:5000
   • Enter a public Instagram profile: https://www.instagram.com/[username]/
   • Set scroll rounds to 2-3
   • Set start date to recent (e.g., 2026-01-01)
   • Check "Review setup" modal appears
   • Click "Run / Start Extraction"
   • Watch logs for:
     ✓ "Browser opened"
     ✓ "Checking for saved session"
     ✓ "Page readiness check passed"
     ✓ "Ready for GO signal"
   • Check GO button is now enabled
   • Click GO
   • Watch for:
     ✓ "GO signal received"
     ✓ "Starting extraction"
     ✓ "Extracted metrics" (for each post)
     ✓ "Data validated"
     ✓ "Excel saved"
   • Verify Excel file was created


VERIFICATION CHECKLIST
======================

System Components:

[ ] Browser/Session Lifecycle
    • Check: One browser/context/page per job
    • Command: grep -n "new_page()" app.py (should be 1)
    • Expected: app.py:366

[ ] Login/Readiness Gate
    • Check: Logs contain "Checking for saved session"
    • Command: grep -n "Checking for saved session" app.py
    • Expected: app.py:289

[ ] CAPTCHA Handling
    • Check: detect_verification_gate() exists and is called
    • Command: grep -n "detect_verification_gate" app.py
    • Expected: app.py:285

[ ] GO Button Logic
    • Check: request_go returns (bool, str)
    • Command: python -c "import app; c = app.JobController(app.get_platform_adapter('instagram')); result = c.request_go(); print(type(result), 'is tuple:', isinstance(result, tuple))"
    • Expected: <class 'tuple'> is tuple: True

[ ] State Machine
    • Check: StateTransition.is_valid() prevents invalid transitions
    • Command: python -c "from core.state.machine import ScrapeState, StateTransition; print(StateTransition.is_valid(ScrapeState.SETUP, ScrapeState.VALIDATION)); print(StateTransition.is_valid(ScrapeState.SETUP, ScrapeState.COLLECTION_RUNNING))"
    • Expected: True, False

[ ] Collection Loop
    • Check: Extraction logs include "Extracted metrics"
    • Command: grep -n "Extracted metrics" app.py
    • Expected: Found at app.py:473

[ ] ETL & Export
    • Check: Empty data check present
    • Command: grep -n "No data found" app.py
    • Expected: app.py:485

[ ] Logging
    • Check: 15+ critical checkpoints in code
    • Command: grep -c "self._log" app.py
    • Expected: 20+

[ ] UI Alignment
    • Check: snapshot() includes canGo flag
    • Command: grep -n '"canGo"' app.py
    • Expected: app.py:199-206

[ ] QA Tests
    • Check: test_system_audit.py exists and has 6 tests
    • Command: python test_system_audit.py
    • Expected: 6 passed


COMMAND REFERENCE
=================

Search for Key Implementations:

# Find all state transitions
grep -n "_transition(" app.py

# Find all logs
grep -n "self._log" app.py

# Find request_go implementation
grep -n "def request_go" app.py

# Find GO button check
grep -n '"canGo"' app.py

# Find ETL validation
grep -n "if self.posts_success == 0" app.py

# Find error message handling
grep -n "error_reason" app.py

# Count total logging statements
grep "self._log" app.py | wc -l

# Find session saving
grep -n "save_storage_state" app.py

# Find session loading
grep -n "auto_login_if_needed" app.py


PRODUCTION TEST SCENARIO
========================

Full End-to-End Test:

1. Clear previous data:
   rm -rf storage_states/instagram_auth.json
   rm instagram_extract.xlsx 2>/dev/null
   rm logs.db 2>/dev/null

2. Start server:
   python app.py &

3. Wait for server to start:
   sleep 2

4. Create test request:
   curl -X POST http://localhost:5000/api/validate \
     -H "Content-Type: application/json" \
     -d '{
       "instagramLink": "https://www.instagram.com/instagram/",
       "scrollRounds": 1,
       "startDate": "2026-01-01",
       "latestMode": true,
       "outputFile": "test_extract.xlsx"
     }'

5. Expected response:
   {"success": true, "message": "Instagram setup validated.", ...}

6. Start extraction:
   curl -X POST http://localhost:5000/api/start \
     -H "Content-Type: application/json" \
     -d '{
       "instagramLink": "https://www.instagram.com/instagram/",
       "scrollRounds": 1,
       "startDate": "2026-01-01",
       "latestMode": true,
       "outputFile": "test_extract.xlsx"
     }'

7. Check status (polling):
   for i in {1..30}; do
     curl -s http://localhost:5000/api/status | python -m json.tool | grep -E '"status"|"activeTask"|"canGo"'
     sleep 2
   done

8. Verify file created:
   ls -la test_extract.xlsx

9. Check logs for errors:
   curl -s http://localhost:5000/api/status | python -m json.tool | grep -A5 '"logs"'

10. Cleanup:
    kill %1  # Kill Flask server


DEBUG MODE
==========

Enable detailed logging:

# Add to app.py before Flask creation:
import logging
logging.basicConfig(level=logging.DEBUG)

# Or run with debug:
FLASK_ENV=development python app.py

# Or with verbose Playwright logging:
PLAYWRIGHT_VERBOSE=1 python app.py


FILE LOCATIONS
==============

Key files created/modified:

app.py                          - Main Flask app (39 changes)
test_system_audit.py            - Unit tests (NEW)
AUDIT_REPORT.md                 - Detailed audit (NEW)
IMPLEMENTATION_SUMMARY.md       - This summary (NEW)

Core modules:
core/state/machine.py           - State machine
core/etl/etl_engine.py          - ETL pipeline
core/platforms/instagram.py     - Instagram adapter
core/platforms/facebook.py      - Facebook adapter
core/logging/logger.py          - Logger

Platform scripts:
instagram_to_excel.py           - Instagram scraper
facebook_to_excel.py            - Facebook scraper

Templates:
templates/dashboard.html        - UI frontend

Data directories:
storage_states/                 - Session cache
data/                           - Excel exports
logs.db                         - Log storage


FINAL CHECKLIST
===============

Before Deployment:

[ ] All imports work: python -c "import app"
[ ] No syntax errors: python -m py_compile app.py
[ ] Tests pass: python test_system_audit.py
[ ] Audit report generated: test AUDIT_REPORT.md exists
[ ] Implementation doc exists: test IMPLEMENTATION_SUMMARY.md exists
[ ] Flask starts: python app.py (check "Running on...")
[ ] Dashboard loads: open http://localhost:5000
[ ] No production secrets in code: grep -r "password\|SECRET\|KEY" . (should be empty)
[ ] Session files configured: grep -n "storage_states" app.py
[ ] Logs streaming: check WebSocket in dashboard
[ ] State machine enforced: grep -n "_transition" app.py (should be 10+)

Expected Results:

✓ All checks pass
✓ System ready for production deployment
✓ Users have clear error messages
✓ All data validated before export
✓ Session persisted between runs
✓ Comprehensive logging for troubleshooting
✓ State machine prevents invalid transitions
✓ No CAPTCHA bypassed
✓ GO button only enabled when ready
✓ Browser lifecycle managed correctly


SUPPORT
=======

If tests fail:

1. Check Python version: python --version (need 3.8+)
2. Check dependencies: pip install -r requirements.txt
3. Check Playwright: playwright install
4. Run with verbose logging: PLAYWRIGHT_VERBOSE=1 python app.py
5. Check logs: tail -f logs.db (if implemented)
6. Check browser logs: check ~/Downloads for browser profile logs

Common Issues:

"Browser session not created"
→ Check if Playwright is installed: pip install -r requirements.txt

"Page not ready"
→ Scroll_rounds too low, date range too restrictive, or page loading slow

"No data found"
→ Scroll_rounds exhausted or all posts outside date range

"Excel export failed"
→ Check disk space, file permissions, or Excel not corrupt


NEXT STEPS
==========

1. Deploy to production: git push origin main
2. Monitor logs for errors: watch logs.db
3. Collect user feedback on error messages
4. Run 30-day metrics: success rates, error types, timing
5. Schedule 60-day review for optimization


Thank you for using the Instagram/Facebook Scraper System!
All systems are GO ✓
