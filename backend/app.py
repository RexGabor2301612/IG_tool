"""Production-grade Flask backend for scraper system."""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify, request, send_file
from flask_sock import Sock

from core.logging import ProductionLogger, LogStreamBroadcaster
from core.state import ScrapeJobState, ScrapeState, StateTransition
from core.session import PlaywrightSessionManager, SessionConfig
from core.extraction import SelectorFactory, Platform, DataExtractor, ExtractionConfig
from core.etl import ETLPipeline


# Initialize Flask app
app = Flask(__name__)
sock = Sock(app)

# Configuration
DATA_DIR = Path("data")
SESSIONS_DIR = Path("storage_states")
LOGS_DIR = Path("logs")

DATA_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Initialize core systems
logger = ProductionLogger(persistence_dir=LOGS_DIR)
broadcaster = LogStreamBroadcaster()
session_manager = PlaywrightSessionManager(sessions_dir=SESSIONS_DIR)

# Global job state
JOB = ScrapeJobState()
JOB_LOCK = threading.RLock()
JOB_THREAD: Optional[threading.Thread] = None


# ============================================================================
# LOGGING UTILITIES
# ============================================================================

def add_log(level: str, action: str, details: str):
    """Add log and broadcast to WebSocket clients."""
    entry = logger.log(getattr(logger.LogLevel, level), action, details)
    if entry:
        broadcaster.broadcast_log_entry(entry.to_dict())


def emit_progress(progress_pct: int, current_post: str = "", scroll_round: int = 0):
    """Emit progress update to WebSocket clients."""
    progress_data = {
        "progress": min(100, max(0, progress_pct)),
        "current_post": current_post,
        "scroll_round": scroll_round,
    }
    broadcaster.broadcast_progress(progress_data)


def emit_status_update():
    """Emit job status update to WebSocket clients."""
    with JOB_LOCK:
        status = JOB.snapshot()
    broadcaster.broadcast_status_update(status)


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route("/")
def home():
    """Dashboard home page."""
    return render_template(
        "dashboard.html",
        platform="instagram",
        version="1.0.0",
    )


@app.route("/facebook")
def facebook_home():
    """Facebook dashboard."""
    return render_template(
        "dashboard.html",
        platform="facebook",
        version="1.0.0",
    )


@app.post("/api/validate")
def validate_inputs():
    """Validate user inputs before scraping."""
    data = request.get_json(silent=True) or {}
    errors = []
    
    # Get platform
    platform = data.get("platform", "instagram").lower()
    if platform not in ["instagram", "facebook"]:
        errors.append("Invalid platform")
    
    # Validate URL
    url = data.get("url", "").strip()
    if not url:
        errors.append("URL is required")
    elif not url.startswith("http"):
        errors.append("URL must start with http(s)")
    
    # Validate dates if provided
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    if start_date and end_date:
        try:
            start = datetime.fromisoformat(start_date)
            end = datetime.fromisoformat(end_date)
            if start > end:
                errors.append("Start date must be before end date")
        except Exception as e:
            errors.append(f"Invalid date format: {e}")
    
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    
    return jsonify({"ok": True, "config": {
        "platform": platform,
        "url": url,
        "start_date": start_date,
        "end_date": end_date,
    }})


@app.post("/api/start")
def start_scrape():
    """Start new scraping job."""
    global JOB_THREAD
    
    data = request.get_json(silent=True) or {}
    
    # Check if job already running
    if JOB_THREAD and JOB_THREAD.is_alive():
        add_log("WARN", "Job already running", "Cannot start multiple jobs simultaneously")
        return jsonify({
            "ok": False,
            "errors": ["A scraping job is already running"],
            "status": JOB.snapshot(),
        }), 409
    
    # Validate inputs
    response = validate_inputs()
    if response[1] != 200:
        return response
    
    # Reset job state
    with JOB_LOCK:
        JOB.reset()
        JOB.transition_to(ScrapeState.SETUP)
    
    add_log("INFO", "Job initialized", f"Platform: {data.get('platform', 'instagram')}")
    emit_status_update()
    
    # Start job thread
    JOB_THREAD = threading.Thread(
        target=run_scrape_job,
        args=(data,),
        daemon=True,
    )
    JOB_THREAD.start()
    
    return jsonify({
        "ok": True,
        "status": JOB.snapshot(),
    })


@app.post("/api/go")
def go_signal():
    """User confirms GO to start scraping."""
    with JOB_LOCK:
        if JOB.current_state != ScrapeState.PAGE_READY:
            add_log("WARN", "Blocked GO", f"System state is {JOB.current_state.value}, not ready")
            return jsonify({
                "ok": False,
                "errors": [f"System not ready (state={JOB.current_state.value})"],
                "status": JOB.snapshot(),
            }), 409
        
        success, msg = JOB.request_go()
        if not success:
            add_log("WARN", "GO rejected", msg)
            return jsonify({
                "ok": False,
                "errors": [msg],
                "status": JOB.snapshot(),
            }), 409
        
        # Transition to collection
        JOB.transition_to(ScrapeState.COLLECTION_RUNNING, "User confirmed GO")
        add_log("SUCCESS", "GO signal accepted", "Starting collection engine")
    
    emit_status_update()
    return jsonify({
        "ok": True,
        "status": JOB.snapshot(),
    })


