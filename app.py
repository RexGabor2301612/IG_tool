from __future__ import annotations

import base64
import json
import threading
import time
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request, send_file
from flask_sock import Sock
from playwright.sync_api import sync_playwright

import app_fb
import instagram_to_excel as scraper
from modules.comment_collector_ig import collect_all_comments_ig
from modules.excel_exporter import save_posts_excel, add_comments_sheet, update_sentiment_counts
from modules.sentiment_classifier import classify_comments

# PRODUCTION SYSTEM: Core modules MANDATORY - no fallback allowed
from core.logging.logger import ProductionLogger, LogLevel, LogEntry
from core.logging.streaming import LogStreamBroadcaster
from core.state.machine import ScrapeState, ScrapeJobState as CoreScrapeJobState
from core.session.manager import PlaywrightSessionManager, SessionConfig
from core.extraction.extractor import DataExtractor, ExtractionConfig, ExtractedPost
from core.extraction.selectors import Platform, SelectorFactory
from core.etl.etl_engine import ETLPipeline, DataBuffer

print("✅ PRODUCTION CORE MODULES IMPORTED SUCCESSFULLY")


app = Flask(__name__)
sock = Sock(app)


class ScrapeCancelled(Exception):
    """Raised when the user cancels an active scrape job."""


@dataclass
class WebScrapeConfig:
    profile_url: str
    scroll_rounds: int
    start_date: datetime
    end_date: Optional[datetime]
    output_file: str
    overwrite: bool = False


class DashboardClient:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.lock = threading.Lock()

    def send(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.ws.send(json.dumps(payload))


class DashboardHub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.clients: list[DashboardClient] = []

    def register(self, ws) -> DashboardClient:
        client = DashboardClient(ws)
        with self.lock:
            self.clients.append(client)
        return client

    def unregister(self, client: DashboardClient) -> None:
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)

    def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        with self.lock:
            clients = list(self.clients)

        stale: list[DashboardClient] = []
        payload = {"type": event_type, "data": data}
        for client in clients:
            try:
                client.send(payload)
            except Exception:
                stale.append(client)

        if stale:
            with self.lock:
                for client in stale:
                    if client in self.clients:
                        self.clients.remove(client)


class LivePreviewState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self, note: str = "Waiting for live browser preview.") -> None:
        self.frame_b64 = ""
        self.width = 0
        self.height = 0
        self.note = note
        self.url = ""
        self.updated_at = ""
        self.last_capture_monotonic = 0.0

    def update(self, *, frame_b64: str, width: int, height: int, note: str, url: str) -> dict[str, Any]:
        payload = {
            "image": frame_b64,
            "width": width,
            "height": height,
            "note": note,
            "url": url,
            "updatedAt": datetime.now().strftime("%H:%M:%S"),
        }
        with self.lock:
            self.frame_b64 = frame_b64
            self.width = width
            self.height = height
            self.note = note
            self.url = url
            self.updated_at = payload["updatedAt"]
            self.last_capture_monotonic = time.monotonic()
        return payload

    def snapshot(self) -> Optional[dict[str, Any]]:
        with self.lock:
            return {
                "image": self.frame_b64,
                "width": self.width,
                "height": self.height,
                "note": self.note,
                "url": self.url,
                "updatedAt": self.updated_at,
            }

    def can_capture(self, interval_seconds: float) -> bool:
        with self.lock:
            return time.monotonic() - self.last_capture_monotonic >= interval_seconds


class LiveCommandBus:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queue: list[dict[str, Any]] = []
        self.paused = False

    def reset(self) -> None:
        with self.lock:
            self.queue = []
            self.paused = False

    def push(self, command: dict[str, Any]) -> None:
        action = str(command.get("action", "")).strip()
        with self.lock:
            if action == "pause":
                self.paused = True
                return
            if action == "resume":
                self.paused = False
                return
            self.queue.append(command)

    def drain(self) -> list[dict[str, Any]]:
        with self.lock:
            items = list(self.queue)
            self.queue.clear()
            return items

    def is_paused(self) -> bool:
        with self.lock:
            return self.paused