@app.post("/api/cancel")
def cancel_scrape():
    """Cancel running job."""
    with JOB_LOCK:
        success, msg = JOB.request_cancel()
        if not success:
            return jsonify({
                "ok": False,
                "errors": [msg],
                "status": JOB.snapshot(),
            }), 409
        
        add_log("WARN", "Cancellation requested", "Stopping at next checkpoint")
    
    emit_status_update()
    return jsonify({
        "ok": True,
        "status": JOB.snapshot(),
    })


@app.post("/api/pause")
def pause_scrape():
    """Pause collection."""
    with JOB_LOCK:
        success, msg = JOB.request_pause()
        if not success:
            return jsonify({
                "ok": False,
                "errors": [msg],
                "status": JOB.snapshot(),
            }), 409
        
        add_log("INFO", "Paused", "Collection paused by user")
    
    emit_status_update()
    return jsonify({
        "ok": True,
        "status": JOB.snapshot(),
    })


@app.post("/api/resume")
def resume_scrape():
    """Resume paused collection."""
    with JOB_LOCK:
        success, msg = JOB.resume_collection()
        if not success:
            return jsonify({
                "ok": False,
                "errors": [msg],
                "status": JOB.snapshot(),
            }), 409
        
        # Transition back to running
        JOB.transition_to(ScrapeState.COLLECTION_RUNNING, "Resumed by user")
        add_log("INFO", "Resumed", "Collection resumed")
    
    emit_status_update()
    return jsonify({
        "ok": True,
        "status": JOB.snapshot(),
    })


@app.get("/api/status")
def get_status():
    """Get current job status."""
    with JOB_LOCK:
        status = JOB.snapshot()
    return jsonify(status)


@app.post("/api/clear-logs")
def clear_logs():
    """Clear all logs."""
    logger.clear()
    add_log("INFO", "Logs cleared", "Log buffer reset")
    return jsonify({"ok": True})


@app.get("/api/logs")
def get_logs():
    """Get recent logs."""
    logs = logger.get_recent(100)
    return jsonify([log.to_dict() for log in logs])


@app.get("/api/download")
def download_file():
    """Download exported file."""
    with JOB_LOCK:
        status = JOB.snapshot()
    
    output_file = status.get("output_file")
    if not output_file or not Path(output_file).exists():
        return jsonify({"ok": False, "errors": ["No export file available"]}), 404
    
    return send_file(
        output_file,
        as_attachment=True,
        download_name=Path(output_file).name,
    )


# ============================================================================
# WEBSOCKET ENDPOINTS
# ============================================================================

@sock.route("/ws/dashboard")
def dashboard_socket(ws):
    """WebSocket connection for real-time updates."""
    client_id = broadcaster.register_ws(ws)
    
    try:
        # Send initial status
        with JOB_LOCK:
            initial_status = JOB.snapshot()
        
        ws.send(json.dumps({
            "type": "initial",
            "data": initial_status,
        }))
        
        # Listen for messages
        while True:
            try:
                message = ws.receive()
                if message is None:
                    break
                
                # Handle control messages here if needed
                data = json.loads(message)
                if data.get("type") == "ping":
                    ws.send(json.dumps({"type": "pong"}))
            
            except Exception as e:
                print(f"WebSocket error: {e}")
                break
    
    except Exception as e:
        print(f"WebSocket exception: {e}")
    
    finally:
        broadcaster.unregister_ws(client_id)


# ============================================================================
# BACKGROUND JOB
# ============================================================================

def run_scrape_job(config: dict):
    """Main scraping job (runs in background thread)."""
    try:
        platform = config.get("platform", "instagram")
        url = config.get("url", "")
        
        add_log("INFO", "Initializing browser", f"Platform: {platform}")
        
        # Phase 1: Setup
        with JOB_LOCK:
            JOB.transition_to(ScrapeState.BROWSER_INIT)
        emit_status_update()
        
        # Phase 2: Create session
        session_config = SessionConfig(
            platform=platform,
            headless=False,
            storage_state_file=SESSIONS_DIR / f"{platform}_auth.json",
        )
        
        session_id, error = session_manager.create_session(session_config)
        if error:
            add_log("ERROR", "Browser creation failed", error)
            with JOB_LOCK:
                JOB.transition_to(ScrapeState.COLLECTION_FAILED, error)
            emit_status_update()
            return
        
        with JOB_LOCK:
            JOB.browser_id = session_id
            JOB.transition_to(ScrapeState.SESSION_LOADING)
        emit_status_update()
        add_log("SUCCESS", "Browser created", f"Session: {session_id}")
        
        # Phase 3: Navigate to page
        page = session_manager.get_page(session_id)
        if not page:
            add_log("ERROR", "Failed to get page", "Session page is None")
            with JOB_LOCK:
                JOB.transition_to(ScrapeState.COLLECTION_FAILED)
            emit_status_update()
            return
        
        page.goto(url, wait_until="domcontentloaded")
        add_log("INFO", "Navigated to page", url)
        
        # Phase 4: Check for login
        page.wait_for_timeout(2000)  # Let page settle
        
        selectors = SelectorFactory.get_by_name(platform)
        login_form_exists = page.query_selector(selectors.login_form) is not None
        
        if login_form_exists:
            add_log("WARN", "Login required", "Waiting for manual login")
            with JOB_LOCK:
                JOB.transition_to(ScrapeState.WAITING_LOGIN)
                JOB.login_required = True
            emit_status_update()
            
            # Wait up to 10 minutes for login
            login_deadline = time.time() + 600
            while time.time() < login_deadline:
                if page.query_selector(selectors.login_form) is None:
                    add_log("SUCCESS", "Login completed", "User logged in successfully")
                    break
                
                if JOB.cancel_requested:
                    raise Exception("Cancelled during login")
                
                page.wait_for_timeout(2000)
            else:
                add_log("ERROR", "Login timeout", "Could not detect login completion")
                with JOB_LOCK:
                    JOB.transition_to(ScrapeState.COLLECTION_FAILED)
                emit_status_update()
                return
        
        else:
            add_log("INFO", "Already logged in", "Session detected")
            with JOB_LOCK:
                JOB.login_required = False
        
        # Phase 5: Page readiness
        add_log("INFO", "Checking page readiness", "Verifying posts container visible")
        with JOB_LOCK:
            JOB.transition_to(ScrapeState.PAGE_READINESS_CHECK)
        emit_status_update()
        
        # Wait for posts to load
        try:
            page.wait_for_selector(selectors.posts_container, timeout=10000)
            add_log("SUCCESS", "Page ready", "Posts container detected")
        except Exception:
            add_log("WARN", "Timeout waiting for posts", "Continuing with fallback ready mode")
        
        with JOB_LOCK:
            JOB.transition_to(ScrapeState.PAGE_READY)
            JOB.page_ready = True
        emit_status_update()
        
        # Phase 6: Wait for user confirmation
        add_log("INFO", "Waiting for user", "Click GO button to start collection")
        
        # Wait for GO signal
        go_received = JOB.wait_for_go(timeout=600)  # 10-minute timeout
        
        if not go_received or JOB.cancel_requested:
            add_log("WARN", "Job cancelled", "User cancelled before collection started")
            with JOB_LOCK:
                JOB.transition_to(ScrapeState.COLLECTION_CANCELLED)
            emit_status_update()
            return
        
        # Phase 7: Collection engine
        add_log("INFO", "Starting collection engine", "Beginning to scroll and extract")
        
        # Simple demo collection (scroll and extract a few posts)
        scroll_round = 0
        posts_found = 0
        
        for scroll_round in range(1, 6):  # Demo: 5 scroll rounds
            add_log("INFO", f"Scroll round {scroll_round}", "Scrolling down to load more posts")
            page.evaluate("window.scrollBy(0, 500)")
            page.wait_for_timeout(1000)
            
            # Extract posts on this screen
            post_items = page.query_selector_all(selectors.post_item)
            posts_found += len(post_items)
            
            add_log("SUCCESS", f"Scroll {scroll_round}", f"+{len(post_items)} new posts")
            emit_progress(int((scroll_round / 5) * 100), current_post=f"Scroll {scroll_round}", scroll_round=scroll_round)
            
            if JOB.cancel_requested or JOB.pause_requested:
                break
        
        # Phase 8: Export
        add_log("INFO", "Preparing export", f"Total posts found: {posts_found}")
        
        output_file = DATA_DIR / f"{platform}_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        etl = ETLPipeline(DATA_DIR, platform)
        success, export_file = etl.export_excel(output_file)
        
        if success:
            add_log("SUCCESS", "Export completed", f"File: {export_file}")
            with JOB_LOCK:
                JOB.transition_to(ScrapeState.COLLECTION_COMPLETED)
        else:
            add_log("ERROR", "Export failed", export_file)
            with JOB_LOCK:
                JOB.transition_to(ScrapeState.COLLECTION_FAILED)
        
        emit_status_update()
        add_log("INFO", "Job finished", "Scraping complete")
    
    except Exception as e:
        add_log("ERROR", "Job exception", str(e))
        with JOB_LOCK:
            JOB.transition_to(ScrapeState.COLLECTION_FAILED, str(e))
        emit_status_update()
    
    finally:
        # Cleanup
        if "session_id" in locals():
            success, msg = session_manager.close_session(session_id, save_state=True)
            if success:
                add_log("INFO", "Session closed", msg)


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(e):
    return jsonify({"ok": False, "errors": ["Not found"]}), 404


@app.errorhandler(500)
def server_error(e):
    add_log("ERROR", "Server error", str(e))
    return jsonify({"ok": False, "errors": ["Server error"]}), 500


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"Starting production server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