class ScrapeJobState:
    """Thread-safe state shared by the scraper worker and Flask API routes."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.status = "idle"
        self.active_task = "Waiting for input"
        self.current_post = ""
        self.current_scroll_round = 0
        self.total_scroll_rounds = 0
        self.posts_found = 0
        self.posts_checked = 0
        self.posts_processed = 0
        self.posts_in_range = 0
        self.posts_success = 0
        self.posts_skipped_newer = 0
        self.posts_skipped_older = 0
        self.posts_skipped_unknown = 0
        self.failed_extractions = 0
        self.errors = 0
        self.progress = 0
        self.output_file = ""
        self.config_summary: dict[str, Any] = {}
        self.logs: list[dict[str, str]] = []
        self.cancel_requested = False
        self.go_requested = False
        self.browser_session_created = False
        self.profile_ready = False
        self.login_required = False
        self.verification_required = False
        self.ready_to_scrape = False
        self.browser_url = ""
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        # Comment collection phase
        self.awaiting_comments = False
        self.comments_requested = False
        self.skip_comments_requested = False
        self.collected_post_urls: list[str] = []

    def add_log(self, level: str, action: str, details: str = "") -> None:
        entry: dict[str, str]
        with self.lock:
            entry = {
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": level.upper(),
                "action": action,
                "details": details,
            }
            self.logs.insert(0, entry)
            self.logs = self.logs[:250]
        
        # PRODUCTION: Always log to ProductionLogger - MANDATORY
        try:
            log_level_map = {
                "INFO": LogLevel.INFO,
                "SUCCESS": LogLevel.SUCCESS,
                "WARN": LogLevel.WARN,
                "ERROR": LogLevel.ERROR,
            }
            log_level = log_level_map.get(level.upper(), LogLevel.INFO)
            PRODUCTION_LOGGER.log(log_level, action, details)
        except Exception as logger_exc:
            print(f"❌ CRITICAL: ProductionLogger.log() failed: {logger_exc}")
            raise  # FAIL FAST - logging is mandatory
        
        broadcast_dashboard_event("log", entry)
        broadcast_job_snapshot(include_logs=False)

    def update(self, **kwargs: Any) -> None:
        with self.lock:
            for key, value in kwargs.items():
                setattr(self, key, value)
        broadcast_job_snapshot(include_logs=False)

    def request_cancel(self) -> bool:
        with self.lock:
            if self.status not in {"preparing", "loading_session", "waiting_login", "waiting_verification", "captcha", "ready", "running", "paused", "stopping"}:
                return False

            self.cancel_requested = True
            self.status = "stopping"
            self.active_task = "Stopping scrape job"
            return True

    def should_cancel(self) -> bool:
        with self.lock:
            return self.cancel_requested

    def request_go(self) -> bool:
        with self.lock:
            if (
                self.status != "ready"
                or self.go_requested
                or not self.browser_session_created
                or not self.profile_ready
                or self.status == "captcha"
                or self.verification_required
                or not self.ready_to_scrape
            ):
                return False
            self.go_requested = True
            return True

    def should_go(self) -> bool:
        with self.lock:
            return self.go_requested

    def request_collect_comments(self) -> bool:
        with self.lock:
            if not self.awaiting_comments or self.comments_requested:
                return False
            self.comments_requested = True
            self.awaiting_comments = False
            return True

    def request_skip_comments(self) -> bool:
        with self.lock:
            if not self.awaiting_comments or self.skip_comments_requested:
                return False
            self.skip_comments_requested = True
            self.awaiting_comments = False
            return True

    def should_collect_comments(self) -> bool:
        with self.lock:
            return self.comments_requested

    def should_skip_comments(self) -> bool:
        with self.lock:
            return self.skip_comments_requested

    def snapshot(self, include_logs: bool = True) -> dict[str, Any]:
        with self.lock:
            ui_state_map = {
                "preparing": "preparing",
                "loading_session": "preparing",
                "waiting_login": "waiting_login",
                "waiting_verification": "captcha",
                "captcha": "captcha",
                "ready": "ready",
                "running": "scraping",
                "completed": "done",
            }
            ui_state = ui_state_map.get(self.status, self.status)
            eligible_total = self.posts_in_range if self.posts_in_range > 0 else (self.posts_success + self.failed_extractions)
            if eligible_total > 0:
                success_rate = round(100 * self.posts_success / eligible_total)
            elif self.posts_checked > 0 and self.failed_extractions == 0:
                success_rate = 100
            else:
                success_rate = 0
            health = max(0, 100 - min(self.errors * 8, 70))
            snapshot = {
                "status": self.status,
                "state": ui_state,
                "activeTask": self.active_task,
                "currentPost": self.current_post,
                "currentScrollRound": self.current_scroll_round,
                "scrollRound": self.current_scroll_round,
                "totalScrollRounds": self.total_scroll_rounds,
                "maxScrollRounds": self.total_scroll_rounds,
                "postsFound": self.posts_found,
                "postsChecked": self.posts_checked,
                "postsProcessed": self.posts_processed,
                "postsInRange": self.posts_in_range,
                "postsSuccess": self.posts_success,
                "postsSkippedNewer": self.posts_skipped_newer,
                "postsSkippedOlder": self.posts_skipped_older,
                "postsSkippedUnknown": self.posts_skipped_unknown,
                "failedExtractions": self.failed_extractions,
                "errors": self.errors,
                "progress": self.progress,
                "successRate": success_rate,
                "health": health,
                "scrapeHealth": health,
                "outputFile": self.output_file,
                "config": self.config_summary,
                "cancelRequested": self.cancel_requested,
                "goRequested": self.go_requested,
                "browserSessionCreated": self.browser_session_created,
                "profileReady": self.profile_ready,
                "pageReady": self.profile_ready,
                "loginRequired": self.login_required,
                "verificationRequired": self.verification_required,
                "readyToScrape": self.ready_to_scrape,
                "browserUrl": self.browser_url,
                "browserOpen": self.browser_session_created,
                "canGo": (
                    self.status == "ready"
                    and self.browser_session_created
                    and self.profile_ready
                    and not self.verification_required
                    and self.ready_to_scrape
                    and not self.go_requested
                ),
                "canDownload": self.status == "completed" and bool(self.output_file) and Path(self.output_file).exists(),
                "downloadReady": self.status == "completed" and bool(self.output_file) and Path(self.output_file).exists(),
            }
            if include_logs:
                snapshot["logs"] = list(self.logs)
            snapshot.update(browser_mode_payload())
            return snapshot


JOB = ScrapeJobState()
JOB_THREAD: Optional[threading.Thread] = None
DASHBOARD_HUB = DashboardHub()
PREVIEW = LivePreviewState()
CONTROL_BUS = LiveCommandBus()
LOGIN_READY_TIMEOUT = 180000
HEADLESS_PROFILE_READY_TIMEOUT = 15000
PREVIEW_INTERVAL_SECONDS = 0.8
PREVIEW_JPEG_QUALITY = 55

# PRODUCTION SYSTEM: Initialize core modules - MANDATORY
try:
    PRODUCTION_LOGGER = ProductionLogger(persistence_dir=Path("."))
    SESSION_MANAGER = PlaywrightSessionManager(sessions_dir=Path("storage_states"))
    ETL_PIPELINE = ETLPipeline(output_dir=Path("."), platform="instagram")
    DATA_EXTRACTOR = DataExtractor()
    print("✅ PRODUCTION SYSTEM ACTIVE - No fallback mode")
    print("  ✓ ProductionLogger: logs to logs.db")
    print("  ✓ PlaywrightSessionManager: sessions in storage_states/")
    print("  ✓ DataExtractor: active post extraction")
    print("  ✓ ETLPipeline: active processing and export")
except Exception as e:
    print("❌ CRITICAL: Production system initialization failed")
    print(f"   Error: {e}")
    print("   System cannot continue without core modules")
    raise


def using_local_browser_window() -> bool:
    return scraper.uses_local_browser_window()


def preview_is_interactive() -> bool:
    return scraper.preview_input_supported()


def browser_mode_payload() -> dict[str, Any]:
    return {
        "browserMode": scraper.browser_runtime_mode(),
        "browserModeLabel": scraper.browser_mode_label(),
        "browserModeNote": scraper.browser_mode_note(),
        "previewInteractive": preview_is_interactive(),
        "localBrowserWindow": using_local_browser_window(),
    }


def login_wait_message() -> str:
    if using_local_browser_window():
        return "A browser window has been opened. Please log in to Instagram there to continue. Once login is detected, click GO / START EXTRACTION in the dashboard."

    return (
        "Instagram login is required, but this environment cannot open a local interactive browser window. "
        "Run the app locally with PLAYWRIGHT_INTERACTIVE_BROWSER=true, or provide storage state / backend-only credentials."
    )


def current_page_url(page, fallback: str = "") -> str:
    try:
        return page.url or fallback
    except Exception:
        return fallback


def sync_browser_url(page, fallback: str = "") -> None:
    JOB.update(browser_url=current_page_url(page, fallback))


def local_login_still_required(page) -> bool:
    """Detect logged-out profile states when running with a real local browser window."""
    if not using_local_browser_window():
        return False

    selectors = [
        "a[href*='/accounts/login']",
        "a:has-text('Log in')",
        "a:has-text('Log In')",
    ]
    for selector in selectors:
        try:
            if page.locator(selector).first.count() > 0:
                return True
        except Exception:
            continue

    return False


def mark_browser_ready(page, profile_url: str, *, waiting_for_go: bool) -> None:
    browser_url = current_page_url(page, profile_url)
    JOB.update(
        status="ready" if waiting_for_go else "running",
        active_task="Ready for extraction" if waiting_for_go else "Profile ready",
        browser_session_created=True,
        profile_ready=True,
        login_required=False,
        verification_required=False,
        ready_to_scrape=waiting_for_go,
        browser_url=browser_url,
        current_post=browser_url,
    )


def open_login_form_in_same_tab(page) -> None:
    login_url = "https://www.instagram.com/accounts/login/"
    if "accounts/login" in current_page_url(page):
        return
    page.goto(login_url, wait_until="domcontentloaded", timeout=scraper.POST_GOTO_TIMEOUT)


def broadcast_dashboard_event(event_type: str, data: dict[str, Any]) -> None:
    DASHBOARD_HUB.broadcast(event_type, data)


def broadcast_job_snapshot(include_logs: bool = False) -> None:
    broadcast_dashboard_event("snapshot", JOB.snapshot(include_logs=include_logs))


def broadcast_preview_snapshot() -> None:
    return


def empty_stats() -> list[dict[str, str]]:
    return [
        {"label": "Posts Found", "value": "0"},
        {"label": "Progress", "value": "0%"},
        {"label": "Success Rate", "value": "0%"},
        {"label": "Errors", "value": "0"},
    ]


def dashboard_features() -> list[dict[str, str]]:
    return [
        {
            "title": "Validated Inputs",
            "description": "Profile URL, scroll rounds, date coverage, and Excel filename are checked before a scrape can start.",
            "icon": "URL",
        },
        {
            "title": "Accurate Metrics",
            "description": "The backend uses platform-scoped extraction for visible likes, comments, shares, and related counts.",
            "icon": "MET",
        },
        {
            "title": "Live Activity Logs",
            "description": "The dashboard streams structured logs and status updates in real time while the real browser runs locally.",
            "icon": "LOG",
        },
        {
            "title": "Excel Export",
            "description": "Results are saved to the confirmed .xlsx filename with stable row pairing per post.",
            "icon": "XLS",
        },
    ]


def platform_switcher(active_key: str) -> list[dict[str, Any]]:
    return [
        {
            "key": "instagram",
            "label": "Instagram",
            "shortLabel": "IG",
            "meta": "Posts and reels",
            "href": "/instagram",
            "active": active_key == "instagram",
            "placeholder": False,
        },
        {
            "key": "facebook",
            "label": "Facebook",
            "shortLabel": "FB",
            "meta": "Pages and posts",
            "href": "/facebook",
            "active": active_key == "facebook",
            "placeholder": False,
        },
        {
            "key": "tiktok",
            "label": "TikTok",
            "shortLabel": "TT",
            "meta": "Coming soon",
            "href": "/tiktok",
            "active": active_key == "tiktok",
            "placeholder": True,
        },
    ]


def build_platform_config(platform_key: str) -> dict[str, Any]:
    platforms = platform_switcher(platform_key)

    if platform_key == "instagram":
        return {
            "platformKey": "instagram",
            "platformName": "Instagram",
            "workspaceSubtitle": "Instagram extraction workspace",
            "heroTitle": "Instagram Extraction Workspace",
            "heroText": "Enter the profile, date coverage, scroll depth, and Excel filename. Review the setup, open the browser session if login is required, then extract visible Instagram post data into Excel.",
            "linkLabel": "Instagram profile link",
            "linkPlaceholder": "https://www.instagram.com/username/",
            "linkPayloadKey": "instagramLink",
            "roundsLabel": "Scroll rounds",
            "progressCardLabel": "Profile Scrolled",
            "currentItemLabel": "Current Post",
            "linksFoundLabel": "Posts Found",
            "collectionTypeEnabled": False,
            "collectionTypeLabel": "Collection type",
            "collectionTypeOptions": [],
            "latestModeLabel": "Collect from start date up to latest post",
            "defaultLatestMode": False,
            "defaultOutputFile": "instagram_extract.xlsx",
            "activityLogsTitle": "Instagram Activity Logs",
            "reviewTitle": "Review Instagram extraction setup",
            "browserSessionTitle": "Manual Login & GO Signal",
            "browserSessionDescription": "A real Chromium window opens locally for Instagram login when needed. Reuse the saved session when available, monitor readiness here, then click GO to start extraction.",
            "depthTagLabel": "Max Scroll",
            "apiBase": "/api",
            "wsPath": "/ws/dashboard",
            "placeholder": False,
            "platforms": platforms,
        }

    if platform_key == "facebook":
        return {
            "platformKey": "facebook",
            "platformName": "Facebook",
            "workspaceSubtitle": "Facebook extraction workspace",
            "heroTitle": "Facebook Extraction Workspace",
            "heroText": "Enter a public Facebook page, profile, or post link, choose how deeply to load content, review the setup, then open the browser session and extract visible public data into Excel.",
            "linkLabel": "Facebook link",
            "linkPlaceholder": "https://www.facebook.com/...",
            "linkPayloadKey": "facebookLink",
            "roundsLabel": "Load rounds",
            "progressCardLabel": "Load Progress",
            "currentItemLabel": "Current Item",
            "linksFoundLabel": "Links Found",
            "collectionTypeEnabled": True,
            "collectionTypeLabel": "Collection type",
            "collectionTypeOptions": [
                {"value": "posts_only", "label": "Posts only"},
                {"value": "posts_with_comments", "label": "Posts with visible comments"},
            ],
            "latestModeLabel": "Collect from start date up to latest visible content",
            "defaultLatestMode": True,
            "defaultOutputFile": "facebook_extract.xlsx",
            "activityLogsTitle": "Facebook Activity Logs",
            "reviewTitle": "Review Facebook extraction setup",
            "browserSessionTitle": "Manual Login & GO Signal",
            "browserSessionDescription": "A real Chromium window opens locally for Facebook login when needed. Reuse the saved session when possible, complete any checkpoint manually, then click GO when the target page is ready.",
            "depthTagLabel": "Depth",
            "apiBase": "/facebook/api",
            "wsPath": "/facebook/ws/dashboard",
            "placeholder": False,
            "platforms": platforms,
        }

    return {
        "platformKey": "tiktok",
        "platformName": "TikTok",
        "workspaceSubtitle": "TikTok extraction workspace",
        "heroTitle": "TikTok Extraction Workspace",
        "heroText": "TikTok support is being prepared. The shared dashboard is ready, but the backend extraction flow is not wired yet.",
        "linkLabel": "TikTok profile link",
        "linkPlaceholder": "https://www.tiktok.com/@username",
        "linkPayloadKey": "tiktokLink",
        "roundsLabel": "Load rounds",
        "progressCardLabel": "Load Progress",
        "currentItemLabel": "Current Item",
        "linksFoundLabel": "Videos Found",
        "collectionTypeEnabled": False,
        "collectionTypeLabel": "Collection type",
        "collectionTypeOptions": [],
        "latestModeLabel": "Collect from start date up to latest visible content",
        "defaultLatestMode": True,
        "defaultOutputFile": "tiktok_extract.xlsx",
        "activityLogsTitle": "TikTok Activity Logs",
        "reviewTitle": "Review TikTok extraction setup",
        "browserSessionTitle": "Platform Placeholder",
        "browserSessionDescription": "TikTok is shown here as the next platform slot. The shared shell is ready, but extraction controls are disabled until the backend is implemented.",
        "depthTagLabel": "Depth",
        "apiBase": "",
        "wsPath": "",
        "placeholder": True,
        "platforms": platforms,
    }


def parse_date(value: str, field_name: str) -> tuple[Optional[datetime], Optional[str]]:
    value = (value or "").strip()
    if not value:
        return None, f"{field_name} is required."

    try:
        return datetime.strptime(value, scraper.DATE_INPUT_FORMAT), None
    except ValueError:
        return None, f"{field_name} must use YYYY-MM-DD."


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off", ""}:
        return False

    return default


def validate_request_payload(payload: dict[str, Any]) -> tuple[Optional[WebScrapeConfig], list[str], bool]:
    errors: list[str] = []
    overwrite_required = False

    # Log validation start
    PRODUCTION_LOGGER.log(LogLevel.INFO, "Validation started", "User submitted Instagram scrape configuration")

    profile_url = scraper.normalize_instagram_profile_url(str(payload.get("instagramLink", "")))
    if profile_url is None:
        error_msg = "Enter a valid Instagram profile link, for example https://www.instagram.com/username/."
        errors.append(error_msg)
        PRODUCTION_LOGGER.log(LogLevel.WARN, "Validation failed: Invalid profile URL", str(payload.get("instagramLink", "")))

    raw_scroll_rounds = str(payload.get("scrollRounds", "")).strip()
    if not raw_scroll_rounds:
        errors.append("Scroll rounds is required.")
        scroll_rounds = 0
    elif not raw_scroll_rounds.isdigit() or int(raw_scroll_rounds) <= 0:
        errors.append("Scroll rounds must be a positive number.")
        scroll_rounds = 0
    else:
        scroll_rounds = int(raw_scroll_rounds)

    start_date, start_error = parse_date(str(payload.get("startDate", "")), "Start date")
    if start_error:
        errors.append(start_error)

    latest_mode = parse_bool(payload.get("latestMode", False), default=False)
    end_date: Optional[datetime] = None
    if not latest_mode:
        end_date, end_error = parse_date(str(payload.get("endDate", "")), "End date")
        if end_error:
            errors.append(end_error)

    if start_date is not None and end_date is not None and end_date < start_date:
        errors.append("End date cannot be earlier than the start date.")

    output_file = scraper.normalize_excel_filename(str(payload.get("outputFile", "")))
    if output_file is None:
        errors.append('Enter a valid Excel filename. Do not use < > : " / \\ | ? * and use .xlsx only.')
        output_file = ""

    overwrite = bool(payload.get("overwrite", False))
    if output_file and Path(output_file).exists() and not overwrite:
        overwrite_required = True
        errors.append(f"{output_file} already exists. Confirm overwrite or enter a new filename.")

    if errors:
        # Log validation errors
        PRODUCTION_LOGGER.log(LogLevel.ERROR, "Validation failed", f"{len(errors)} error(s): {'; '.join(errors[:3])}")
        return None, errors, overwrite_required

    # Log successful validation
    PRODUCTION_LOGGER.log(LogLevel.SUCCESS, "Validation passed", f"Profile: {profile_url}, Rounds: {scroll_rounds}, Output: {output_file}")

    return (
        WebScrapeConfig(
            profile_url=profile_url or "",
            scroll_rounds=scroll_rounds,
            start_date=start_date or datetime.now(),
            end_date=end_date,
            output_file=output_file,
            overwrite=overwrite,
        ),
        [],
        False,
    )


def config_to_summary(config: WebScrapeConfig) -> dict[str, str]:
    return {
        "instagramLink": config.profile_url,
        "scrollRounds": str(config.scroll_rounds),
        "startDate": config.start_date.strftime(scraper.DATE_INPUT_FORMAT),
        "endDate": config.end_date.strftime(scraper.DATE_INPUT_FORMAT) if config.end_date else "",
        "latestMode": "true" if config.end_date is None else "false",
        "dateCoverage": scraper.format_date_coverage(config.start_date, config.end_date),
        "outputFile": config.output_file,
    }


def emit_preview_frame(page, note: str, force: bool = False) -> None:
    return


def normalize_preview_key(key: str) -> Optional[str]:
    if key == " ":
        return "Space"
    allowed = {
        "Enter",
        "Tab",
        "Backspace",
        "Escape",
        "Delete",
        "PageDown",
        "PageUp",
        "Home",
        "End",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
    }
    return key if key in allowed else None


def execute_control_command(page, command: dict[str, Any]) -> None:
    action = str(command.get("action", "")).strip()
    if not action:
        return

    if action == "preview_click":
        if not preview_is_interactive():
            emit_preview_frame(page, "Preview is view only. Use the opened browser window to log in.", force=True)
            return
        x = int(command.get("x", 0))
        y = int(command.get("y", 0))
        page.mouse.click(x, y)
        emit_preview_frame(page, f"Preview click ({x}, {y})", force=True)
        return

    if action == "preview_key":
        if not preview_is_interactive():
            emit_preview_frame(page, "Preview is view only. Keyboard input must go to the opened browser window.", force=True)
            return
        text = str(command.get("text", ""))
        key = str(command.get("key", ""))
        normalized_key = normalize_preview_key(key)
        if text and len(text) == 1 and not normalized_key:
            page.keyboard.insert_text(text)
        elif normalized_key:
            page.keyboard.press(normalized_key)
        emit_preview_frame(page, f"Preview key: {key or text}", force=True)
        return

    if action == "scroll_up":
        page.mouse.wheel(0, -1600)
        emit_preview_frame(page, "Manual scroll up", force=True)
        return

    if action == "scroll_down":
        page.mouse.wheel(0, 1600)
        emit_preview_frame(page, "Manual scroll down", force=True)
        return

    if action == "preview_scroll":
        if not preview_is_interactive():
            emit_preview_frame(page, "Preview is view only. Use scroll controls or the opened browser window.", force=True)
            return
        delta_y = int(command.get("deltaY", 0))
        if delta_y:
            page.mouse.wheel(0, delta_y)
            emit_preview_frame(page, f"Preview wheel scroll ({delta_y})", force=True)
        return

    if action in {"focus_browser", "open_browser"}:
        try:
            page.bring_to_front()
            emit_preview_frame(page, "Browser window focused", force=True)
        except Exception:
            emit_preview_frame(page, "Browser window focus requested", force=True)
        return

    if action == "force_next_scroll":
        page.mouse.wheel(0, 2200)
        emit_preview_frame(page, "Forced scroll", force=True)
        return

    if action == "capture_screenshot":
        emit_preview_frame(page, "Manual screenshot capture", force=True)


def drain_control_commands(page) -> None:
    for command in CONTROL_BUS.drain():
        try:
            execute_control_command(page, command)
        except Exception as exc:
            JOB.add_log("WARN", "Control command failed", f"{command.get('action', 'unknown')} ({type(exc).__name__})")


def wait_if_paused(page, active_task: str) -> None:
    if not CONTROL_BUS.is_paused():
        return

    JOB.update(status="paused", active_task=f"Paused: {active_task}")
    JOB.add_log("WARN", "Paused", f"Paused during {active_task.lower()}.")
    while CONTROL_BUS.is_paused():
        if JOB.should_cancel():
            raise ScrapeCancelled("Cancelled while paused.")
        drain_control_commands(page)
        emit_preview_frame(page, f"Paused: {active_task}")
        time.sleep(0.25)

    JOB.update(status="running", active_task=active_task)
    JOB.add_log("INFO", "Resumed", f"Resumed {active_task.lower()}.")
    emit_preview_frame(page, f"Resumed: {active_task}", force=True)


def pump_live_runtime(page, active_task: str, note: str, force_preview: bool = False) -> None:
    wait_if_paused(page, active_task)
    drain_control_commands(page)
    emit_preview_frame(page, note, force=force_preview)


def wait_for_user_login(page, context, profile_url: str, *, waiting_for_go: bool) -> None:
    message = login_wait_message()
    if using_local_browser_window():
        try:
            page.bring_to_front()
        except Exception:
            pass
    JOB.update(
        status="waiting_login",
        active_task="Waiting for Instagram login",
        browser_session_created=True,
        profile_ready=False,
        login_required=True,
        verification_required=False,
        ready_to_scrape=False,
        browser_url=current_page_url(page, profile_url),
        current_post=current_page_url(page, profile_url),
    )
    JOB.add_log("WARN", "Login required", "Instagram login is required before scrolling or extraction can continue.")
    JOB.add_log("INFO", "Reusing existing browser session", "Waiting in the same Playwright browser tab. No duplicate browser or page will be opened.")
    JOB.add_log("WARN", "Waiting for manual login", message)
    broadcast_dashboard_event("login_required", {"message": message, "url": current_page_url(page, profile_url)})
    emit_preview_frame(page, "Waiting for user login", force=True)

    if not using_local_browser_window():
        raise RuntimeError(message)

    deadline = time.monotonic() + (LOGIN_READY_TIMEOUT / 1000)
    login_form_logged = False
    login_page_opened = False
    returned_to_profile_after_login = False
    logged_out_hint_logged = False
    verification_logged = False
    login_submit_logged = False
    login_loop_logged = False
    save_login_prompt_logged = False
    last_verification_ping = 0.0
    while time.monotonic() < deadline:
        if JOB.should_cancel():
            raise ScrapeCancelled("Cancelled while waiting for user login.")

        wait_if_paused(page, "Waiting for user login")
        drain_control_commands(page)
        sync_browser_url(page, profile_url)
        emit_preview_frame(page, "Waiting for user login")

        if scraper.dismiss_instagram_save_login_prompt(page):
            if not save_login_prompt_logged:
                JOB.add_log("INFO", "Dismissed save-login prompt", "Automatically clicked Not now to keep login flow moving.")
                save_login_prompt_logged = True
            time.sleep(0.2)
            continue

        verification_required, verification_reason = scraper.detect_checkpoint_or_verification(page)
        if verification_required:
            JOB.update(
                status="captcha",
                active_task="Instagram verification required",
                browser_session_created=True,
                profile_ready=False,
                login_required=True,
                verification_required=True,
                ready_to_scrape=False,
                browser_url=current_page_url(page, profile_url),
                current_post=current_page_url(page, profile_url),
            )
            if not verification_logged:
                JOB.add_log("WARN", "Verification required", verification_reason)
                JOB.add_log("WARN", "Waiting for user to complete verification", "Please complete Instagram verification manually in the opened browser.")
                broadcast_dashboard_event("verification_required", {"message": "Instagram verification required. Please complete it manually in the opened browser.", "url": current_page_url(page, profile_url)})
                verification_logged = True
                last_verification_ping = time.monotonic()
            elif time.monotonic() - last_verification_ping >= 10:
                JOB.add_log("INFO", "Still waiting for verification", "Please complete Instagram verification in the opened browser.")
                last_verification_ping = time.monotonic()
            time.sleep(2.0)
            continue

        profile_ready = scraper.profile_ready_for_collection(page)
        still_logged_out = local_login_still_required(page)

        if profile_ready and not still_logged_out:
            cookies_present = scraper.has_authenticated_session(context)
            if (verification_logged or login_submit_logged) and not cookies_present:
                JOB.add_log("WARN", "Waiting for verified session", "The profile grid appeared, but Instagram session cookies are still missing. Waiting for login or verification to finish.")
                time.sleep(0.35)
                continue
            if cookies_present:
                JOB.add_log("INFO", "Checking session cookies", "Instagram session cookies are present in the current browser context.")
                scraper.save_storage_state(context, JOB.add_log)
            mark_browser_ready(page, profile_url, waiting_for_go=waiting_for_go)
            JOB.add_log(
                "SUCCESS",
                "Login completed",
                "Profile grid is visible in the existing browser session."
                if not waiting_for_go
                else "Profile grid is visible. Login completed and the session is ready for GO.",
            )
            if verification_logged:
                JOB.add_log("SUCCESS", "Verification completed", "Instagram verification finished and the requested profile is accessible.")
            JOB.add_log("SUCCESS", "Profile grid detected", "Profile grid is visible after login.")
            if cookies_present:
                JOB.add_log("SUCCESS", "Saved session valid", "Instagram session cookies are active and storage state is ready for reuse.")
            else:
                JOB.add_log("INFO", "Public content ready", "Instagram profile appears accessible without a saved authenticated session.")
            if waiting_for_go:
                JOB.add_log("INFO", "Ready for extraction", "Login completed. Ready to start extraction once GO is pressed.")
            broadcast_dashboard_event("login_completed", {"message": "Login completed. Profile grid detected.", "url": current_page_url(page, profile_url)})
            emit_preview_frame(page, "Login complete", force=True)
            return

        if profile_ready and still_logged_out:
            JOB.update(login_required=True, profile_ready=False, ready_to_scrape=False, browser_url=current_page_url(page, profile_url))
            if not logged_out_hint_logged:
                JOB.add_log("WARN", "Still logged out", "Profile is visible but the Instagram Log in link is still present.")
                logged_out_hint_logged = True
            if not login_page_opened:
                try:
                    JOB.add_log("INFO", "Opening Instagram login form", "Navigating the current browser tab to Instagram's login page for manual sign-in.")
                    open_login_form_in_same_tab(page)
                    sync_browser_url(page, profile_url)
                    login_page_opened = True
                except Exception as exc:
                    JOB.add_log("WARN", "Login form navigation failed", f"{type(exc).__name__}")
            time.sleep(0.3)
            continue

        login_required, login_reason = scraper.detect_login_gate(page)
        login_form_visible = scraper.wait_for_selector(page, scraper.LOGIN_FORM_SELECTOR, 200)

        if login_required:
            JOB.update(login_required=True, verification_required=False, profile_ready=False, ready_to_scrape=False, browser_url=current_page_url(page, profile_url))
            returned_to_profile_after_login = False
            if login_form_visible:
                if not login_form_logged:
                    JOB.add_log("INFO", "Login form detected", "Instagram login form is visible in the current browser tab.")
                    login_form_logged = True
                if not login_submit_logged and login_page_opened:
                    JOB.add_log("INFO", "Waiting for manual login", "Enter your Instagram credentials in the opened browser window.")
                if login_submit_logged and not login_loop_logged:
                    JOB.add_log("WARN", "Login loop detected", "The Instagram login form reappeared after submission. The session is not established yet or verification is still required.")
                    login_loop_logged = True
                login_page_opened = True
            elif not login_page_opened:
                try:
                    JOB.add_log("INFO", "Opening Instagram login form", "Navigating the current browser tab to Instagram's login page for manual sign-in.")
                    open_login_form_in_same_tab(page)
                    sync_browser_url(page, profile_url)
                    login_page_opened = True
                except Exception as exc:
                    JOB.add_log("WARN", "Login form navigation failed", f"{type(exc).__name__}")
            time.sleep(0.3)
            continue

        current_url = current_page_url(page, profile_url)
        if login_page_opened and login_form_logged and not login_form_visible and not login_submit_logged:
            JOB.add_log("INFO", "Login submitted", "The Instagram login form disappeared. Waiting for navigation, session cookies, and profile readiness.")
            login_submit_logged = True
        if (
            not returned_to_profile_after_login
            and "instagram.com" in current_url
            and "accounts/login" not in current_url
            and "/challenge/" not in current_url
            and "two_factor" not in current_url
            and "onetap" not in current_url
            and scraper.has_authenticated_session(context)
            and current_url.rstrip("/") != profile_url.rstrip("/")
        ):
            try:
                JOB.add_log("INFO", "Login session detected", "Login gate cleared. Returning to the target profile in the existing browser tab.")
                page.goto(profile_url, wait_until="domcontentloaded", timeout=scraper.POST_GOTO_TIMEOUT)
                sync_browser_url(page, profile_url)
                returned_to_profile_after_login = True
                login_form_logged = False
                continue
            except Exception as exc:
                JOB.add_log("WARN", "Profile reload after login failed", f"{type(exc).__name__}")

        time.sleep(0.3)

    raise TimeoutError("Instagram login was required, but the session was not completed before timeout.")


def wait_for_go_signal(page) -> None:
    mark_browser_ready(page, current_page_url(page), waiting_for_go=True)
    JOB.add_log(
        "INFO",
        "Waiting for GO signal",
        "Login detected and profile grid is ready. Click GO / START EXTRACTION to begin scrolling and data collection.",
    )

    while not JOB.should_go():
        if JOB.should_cancel():
            raise ScrapeCancelled("Cancelled while waiting for GO signal.")

        drain_control_commands(page)
        sync_browser_url(page, current_page_url(page))
        time.sleep(0.2)

    JOB.update(status="running", active_task="Starting scroll collection", ready_to_scrape=False, login_required=False)
    JOB.add_log("SUCCESS", "GO signal received", "Starting scroll collection and extraction.")


def ensure_login_ready(page, context, profile_url: str, active_task: str, delay_reason: str = "", log_check: bool = False) -> bool:
    if log_check:
        JOB.add_log("INFO", "Checking login state", f"Checking login state before {active_task.lower()}.")
    login_required, reason = scraper.detect_login_gate(page)
    if not login_required:
        return False

    JOB.add_log("WARN", "Login required", reason or "Instagram login is required.")
    if delay_reason:
        JOB.add_log("WARN", "Link collection delayed because login is required", delay_reason)
    wait_for_user_login(page, context, profile_url, waiting_for_go=False)
    JOB.add_log("INFO", "Resuming scroll after login", "Profile grid detected. Resuming automated collection.")
    emit_preview_frame(page, "Resuming after login", force=True)
    return True


def collect_post_links_with_progress(page, config: WebScrapeConfig) -> list[str]:
    JOB.update(active_task="Collecting post links", total_scroll_rounds=config.scroll_rounds)
    JOB.add_log("INFO", "Starting scroll collection", "Profile grid detected. Beginning scroll collection.")

    def progress_hook(scroll_round: int, total_rounds: int, posts_found: int) -> None:
        progress_value = 0 if scroll_round <= 0 else min(20, round(20 * scroll_round / max(total_rounds, 1)))
        JOB.update(
            current_scroll_round=scroll_round,
            total_scroll_rounds=total_rounds,
            posts_found=posts_found,
            progress=progress_value,
        )
        broadcast_dashboard_event(
            "scroll_update",
            {"round": scroll_round, "totalRounds": total_rounds, "postsFound": posts_found},
        )

    def live_hook(runtime_page, phase: str, payload: dict[str, Any]) -> None:
        if ensure_login_ready(
            runtime_page,
            runtime_page.context,
            config.profile_url,
            "Collecting post links",
            "Waiting for login before scroll collection can continue.",
        ):
            JOB.add_log("INFO", "Post links loaded after login", f"Continuing scroll collection after login; current visible links: {payload.get('totalLinks', 0)}.")
        round_text = payload.get("round")
        note = {
            "initial-grid": f"Initial grid loaded: {payload.get('totalLinks', 0)} links",
            "scroll-round": (
                f"Scroll Round {round_text}/{config.scroll_rounds}: "
                f"+{payload.get('newLinks', 0)} new links, total {payload.get('totalLinks', 0)}"
            ),
            "scroll-stop": f"Scroll stop: {payload.get('reason', 'Complete')}",
        }.get(phase, f"Collecting post links ({phase})")
        pump_live_runtime(runtime_page, "Collecting post links", note, force_preview=phase != "scroll-round")

    diagnostics: dict[str, Any] = {}
    probe_page = None
    if not using_local_browser_window():
        probe_page = page.context.new_page()
        probe_page.route("**/*", scraper.route_nonessential_resources)

    try:
        links = scraper.collect_post_links(
            page,
            max_posts=None,
            scroll_rounds=config.scroll_rounds,
            target_start_date=config.start_date,
            probe_page=probe_page,
            log_hook=JOB.add_log,
            progress_hook=progress_hook,
            cancel_check=JOB.should_cancel,
            diagnostics=diagnostics,
            live_hook=live_hook,
        )
        oldest = diagnostics.get("oldestVisibleDate")
        newest = diagnostics.get("newestVisibleDate")
        if newest is not None or oldest is not None:
            JOB.add_log(
                "INFO",
                "Visible date span after scrolling",
                (
                    f"Newest visible: {newest.strftime(scraper.DATE_INPUT_FORMAT) if newest else 'unknown'} | "
                    f"Oldest visible: {oldest.strftime(scraper.DATE_INPUT_FORMAT) if oldest else 'unknown'}"
                ),
            )
        stop_reason = diagnostics.get("stopReason")
        if stop_reason:
            JOB.add_log("INFO", "Scroll stop reason", str(stop_reason))
        return links
    except RuntimeError as exc:
        if "Cancelled during profile scrolling." in str(exc):
            raise ScrapeCancelled(str(exc)) from exc
        raise
    finally:
        try:
            probe_page.close()
        except Exception:
            pass


def wait_until_profile_ready_or_login_completed(page, context, profile_url: str) -> None:
    """Block until the target profile is ready for extraction, without scraping before login completes."""
    saved_session_path = scraper.get_storage_state_path(require_exists=True)
    JOB.update(
        status="loading_session",
        active_task="Loading saved session",
        browser_session_created=True,
        profile_ready=False,
        login_required=False,
        verification_required=False,
        ready_to_scrape=False,
        browser_url=profile_url,
        current_post=profile_url,
    )
    JOB.add_log("INFO", "Checking saved session", "Checking whether a Playwright storage_state file is available for Instagram.")
    if saved_session_path is not None:
        JOB.add_log("INFO", "Saved session found", str(saved_session_path))
    else:
        JOB.add_log("INFO", "Saved session missing", "No saved Instagram storage_state file was found. Manual login may be required.")
    JOB.add_log("INFO", "Opened target profile", profile_url)
    page.goto(profile_url, wait_until="domcontentloaded", timeout=scraper.POST_GOTO_TIMEOUT)
    sync_browser_url(page, profile_url)
    emit_preview_frame(page, "Profile opened", force=True)
    JOB.add_log("INFO", "Checking login state", "Checking whether Instagram requires login before scraping.")

    if JOB.should_cancel():
        raise ScrapeCancelled("Cancelled while waiting for Instagram login/profile.")

    session_state = scraper.validate_session(page, context, profile_url)
    if saved_session_path is not None:
        if session_state["state"] == "ready":
            JOB.add_log("SUCCESS", "Saved session valid", session_state["reason"])
        elif session_state["state"] == "verification_required":
            JOB.add_log("WARN", "Saved session expired", "Stored Instagram session requires verification before it can be reused.")
        elif session_state["state"] == "login_required":
            JOB.add_log("WARN", "Saved session expired", "Stored Instagram session no longer bypasses the login wall.")
        else:
            JOB.add_log("WARN", "Saved session expired", "Stored Instagram session could not be validated yet.")

    if session_state["state"] == "verification_required":
        JOB.add_log("WARN", "Verification required", session_state["reason"])
        wait_for_user_login(page, context, profile_url, waiting_for_go=True)
        return

    if session_state["state"] == "login_required":
        JOB.add_log("WARN", "Login required", session_state["reason"] or "Instagram login is required.")
        JOB.add_log("WARN", "Link collection delayed because login is required", "Waiting for user login before scrolling.")
        if scraper.auto_login_if_needed(page, context, profile_url, log_hook=JOB.add_log):
            mark_browser_ready(page, profile_url, waiting_for_go=True)
            JOB.add_log("SUCCESS", "Login completed", "Automatic login restored the session.")
            JOB.add_log("SUCCESS", "Profile grid detected", "Profile grid is visible after login.")
            JOB.add_log("INFO", "Ready for GO signal", "Click GO / START EXTRACTION to continue.")
            emit_preview_frame(page, "Auto-login complete", force=True)
            return
        wait_for_user_login(page, context, profile_url, waiting_for_go=True)
        return

    if session_state["state"] == "ready":
        if local_login_still_required(page):
            JOB.add_log("WARN", "Login required", "Profile is visible, but Instagram still shows a Log in prompt.")
            JOB.add_log("WARN", "Link collection delayed because login is required", "Waiting for user login before scrolling.")
            wait_for_user_login(page, context, profile_url, waiting_for_go=True)
            return
        mark_browser_ready(page, profile_url, waiting_for_go=True)
        JOB.add_log("SUCCESS", "Profile detected", "Post grid is visible; scraping can continue.")
        JOB.add_log("INFO", "Profile grid detected", "Profile grid is visible and ready for collection.")
        JOB.add_log("INFO", "Ready for GO signal", "Click GO / START EXTRACTION to continue.")
        emit_preview_frame(page, "Profile grid detected", force=True)
        return

    if scraper.auto_login_if_needed(page, context, profile_url, log_hook=JOB.add_log):
        mark_browser_ready(page, profile_url, waiting_for_go=True)
        JOB.add_log("SUCCESS", "Login completed", "Automatic login restored the Instagram session.")
        JOB.add_log("SUCCESS", "Profile grid detected", "Profile grid is visible after automatic login.")
        JOB.add_log("INFO", "Ready for GO signal", "Click GO / START EXTRACTION to continue.")
        emit_preview_frame(page, "Auto-login complete", force=True)
        return

    if scraper.wait_for_selector(page, scraper.LOGIN_FORM_SELECTOR, 1200):
        JOB.add_log("WARN", "Login required", "Instagram login form is visible.")
        JOB.add_log("WARN", "Link collection delayed because login is required", "Waiting for user login before scrolling.")
        wait_for_user_login(page, context, profile_url, waiting_for_go=True)
        return

    JOB.update(active_task="Waiting for Instagram profile", status="loading_session", browser_session_created=True, profile_ready=False, verification_required=False, ready_to_scrape=False)
    JOB.add_log("INFO", "Waiting for profile", "Waiting for Instagram profile grid to load before scrolling begins.")
    deadline = time.monotonic() + (HEADLESS_PROFILE_READY_TIMEOUT / 1000)
    while time.monotonic() < deadline:
        if JOB.should_cancel():
            raise ScrapeCancelled("Cancelled while waiting for Instagram login/profile.")

        pump_live_runtime(page, "Waiting for Instagram profile", "Waiting for Instagram profile")
        sync_browser_url(page, profile_url)
        session_state = scraper.validate_session(page, context, profile_url)
        if session_state["state"] == "ready":
            if local_login_still_required(page):
                JOB.add_log("WARN", "Login required", "Profile is visible, but Instagram still shows a Log in prompt.")
                JOB.add_log("WARN", "Link collection delayed because login is required", "Waiting for user login before scrolling.")
                wait_for_user_login(page, context, profile_url, waiting_for_go=True)
                return
            mark_browser_ready(page, profile_url, waiting_for_go=True)
            JOB.add_log("SUCCESS", "Profile detected", "Post grid is visible; scraping can continue.")
            JOB.add_log("INFO", "Profile grid detected", "Profile grid is visible and ready for collection.")
            JOB.add_log("INFO", "Ready for GO signal", "Profile grid is visible. Click GO / START EXTRACTION to continue.")
            emit_preview_frame(page, "Profile grid detected", force=True)
            return
        if session_state["state"] == "verification_required":
            JOB.add_log("WARN", "Verification required", session_state["reason"] or "Instagram verification is required.")
            wait_for_user_login(page, context, profile_url, waiting_for_go=True)
            return
        if session_state["state"] == "login_required":
            JOB.add_log("WARN", "Login required", session_state["reason"] or "Instagram login is required.")
            JOB.add_log("WARN", "Link collection delayed because login is required", "Waiting for user login before scrolling.")
            wait_for_user_login(page, context, profile_url, waiting_for_go=True)
            return
        time.sleep(0.25)

    mark_browser_ready(page, profile_url, waiting_for_go=True)
    JOB.add_log(
        "WARN",
        "Fallback ready mode",
        "Instagram readiness timed out. Continue manually and click GO / START EXTRACTION when the page is usable.",
    )
    emit_preview_frame(page, "Fallback ready mode", force=True)


def run_scrape_job(config: WebScrapeConfig) -> None:
    CONTROL_BUS.reset()
    JOB.update(
        status="preparing",
        active_task="Creating browser session",
        output_file=config.output_file,
        config_summary=config_to_summary(config),
        browser_session_created=False,
        profile_ready=False,
        login_required=False,
        verification_required=False,
        ready_to_scrape=False,
        browser_url=config.profile_url,
        current_post="",
        started_at=time.time(),
        finished_at=None,
    )
    JOB.add_log("INFO", "Job started", f"Output: {config.output_file}")
    JOB.add_log(
        "INFO",
        "Selected date coverage",
        (
            f"startDate={config.start_date.strftime(scraper.DATE_INPUT_FORMAT)}, "
            f"endDate={config.end_date.strftime(scraper.DATE_INPUT_FORMAT) if config.end_date else 'latest'}, "
            f"latestMode={'true' if config.end_date is None else 'false'}"
        ),
    )

    browser = None
    context = None
    page = None
    try:
        with sync_playwright() as p:
            # PRODUCTION: Use PlaywrightSessionManager ONLY - strict, no fallback
            try:
                JOB.add_log("INFO", "Browser session initializing", "Using PlaywrightSessionManager")
                browser, context = SESSION_MANAGER.init_browser(p, platform="instagram")
            except Exception as sm_exc:
                JOB.add_log("ERROR", "SessionManager FAILED - SYSTEM STOPPING", str(sm_exc))
                raise  # FAIL FAST - no fallback allowed
            
            context.route("**/*", scraper.route_nonessential_resources)

            page = context.new_page()
            JOB.update(browser_session_created=True, browser_url=current_page_url(page, config.profile_url))
            JOB.add_log("INFO", "Browser session created", "Playwright browser/context/page ready")
            saved_session_path = scraper.get_storage_state_path(require_exists=True)
            JOB.add_log("INFO", "Checking saved session", "Looking for storage_state file")
            if saved_session_path is not None:
                JOB.add_log("INFO", "Saved session found", str(saved_session_path))
            else:
                JOB.add_log("INFO", "Saved session missing", "No saved Instagram storage_state file is available yet.")
            if using_local_browser_window():
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                JOB.add_log(
                    "INFO",
                    "Browser opened",
                    "A real Chromium browser window has been opened. Please log in there if Instagram asks, then return to the dashboard and click GO / START EXTRACTION.",
                )
            else:
                JOB.add_log(
                    "INFO",
                    "Browser opened",
                    "Headless Playwright session started. This environment cannot open a local interactive browser window.",
                )
            emit_preview_frame(page, "Browser started", force=True)
            wait_until_profile_ready_or_login_completed(page, context, config.profile_url)
            wait_for_go_signal(page)

            link_collection_started = time.perf_counter()
            links = collect_post_links_with_progress(page, config)
            link_collection_elapsed = time.perf_counter() - link_collection_started
            JOB.update(posts_found=len(links), active_task="Extracting post data")
            JOB.add_log(
                "SUCCESS",
                "Link collection complete",
                f"Found {len(links)} unique post links in {link_collection_elapsed:.2f}s.",
            )
            emit_preview_frame(page, f"Link collection complete: {len(links)} links", force=True)

            all_posts = []
            total_links = len(links)
            oldest_post_seen: Optional[datetime] = None
            newest_post_seen: Optional[datetime] = None
            target_date_reached = False
            seen_in_range_post = False
            for index, link in enumerate(links, start=1):
                if JOB.should_cancel():
                    raise ScrapeCancelled("Cancelled during post extraction.")

                pump_live_runtime(page, "Extracting post data", f"Preparing post {index}/{total_links}")
                JOB.update(
                    active_task="Extracting post data",
                    current_post=link,
                    posts_checked=index - 1,
                    posts_processed=index - 1,
                    progress=20 + round(70 * (index - 1) / max(total_links, 1)),
                )
                JOB.add_log("INFO", f"Processing {index}/{total_links}", link)
                needs_cooldown = False

                try:
                    post_started = time.perf_counter()
                    raw_date, date_obj, post_type = scraper.open_post_for_extraction(page, link)
                    detected_date_text = date_obj.strftime(scraper.DATE_INPUT_FORMAT) if date_obj else (raw_date or "Cannot detect")
                    JOB.add_log("INFO", "Post date detected", f"{link} -> {detected_date_text}")
                    emit_preview_frame(page, f"Post date detected: {detected_date_text}", force=True)

                    if date_obj is not None:
                        if oldest_post_seen is None or date_obj < oldest_post_seen:
                            oldest_post_seen = date_obj
                            JOB.add_log("INFO", "Oldest post so far", date_obj.strftime(scraper.DATE_INPUT_FORMAT))
                        if newest_post_seen is None or date_obj > newest_post_seen:
                            newest_post_seen = date_obj
                            JOB.add_log("INFO", "Newest post so far", date_obj.strftime(scraper.DATE_INPUT_FORMAT))

                    coverage_status, coverage_reason = scraper.classify_post_date_coverage(
                        date_obj,
                        config.start_date,
                        config.end_date,
                    )

                    if coverage_status == "newer_than_end":
                        snapshot = JOB.snapshot()
                        JOB.update(posts_skipped_newer=snapshot["postsSkippedNewer"] + 1)
                        JOB.add_log("INFO", "Post skipped", coverage_reason)
                        emit_preview_frame(page, f"Skipped newer post {index}/{total_links}", force=True)
                        needs_cooldown = True
                        JOB.update(posts_checked=index, posts_processed=index, progress=20 + round(70 * index / max(total_links, 1)))
                        time.sleep(scraper.BASE_POST_DELAY)
                        continue

                    if coverage_status == "older_than_start":
                        snapshot = JOB.snapshot()
                        JOB.update(posts_skipped_older=snapshot["postsSkippedOlder"] + 1)
                        JOB.add_log("INFO", "Post skipped", coverage_reason)
                        emit_preview_frame(page, f"Reached posts older than start date at {index}/{total_links}", force=True)
                        if seen_in_range_post:
                            JOB.add_log(
                                "INFO",
                                "Reached posts older than start date",
                                "Stopping post processing because collected links are ordered newest to oldest.",
                            )
                            JOB.update(posts_checked=index, posts_processed=index, progress=90)
                            break
                        needs_cooldown = True
                        JOB.update(posts_checked=index, posts_processed=index, progress=20 + round(70 * index / max(total_links, 1)))
                        time.sleep(scraper.BASE_POST_DELAY)
                        continue

                    if coverage_status == "unknown_date":
                        snapshot = JOB.snapshot()
                        JOB.update(posts_skipped_unknown=snapshot["postsSkippedUnknown"] + 1)
                        JOB.add_log("WARN", "Post skipped", coverage_reason)
                        emit_preview_frame(page, f"Skipped undated post {index}/{total_links}", force=True)
                        needs_cooldown = True
                        JOB.update(posts_checked=index, posts_processed=index, progress=20 + round(70 * index / max(total_links, 1)))
                        time.sleep(scraper.BASE_POST_DELAY)
                        continue

                    seen_in_range_post = True
                    snapshot = JOB.snapshot()
                    JOB.update(posts_in_range=snapshot["postsInRange"] + 1)
                    
                    # PRODUCTION: Use DataExtractor ONLY - strict, no fallback
                    try:
                        extracted = DATA_EXTRACTOR.extract(page, link, Platform.INSTAGRAM)
                        # Convert ExtractedPost to PostData for compatibility
                        from instagram_to_excel import PostData as IGPostData
                        post = IGPostData(
                            url=extracted.url,
                            post_type=post_type,
                            post_date_raw=raw_date,
                            post_date_obj=date_obj,
                            likes=extracted.likes,
                            comments=extracted.comments,
                            shares=extracted.shares,
                        )
                        JOB.add_log("INFO", "Metrics extracted", f"POST: {link[:80]}")
                    except Exception as ext_exc:
                        JOB.add_log("ERROR", "DataExtractor FAILED - SYSTEM STOPPING", str(ext_exc))
                        raise  # FAIL FAST - no fallback allowed
                    
                    post_elapsed = time.perf_counter() - post_started
                    all_posts.append(post)
                    
                    # PRODUCTION: Incrementally save to SQLite during collection - MANDATORY
                    try:
                        ETL_PIPELINE.add_post(post, url=link)
                    except Exception as etl_add_exc:
                        JOB.add_log("ERROR", "ETLPipeline.add_post FAILED", str(etl_add_exc))
                        raise  # FAIL FAST - incremental save is mandatory

                    if post.post_date_obj is not None and not target_date_reached and post.post_date_obj.date() <= config.start_date.date():
                        target_date_reached = True
                        JOB.add_log(
                            "SUCCESS",
                            "Reached target coverage",
                            f"Collected a post dated {scraper.format_post_date(post)}, which is on/before the target start date.",
                        )

                    success = post.likes is not None or post.comments is not None
                    if success:
                        snapshot = JOB.snapshot()
                        JOB.update(posts_success=snapshot["postsSuccess"] + 1)
                        JOB.add_log("INFO", "Post included", coverage_reason)
                        JOB.add_log(
                            "SUCCESS",
                            "Extracted post",
                            f"Likes: {post.likes}, Comments: {post.comments}, Shares: {post.shares}, Date: {scraper.format_post_date(post)}, Took: {post_elapsed:.2f}s",
                        )
                        emit_preview_frame(page, f"Extracted post {index}/{total_links}", force=True)
                    else:
                        snapshot = JOB.snapshot()
                        JOB.update(failed_extractions=snapshot["failedExtractions"] + 1)
                        JOB.update(errors=snapshot["errors"] + 1)
                        JOB.add_log("WARN", "Metrics incomplete", link)
                        emit_preview_frame(page, f"Metrics incomplete for {index}/{total_links}", force=True)
                        needs_cooldown = True
                    if post_elapsed >= scraper.SLOW_POST_SECONDS:
                        JOB.add_log("WARN", "Slow extraction", f"{link} took {post_elapsed:.2f}s.")
                except Exception as exc:
                    snapshot = JOB.snapshot()
                    JOB.update(failed_extractions=snapshot["failedExtractions"] + 1)
                    JOB.update(errors=snapshot["errors"] + 1)
                    JOB.add_log("WARN", "Post extraction failed", f"{link} ({type(exc).__name__})")
                    emit_preview_frame(page, f"Extraction failed for {index}/{total_links}", force=True)
                    needs_cooldown = True

                JOB.update(posts_checked=index, posts_processed=index, progress=20 + round(70 * index / max(total_links, 1)))
                if needs_cooldown:
                    time.sleep(scraper.BASE_POST_DELAY)

            if JOB.should_cancel():
                raise ScrapeCancelled("Cancelled before saving Excel output.")

            if oldest_post_seen is not None:
                JOB.add_log(
                    "INFO",
                    "Collection date span",
                    f"Newest collected post: {newest_post_seen.strftime(scraper.DATE_INPUT_FORMAT) if newest_post_seen else 'unknown'} | "
                    f"Oldest collected post: {oldest_post_seen.strftime(scraper.DATE_INPUT_FORMAT)}",
                )
            if not target_date_reached:
                oldest_text = oldest_post_seen.strftime(scraper.DATE_INPUT_FORMAT) if oldest_post_seen else "unknown"
                JOB.add_log(
                    "WARN",
                    "Target date not reached",
                    f"Oldest collected post was {oldest_text}. Target start date is {config.start_date.strftime(scraper.DATE_INPUT_FORMAT)}.",
                )

            filtered_posts = [
                post for post in all_posts if scraper.post_matches_date_coverage(post, config.start_date, config.end_date)
            ]
            removed_count = len(all_posts) - len(filtered_posts)
            if removed_count:
                JOB.add_log("INFO", "Date filter applied", f"Filtered out {removed_count} posts outside selected coverage.")

            JOB.update(active_task="Processing with ETL pipeline", progress=92)
            coverage_label = scraper.format_date_coverage(config.start_date, config.end_date)
            
            # PRODUCTION: Use ETLPipeline ONLY - strict, no fallback
            try:
                JOB.add_log("INFO", "ETL pipeline starting", "Processing posts...")
                result = ETL_PIPELINE.process(
                    posts=filtered_posts,
                    output_file=config.output_file,
                    coverage_label=coverage_label,
                    platform="instagram",
                )
                if not result["success"]:
                    raise Exception(f"ETL processing failed: {result.get('error', 'Unknown error')}")
                
                JOB.add_log("SUCCESS", "ETL pipeline completed", f"Processed: {result.get('posts_processed', 0)}, Duplicates: {result.get('duplicates_removed', 0)}")
            except Exception as etl_exc:
                JOB.add_log("ERROR", "ETL Pipeline FAILED - SYSTEM STOPPING", str(etl_exc))
                raise  # FAIL FAST - no fallback allowed
            
            JOB.add_log("SUCCESS", "Excel exported", config.output_file)
            emit_preview_frame(page, "Excel saved", force=True)

            # Store post URLs for comment collection
            with JOB.lock:
                JOB.collected_post_urls = [p.url for p in filtered_posts]

            # ------------------------------------------------------------------
            # Phase 9 — Prompt user for comment collection (wait up to 10 min)
            # ------------------------------------------------------------------
            JOB.update(
                status="awaiting_comments",
                active_task="Waiting for comment collection decision",
                awaiting_comments=True,
                comments_requested=False,
                skip_comments_requested=False,
            )
            JOB.add_log("INFO", "Asking user for comments", "Metrics saved. Decide whether to collect all comments.")
            broadcast_dashboard_event("comments_prompt", {
                "postCount": len(filtered_posts),
                "outputFile": config.output_file,
            })

            comment_deadline = time.monotonic() + 600   # 10-minute timeout
            while time.monotonic() < comment_deadline:
                if JOB.should_cancel():
                    raise ScrapeCancelled("Cancelled while waiting for comment collection decision.")
                if JOB.should_collect_comments() or JOB.should_skip_comments():
                    break
                drain_control_commands(page)
                time.sleep(0.3)

            # ------------------------------------------------------------------
            # Phase 10 — Execute or skip comment collection
            # ------------------------------------------------------------------
            if JOB.should_collect_comments():
                JOB.update(status="collecting_comments", active_task="Collecting comments from posts", progress=97)
                JOB.add_log("INFO", "Comment collection started", f"Collecting comments from {len(filtered_posts)} posts.")
                try:
                    collected_urls = JOB.collected_post_urls
                    comments = collect_all_comments_ig(
                        page,
                        collected_urls,
                        log_hook=JOB.add_log,
                        cancel_check=JOB.should_cancel,
                    )
                    JOB.add_log("INFO", "Classifying comment sentiments", f"{len(comments)} comments to classify.")
                    comments = classify_comments(comments)
                    add_comments_sheet(config.output_file, comments)
                    update_sentiment_counts(config.output_file, collected_urls, comments)
                    JOB.add_log("SUCCESS", "Comments sheet saved", f"{len(comments)} comments written to {config.output_file}")
                    broadcast_dashboard_event("comments_completed", {"commentCount": len(comments), "outputFile": config.output_file})
                except Exception as _cmt_exc:
                    JOB.add_log("WARN", "Comment collection error", str(_cmt_exc))
            else:
                JOB.add_log("INFO", "Comment collection skipped", "User chose to skip comment collection.")
                broadcast_dashboard_event("comments_skipped", {})

            broadcast_dashboard_event("job_completed", JOB.snapshot(include_logs=False))
            JOB.update(status="completed", active_task="Completed", progress=100, finished_at=time.time(), ready_to_scrape=False, login_required=False, verification_required=False, awaiting_comments=False)
    except ScrapeCancelled as exc:
        JOB.update(
            status="cancelled",
            active_task="Cancelled",
            finished_at=time.time(),
            ready_to_scrape=False,
            login_required=False,
            verification_required=False,
        )
        JOB.add_log("WARN", "Scrape cancelled", str(exc))
        if page is not None:
            emit_preview_frame(page, "Scrape cancelled", force=True)
    except Exception as exc:
        snapshot = JOB.snapshot()
        JOB.update(status="failed", active_task="Failed", errors=snapshot["errors"] + 1, finished_at=time.time(), ready_to_scrape=False, verification_required=False)
        JOB.add_log("WARN", "Scrape failed", f"{type(exc).__name__}: {exc}")
        if page is not None:
            emit_preview_frame(page, "Scrape failed", force=True)
    finally:
        # PRODUCTION: Use SessionManager to close and auto-save session - MANDATORY
        try:
            SESSION_MANAGER.close()
            JOB.add_log("INFO", "Session saved", "Browser session auto-saved for reuse")
        except Exception as sm_close_exc:
            JOB.add_log("ERROR", "Session close FAILED", str(sm_close_exc))
            # Still try manual close as last resort
            try:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
            except Exception:
                pass
        
        JOB.update(browser_session_created=False, profile_ready=False, login_required=False, verification_required=False, ready_to_scrape=False, browser_url="")
        broadcast_job_snapshot(include_logs=False)


@app.route("/")
@app.route("/instagram")
def home():
    return render_template(
        "dashboard.html",
        platform_config=build_platform_config("instagram"),
        stats=empty_stats(),
        features=dashboard_features(),
        logs=[],
    )


@app.route("/facebook")
def facebook_home():
    return render_template(
        "dashboard.html",
        platform_config=build_platform_config("facebook"),
        stats=empty_stats(),
        features=dashboard_features(),
        logs=[],
    )


@app.route("/tiktok")
def tiktok_home():
    return render_template(
        "dashboard.html",
        platform_config=build_platform_config("tiktok"),
        stats=empty_stats(),
        features=dashboard_features(),
        logs=[],
    )


@app.get("/assets/<path:filename>")
def asset_file(filename: str):
    allowed_files = {"icons8-about-us.svg", "icons8-expand-50.png"}
    if filename not in allowed_files:
        return jsonify({"ok": False, "errors": ["Asset not allowed."]}), 404

    return send_file(Path(__file__).with_name(filename))


@app.post("/api/collect-comments")
def collect_comments():
    if not JOB.request_collect_comments():
        return jsonify({"ok": False, "errors": ["Not currently waiting for comment collection decision."]}), 409
    JOB.add_log("INFO", "Comment collection requested", "User chose to collect all comments.")
    return jsonify({"ok": True, "status": JOB.snapshot(include_logs=False)})


@app.post("/api/skip-comments")
def skip_comments():
    if not JOB.request_skip_comments():
        return jsonify({"ok": False, "errors": ["Not currently waiting for comment collection decision."]}), 409
    JOB.add_log("INFO", "Comment collection skipped", "User chose to skip comment collection.")
    return jsonify({"ok": True, "status": JOB.snapshot(include_logs=False)})


@app.post("/facebook/api/collect-comments")
def facebook_collect_comments():
    return app_fb.collect_comments()


@app.post("/facebook/api/skip-comments")
def facebook_skip_comments():
    return app_fb.skip_comments()


@app.post("/facebook/api/validate")
def facebook_validate():
    return app_fb.validate_inputs()


@app.post("/facebook/api/start")
def facebook_start():
    return app_fb.start_scrape()


@app.get("/facebook/api/status")
def facebook_status():
    return app_fb.status()


@app.post("/facebook/api/clear-logs")
def facebook_clear_logs():
    return app_fb.clear_logs()


@app.post("/facebook/api/cancel")
def facebook_cancel():
    return app_fb.cancel_scrape()


@app.post("/facebook/api/go")
def facebook_go():
    return app_fb.go_signal()


@app.post("/facebook/api/force-ready")
def facebook_force_ready():
    return app_fb.force_ready()


@app.post("/facebook/api/focus-browser")
def facebook_focus_browser():
    return app_fb.focus_browser()


@app.get("/facebook/api/download")
def facebook_download():
    return app_fb.download_file()


@sock.route("/ws/dashboard")
def dashboard_socket(ws):
    client = DASHBOARD_HUB.register(ws)
    try:
        client.send({"type": "snapshot", "data": JOB.snapshot(include_logs=True)})

        while True:
            raw_message = ws.receive()
            if raw_message is None:
                break

            try:
                message = json.loads(raw_message)
            except Exception:
                continue

            message_type = str(message.get("type", "")).strip()
            if message_type == "request_snapshot":
                client.send({"type": "snapshot", "data": JOB.snapshot(include_logs=True)})
                continue

            if message_type != "control":
                continue

            action = str(message.get("action", "")).strip()
            if not action:
                continue

            if action == "pause":
                CONTROL_BUS.push({"action": "pause"})
                JOB.update(status="paused", active_task="Pause requested")
                JOB.add_log("WARN", "Pause requested", "Automation will pause at the next safe checkpoint.")
                continue

            if action == "resume":
                CONTROL_BUS.push({"action": "resume"})
                JOB.update(status="running", active_task="Resuming automation")
                JOB.add_log("INFO", "Resume requested", "Automation will resume at the next safe checkpoint.")
                continue

            CONTROL_BUS.push(message)
            if action == "focus_browser":
                JOB.add_log("INFO", "Control received", "Browser focus requested from the dashboard.")
    except Exception:
        pass
    finally:
        DASHBOARD_HUB.unregister(client)


@sock.route("/facebook/ws/dashboard")
def facebook_dashboard_socket(ws):
    return app_fb.dashboard_socket(ws)


@app.post("/api/validate")
def validate_inputs():
    config, errors, overwrite_required = validate_request_payload(request.get_json(silent=True) or {})
    if errors:
        return jsonify({"ok": False, "errors": errors, "overwriteRequired": overwrite_required}), 400

    return jsonify({"ok": True, "config": config_to_summary(config)})


@app.post("/api/start")
def start_scrape():
    global JOB_THREAD

    config, errors, overwrite_required = validate_request_payload(request.get_json(silent=True) or {})
    if errors:
        return jsonify({"ok": False, "errors": errors, "overwriteRequired": overwrite_required}), 400

    if JOB_THREAD is not None and JOB_THREAD.is_alive():
        JOB.add_log("WARN", "Prevented duplicate browser launch", "A job is already active, so a second browser session was not created.")
        return jsonify({"ok": False, "errors": ["A scraping job is already running."]}), 409

    JOB.reset()
    CONTROL_BUS.reset()
    broadcast_job_snapshot(include_logs=True)
    JOB_THREAD = threading.Thread(target=run_scrape_job, args=(config,), daemon=True)
    JOB_THREAD.start()

    return jsonify({"ok": True, "status": JOB.snapshot()})


@app.get("/api/status")
def status():
    return jsonify(JOB.snapshot())


@app.post("/api/clear-logs")
def clear_logs():
    with JOB.lock:
        JOB.logs = []
    broadcast_job_snapshot(include_logs=True)
    return jsonify({"ok": True, "status": JOB.snapshot()})


@app.post("/api/cancel")
def cancel_scrape():
    if not JOB.request_cancel():
        return jsonify({"ok": False, "errors": ["No active scraping job to cancel."], "status": JOB.snapshot()}), 409

    JOB.add_log("WARN", "Cancellation requested", "The scraper will stop at the next safe checkpoint.")
    return jsonify({"ok": True, "status": JOB.snapshot()})


@app.post("/api/go")
def go_signal():
    snapshot = JOB.snapshot()
    if not snapshot.get("browserSessionCreated"):
        JOB.add_log("WARN", "Blocked GO", "GO was rejected because no browser session is active.")
        return jsonify({"ok": False, "errors": ["No browser session is active. Click Run / Start first."], "status": snapshot}), 409
    if snapshot.get("status") in {"waiting_login", "waiting_verification", "captcha"} or snapshot.get("verificationRequired"):
        JOB.add_log("WARN", "Blocked GO", "GO was rejected because Instagram verification is still required.")
        return jsonify({"ok": False, "errors": ["Please finish Instagram login or verification first."], "status": snapshot}), 409
    if snapshot.get("loginRequired") or not snapshot.get("profileReady"):
        JOB.add_log("WARN", "Blocked GO", "GO was rejected because Instagram login/profile readiness is not complete yet.")
        return jsonify({"ok": False, "errors": ["Please finish Instagram login or verification first."], "status": snapshot}), 409
    if snapshot.get("status") != "ready":
        JOB.add_log("WARN", "Blocked GO", f"GO was rejected because the job state is {snapshot.get('status')}.")
        return jsonify({"ok": False, "errors": ["The scraper is not ready for the GO signal yet."], "status": snapshot}), 409
    if not JOB.request_go():
        JOB.add_log("WARN", "Blocked GO", "GO was rejected because the session was already continuing.")
        return jsonify({"ok": False, "errors": ["The scraper is not waiting for the GO signal."], "status": JOB.snapshot()}), 409

    JOB.add_log("INFO", "Reusing existing browser session", "GO continues the existing Playwright page and browser session.")

    return jsonify({"ok": True, "status": JOB.snapshot()})


@app.post("/api/force-ready")
def force_ready():
    snapshot = JOB.snapshot(include_logs=False)
    if snapshot.get("status") in {"running", "completed", "failed", "cancelled", "stopped"}:
        return jsonify({"ok": False, "errors": ["Force Ready is only available before extraction starts."], "status": snapshot}), 409
    if not snapshot.get("browserSessionCreated"):
        return jsonify({"ok": False, "errors": ["No browser session is active. Start a run first."], "status": snapshot}), 409

    JOB.update(
        status="ready",
        active_task="Ready for extraction",
        profile_ready=True,
        login_required=False,
        verification_required=False,
        ready_to_scrape=True,
    )
    JOB.add_log("WARN", "Force Ready enabled", "Manual override was used. Click GO / START EXTRACTION when the profile/page is visible.")
    return jsonify({"ok": True, "status": JOB.snapshot(include_logs=False)})


@app.post("/api/focus-browser")
def focus_browser():
    snapshot = JOB.snapshot()
    if snapshot["status"] not in {"preparing", "loading_session", "waiting_login", "waiting_verification", "captcha", "ready", "running", "paused"}:
        return jsonify({"ok": False, "errors": ["No active browser session is available to focus."], "status": snapshot}), 409
    if not snapshot.get("localBrowserWindow"):
        return jsonify({"ok": False, "errors": ["This environment does not have a local browser window to focus."], "status": snapshot}), 409

    CONTROL_BUS.push({"action": "focus_browser"})
    JOB.add_log("INFO", "Browser focus requested", "Attempting to bring the Playwright browser window to the front.")
    return jsonify({"ok": True, "status": JOB.snapshot()})


@app.get("/api/download")
def download_output():
    snapshot = JOB.snapshot()
    output_file = snapshot.get("outputFile") or ""
    output_path = Path(output_file)

    if snapshot.get("status") != "completed":
        return jsonify({"ok": False, "errors": ["The Excel file is not ready yet."]}), 409
    if not output_file or not output_path.exists():
        return jsonify({"ok": False, "errors": ["The Excel output file could not be found."]}), 404

    return send_file(output_path.resolve(), as_attachment=True, download_name=output_path.name)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
