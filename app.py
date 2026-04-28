from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request, send_file
from flask_sock import Sock
from playwright.sync_api import sync_playwright

from core.etl.etl_engine import DataBuffer, ETLPipeline
from core.logging.logger import LogLevel, ProductionLogger
from core.platforms import PlatformAdapter
from core.platforms.registry import get_platform_adapter, list_platform_adapters
from core.state.machine import ScrapeJobState as CoreScrapeJobState, ScrapeState


APP_ROOT = Path(__file__).resolve().parent
DATA_DIR = APP_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOGIN_READY_TIMEOUT = 180_000
PAGE_READY_TIMEOUT = 45_000

app = Flask(__name__)
sock = Sock(app)


@dataclass
class WebScrapeConfig:
    target_url: str
    scroll_rounds: int
    start_date: datetime
    end_date: Optional[datetime]
    output_file: str
    collection_type: Optional[str] = None


class ScrapeCancelled(Exception):
    """Raised when a scrape is cancelled by the user."""


class DashboardHub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.clients: dict[str, list] = {"instagram": [], "facebook": []}

    def register(self, platform: str, ws) -> None:
        with self.lock:
            self.clients[platform].append(ws)

    def unregister(self, platform: str, ws) -> None:
        with self.lock:
            if ws in self.clients.get(platform, []):
                self.clients[platform].remove(ws)

    def broadcast(self, platform: str, payload: dict[str, Any]) -> None:
        with self.lock:
            clients = list(self.clients.get(platform, []))

        dead = []
        message = json.dumps(payload)
        for ws in clients:
            try:
                ws.send(message)
            except Exception:
                dead.append(ws)

        if dead:
            with self.lock:
                for ws in dead:
                    if ws in self.clients.get(platform, []):
                        self.clients[platform].remove(ws)


DASHBOARD = DashboardHub()
PRODUCTION_LOGGER = ProductionLogger(persistence_dir=APP_ROOT)


class JobController:
    def __init__(self, adapter: PlatformAdapter) -> None:
        self.adapter = adapter
        self.platform = adapter.key
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.core_state = CoreScrapeJobState()
        self.buffer = DataBuffer(max_size=200)
        self.etl = ETLPipeline(output_dir=DATA_DIR, platform=self.platform)
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.status = "idle"
            self.active_task = "Waiting for input"
            self.current_post = ""
            self.current_scroll_round = 0
            self.total_scroll_rounds = 0
            self.posts_found = 0
            self.total_posts = 0
            self.current_post_index = 0
            self.posts_processed = 0
            self.posts_success = 0
            self.skipped_posts = 0
            self.errors = 0
            self.progress = 0
            self.output_file = ""
            self.config_summary: dict[str, Any] = {}
            self.logs: list[dict[str, str]] = []
            self.cancel_requested = False
            self.go_requested = False
            self.browser_session_created = False
            self.page_ready = False
            self.login_required = False
            self.verification_required = False
            self.ready_to_scrape = False
            self.browser_url = ""
            self.started_at: Optional[float] = None
            self.finished_at: Optional[float] = None
            self.go_event = threading.Event()
            self.core_state.reset()

    def _transition(self, state: ScrapeState, reason: str) -> None:
        success, message = self.core_state.transition_to(state, reason)
        if success:
            self._log(LogLevel.INFO, "State transition", message)
        else:
            self._log(LogLevel.WARN, "State transition skipped", message)

    def _log(self, level: LogLevel, action: str, details: str = "") -> None:
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": level.value,
            "action": action,
            "details": details,
        }
        with self.lock:
            self.logs.insert(0, entry)
            self.logs = self.logs[:250]
        PRODUCTION_LOGGER.log(level, action, details)
        DASHBOARD.broadcast(self.platform, {"type": "log", "data": entry})

    def _broadcast_snapshot(self) -> None:
        DASHBOARD.broadcast(self.platform, {"type": "snapshot", "data": self.snapshot(include_logs=True)})

    def _set_status(self, status: str, task: str) -> None:
        with self.lock:
            self.status = status
            self.active_task = task
        self._broadcast_snapshot()

    def snapshot(self, include_logs: bool = False) -> dict[str, Any]:
        with self.lock:
            success_rate = round(100 * self.posts_success / self.posts_processed) if self.posts_processed else 0
            health = max(0, 100 - min(self.errors * 8, 70))
            payload = {
                "platform": self.platform,
                "status": self.status,
                "state": self.status,
                "activeTask": self.active_task,
                "currentPost": self.current_post,
                "currentPostUrl": self.current_post,
                "scrollRound": self.current_scroll_round,
                "maxScrollRounds": self.total_scroll_rounds,
                "postsFound": self.posts_found,
                "totalPosts": self.total_posts,
                "currentPostIndex": self.current_post_index,
                "postsProcessed": self.posts_processed,
                "postsSuccess": self.posts_success,
                "skippedPosts": self.skipped_posts,
                "errors": self.errors,
                "progress": self.progress,
                "successRate": success_rate,
                "success_rate": success_rate,
                "scrapeHealth": health,
                "outputFile": self.output_file,
                "config": self.config_summary,
                "cancelRequested": self.cancel_requested,
                "goRequested": self.go_requested,
                "browserOpen": self.browser_session_created,
                "pageReady": self.page_ready,
                "loginRequired": self.login_required,
                "verificationRequired": self.verification_required,
                "readyToScrape": self.ready_to_scrape,
                "browserUrl": self.browser_url,
                "current_scroll_round": self.current_scroll_round,
                "total_scroll_rounds": self.total_scroll_rounds,
                "posts_found": self.posts_found,
                "total_posts": self.total_posts,
                "current_post_index": self.current_post_index,
                "posts_processed": self.posts_processed,
                "posts_success": self.posts_success,
                "skipped_posts": self.skipped_posts,
                "errors_count": self.errors,
                "canGo": (
                    self.status == "ready"
                    and self.browser_session_created
                    and self.page_ready
                    and not self.verification_required
                    and self.ready_to_scrape
                    and not self.go_requested
                ),
                "downloadReady": self.status == "completed" and bool(self.output_file) and Path(self.output_file).exists(),
                "browserMode": "Opened Browser Window" if self.adapter.uses_local_browser_window() else "Headless Browser Session",
                "dateCoverage": self.config_summary.get("dateCoverage", "-"),
            }
            if include_logs:
                payload["logs"] = list(self.logs)
            return payload

    def request_cancel(self) -> bool:
        with self.lock:
            if self.status in {"completed", "failed", "cancelled", "idle"}:
                return False
            self.cancel_requested = True
            self.status = "cancelled"
            self.active_task = "Cancelling extraction"
            self.go_event.set()
        self._transition(ScrapeState.COLLECTION_CANCELLED, "Cancel requested")
        self._broadcast_snapshot()
        return True

    def request_go(self) -> bool:
        with self.lock:
            if self.status != "ready" or self.go_requested or not self.ready_to_scrape:
                return False
            self.go_requested = True
            self.go_event.set()
        return True

    def clear_logs(self) -> None:
        with self.lock:
            self.logs = []
        self._broadcast_snapshot()

    def focus_browser(self) -> None:
        self._log(LogLevel.INFO, "Focus browser", "Bring the browser window to the foreground.")

    def _log_hook(self, level: str, action: str, details: str = "") -> None:
        level_map = {
            "INFO": LogLevel.INFO,
            "SUCCESS": LogLevel.SUCCESS,
            "WARN": LogLevel.WARN,
            "ERROR": LogLevel.ERROR,
        }
        self._log(level_map.get(level.upper(), LogLevel.INFO), action, details)

    def _progress_hook(self, round_index: int, total_rounds: int, posts_found: int) -> None:
        with self.lock:
            self.current_scroll_round = round_index
            self.total_scroll_rounds = total_rounds
            self.posts_found = posts_found
            self.progress = min(20, round(20 * round_index / max(total_rounds, 1)))
        self._broadcast_snapshot()

    def _flush_buffer(self) -> None:
        for record in self.buffer.flush():
            self.etl.save_post(record)

    def _wait_for_ready(self, page, context, config: WebScrapeConfig) -> None:
        deadline = time.monotonic() + (LOGIN_READY_TIMEOUT / 1000)
        verification_logged = False
        login_logged = False
        last_ping = 0.0

        while time.monotonic() < deadline:
            if self.cancel_requested:
                raise ScrapeCancelled("Cancelled while waiting for login/verification.")

            verification_required, verification_reason = self.adapter.detect_verification_gate(page)
            if verification_required:
                with self.lock:
                    self.status = "waiting_verification"
                    self.active_task = "Verification required"
                    self.login_required = True
                    self.verification_required = True
                    self.page_ready = False
                    self.ready_to_scrape = False
                    self.browser_url = page.url or config.target_url
                if not verification_logged:
                    self._log(LogLevel.WARN, "Verification checkpoint detected", verification_reason)
                    self._log(LogLevel.WARN, "Waiting for user verification", "Please complete verification in the opened browser.")
                    verification_logged = True
                if time.monotonic() - last_ping >= 10:
                    self._log(LogLevel.INFO, "Still waiting for verification", "Please complete verification in the opened browser.")
                    last_ping = time.monotonic()
                self._broadcast_snapshot()
                time.sleep(0.35)
                continue

            login_required, login_reason = self.adapter.detect_login_gate(page)
            if login_required:
                with self.lock:
                    self.status = "waiting_login"
                    self.active_task = "Login required"
                    self.login_required = True
                    self.verification_required = False
                    self.page_ready = False
                    self.ready_to_scrape = False
                    self.browser_url = page.url or config.target_url
                if not login_logged:
                    self._log(LogLevel.WARN, "Login required", login_reason or "Login required before extraction.")
                    self._log(LogLevel.WARN, "Waiting for login", "Please complete login in the opened browser.")
                    login_logged = True
                if self.adapter.uses_local_browser_window():
                    try:
                        self.adapter.open_login_form(page, config.target_url)
                    except Exception:
                        pass
                self._broadcast_snapshot()
                time.sleep(0.35)
                continue

            if self.adapter.page_ready_for_collection(page):
                self.adapter.save_storage_state(context, self._log_hook)
                with self.lock:
                    self.status = "ready"
                    self.active_task = "Ready for GO signal"
                    self.page_ready = True
                    self.login_required = False
                    self.verification_required = False
                    self.ready_to_scrape = True
                    self.browser_url = page.url or config.target_url
                self._transition(ScrapeState.PAGE_READY, "Page ready")
                self._log(LogLevel.SUCCESS, f"{self.adapter.name} page ready", "Target page is visible and ready.")
                self._log(LogLevel.INFO, "Ready for GO signal", "Click GO / START EXTRACTION to continue.")
                self._broadcast_snapshot()
                return

            time.sleep(0.3)

        raise TimeoutError("Page did not become ready before timeout.")

    def run(self, config: WebScrapeConfig) -> None:
        self._transition(ScrapeState.VALIDATION, "Inputs validated")
        self._transition(ScrapeState.BROWSER_INIT, "Launching browser")

        with sync_playwright() as p:
            launch_browser = self.adapter.launch_browser()
            browser, context = launch_browser(p)
            context.route("**/*", self._route_nonessential_resources)
            page = context.new_page()

            with self.lock:
                self.browser_session_created = True
                self.browser_url = page.url or config.target_url
                self.started_at = time.time()
                self.output_file = config.output_file
            self._log(LogLevel.INFO, f"{self.adapter.name} browser opened", "Playwright browser session created.")
            try:
                viewport = page.viewport_size or {}
                self._log(
                    LogLevel.INFO,
                    "Viewport",
                    f"{viewport.get('width', 'auto')}x{viewport.get('height', 'auto')} ({self.adapter.name})",
                )
            except Exception:
                pass

            page.goto(config.target_url, wait_until="domcontentloaded", timeout=60_000)

            if self.adapter.auto_login_if_needed(page, context, config.target_url, log_hook=self._log_hook):
                self._log(LogLevel.SUCCESS, "Session restored", "Auto-login restored the session.")

            self._wait_for_ready(page, context, config)

            self.go_event.wait()
            if self.cancel_requested:
                raise ScrapeCancelled("Cancelled before GO signal.")

            with self.lock:
                self.status = "running"
                self.active_task = "Starting extraction"
                self.ready_to_scrape = False
                self.go_requested = True
                self.progress = 20
            self._transition(ScrapeState.COLLECTION_RUNNING, "GO received")
            self._log(LogLevel.SUCCESS, "GO signal received", "Starting extraction.")
            self._log(LogLevel.INFO, "Starting extraction", "Scrolling and extracting posts.")
            self._broadcast_snapshot()

            diagnostics: dict[str, Any] = {}
            unavailable_metrics = 0
            if hasattr(self.adapter, "set_target_url"):
                try:
                    self.adapter.set_target_url(config.target_url)
                except Exception:
                    pass
            while True:
                try:
                    links = self.adapter.collect_post_links(
                        page,
                        scroll_rounds=config.scroll_rounds,
                        start_date=config.start_date,
                        log_hook=self._log_hook,
                        progress_hook=self._progress_hook,
                        cancel_check=lambda: self.cancel_requested,
                        diagnostics=diagnostics,
                    )
                    break
                except Exception as exc:
                    if exc.__class__.__name__ != "AuthRequiredError":
                        raise
                    self._log(LogLevel.WARN, "Auth required during scroll", str(exc))
                    self._wait_for_ready(page, context, config)
            with self.lock:
                self.posts_found = len(links)
                self.total_posts = len(links)
                self.progress = 30
            self._log(LogLevel.SUCCESS, "Collected links", f"Found {len(links)} unique links.")
            self._broadcast_snapshot()

            posts: list[Any] = []
            total = len(links)
            for index, link in enumerate(links, start=1):
                if self.cancel_requested:
                    raise ScrapeCancelled("Cancelled during extraction.")

                with self.lock:
                    self.current_post = link
                    self.current_post_index = index
                    self.progress = 30 + round(60 * (index - 1) / max(total, 1))
                self._broadcast_snapshot()

                self._log(LogLevel.INFO, "Processing post", f"{index}/{total}: {link}")

                try:
                    post = self.adapter.extract_post(page, link, config.collection_type, log_hook=self._log_hook)
                    if isinstance(post, dict):
                        unavailable_metrics += int(post.get("unavailable_metrics") or 0)
                        post_date = post.get("post_date_obj")
                    else:
                        post_date = getattr(post, "post_date_obj", None)
                    if post_date and (post_date < config.start_date or (config.end_date and post_date > config.end_date)):
                        with self.lock:
                            self.skipped_posts += 1
                        self._log(LogLevel.WARN, "Post skipped", "Outside selected date coverage.")
                        continue
                    posts.append(post)
                    record = self.adapter.post_to_record(post)
                    if not self.buffer.add(record):
                        self._flush_buffer()
                        self.buffer.add(record)
                    with self.lock:
                        self.posts_success += 1
                except Exception as exc:
                    if exc.__class__.__name__ == "AuthRequiredError":
                        self._log(LogLevel.WARN, "Auth required during extraction", str(exc))
                        self._wait_for_ready(page, context, config)
                        continue
                    with self.lock:
                        self.errors += 1
                    self._log(LogLevel.WARN, "Extraction failed", f"{link} ({type(exc).__name__}: {exc})")

                with self.lock:
                    self.posts_processed += 1

            self._flush_buffer()
            coverage_label = self.adapter.format_date_coverage(config.start_date, config.end_date)
            if hasattr(self.adapter, "set_run_diagnostics"):
                try:
                    self.adapter.set_run_diagnostics(
                        {
                            "Total collected links": len(links),
                            "Total processed": self.posts_processed,
                            "Successful extractions": self.posts_success,
                            "Skipped posts": self.skipped_posts,
                            "Unavailable metrics": unavailable_metrics,
                            "Errors": self.errors,
                            "Viewport": page.viewport_size or {},
                            "Page readiness": self.status,
                        }
                    )
                except Exception:
                    pass
            self.adapter.export_excel(posts, config.output_file, coverage_label, config.collection_type)
            with self.lock:
                self.progress = 100
                self.status = "completed"
                self.active_task = "Completed"
                self.finished_at = time.time()
            self._transition(ScrapeState.COLLECTION_COMPLETED, "Exported")
            self._log(LogLevel.SUCCESS, "Excel saved", config.output_file)
            self._broadcast_snapshot()

            context.close()
            if browser is not None:
                browser.close()

    def start(self, config: WebScrapeConfig) -> None:
        if self.thread and self.thread.is_alive():
            raise RuntimeError("A scraping job is already running.")

        self.reset()
        self.config_summary = {
            "link": config.target_url,
            "dateCoverage": self.adapter.format_date_coverage(config.start_date, config.end_date),
        }
        with self.lock:
            self.status = "preparing"
            self.active_task = "Creating browser session"
            self.total_scroll_rounds = config.scroll_rounds
            self.output_file = config.output_file
            self.ready_to_scrape = False
            self.go_event.clear()

        self._transition(ScrapeState.VALIDATION, "Start requested")
        self._broadcast_snapshot()

        def _run():
            try:
                self.run(config)
            except ScrapeCancelled as exc:
                with self.lock:
                    self.status = "cancelled"
                    self.active_task = "Cancelled"
                    self.finished_at = time.time()
                self._transition(ScrapeState.COLLECTION_CANCELLED, str(exc))
                self._log(LogLevel.WARN, "Job cancelled", str(exc))
                self._broadcast_snapshot()
            except Exception as exc:
                with self.lock:
                    self.status = "failed"
                    self.active_task = "Failed"
                    self.errors += 1
                    self.finished_at = time.time()
                self._transition(ScrapeState.COLLECTION_FAILED, f"{type(exc).__name__}: {exc}")
                self._log(LogLevel.ERROR, "Job failed", f"{type(exc).__name__}: {exc}")
                self._broadcast_snapshot()

        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()

    def _route_nonessential_resources(self, route) -> None:
        try:
            resource_type = route.request.resource_type
        except Exception:
            resource_type = ""
        if resource_type in {"image", "media", "font"}:
            route.abort()
            return
        route.continue_()


INSTAGRAM = JobController(get_platform_adapter("instagram"))
FACEBOOK = JobController(get_platform_adapter("facebook"))


def parse_date(value: str, field_name: str) -> tuple[Optional[datetime], Optional[str]]:
    raw = (value or "").strip()
    if not raw:
        return None, f"{field_name} is required."
    try:
        return datetime.strptime(raw, "%Y-%m-%d"), None
    except ValueError:
        return None, f"{field_name} must use YYYY-MM-DD."


def parse_payload(adapter: PlatformAdapter, payload: dict[str, Any]) -> tuple[Optional[WebScrapeConfig], list[str]]:
    errors: list[str] = []
    target_url = adapter.normalize_url(str(payload.get(adapter.platform_config().link_payload_key, "")))
    if not target_url:
        errors.append(f"Enter a valid {adapter.name} link.")

    raw_scroll = str(payload.get("scrollRounds", "")).strip()
    if not raw_scroll.isdigit() or int(raw_scroll) <= 0:
        errors.append("Scroll rounds must be a positive number.")
        scroll_rounds = 0
    else:
        scroll_rounds = int(raw_scroll)

    start_date, start_error = parse_date(str(payload.get("startDate", "")), "Start date")
    if start_error:
        errors.append(start_error)

    latest_mode = bool(payload.get("latestMode", True))
    end_date: Optional[datetime] = None
    if not latest_mode:
        end_date, end_error = parse_date(str(payload.get("endDate", "")), "End date")
        if end_error:
            errors.append(end_error)

    if start_date and end_date and end_date < start_date:
        errors.append("End date cannot be earlier than the start date.")

    output_file = str(payload.get("outputFile", "")).strip()
    if not output_file:
        errors.append("Excel filename is required.")
    if output_file and not output_file.lower().endswith(".xlsx"):
        output_file = f"{output_file}.xlsx"

    collection_type = payload.get("collectionType")

    if errors:
        return None, errors

    return WebScrapeConfig(
        target_url=target_url or "",
        scroll_rounds=scroll_rounds,
        start_date=start_date or datetime.now(),
        end_date=end_date,
        output_file=output_file,
        collection_type=collection_type,
    ), []


def build_platform_switch(active_key: str) -> list[dict[str, Any]]:
    platforms = []
    for adapter in list_platform_adapters():
        config = adapter.platform_config()
        platforms.append(
            {
                "key": config.key,
                "label": config.name,
                "shortLabel": config.name[:2].upper(),
                "meta": "Active" if config.key == active_key else "Ready",
                "href": "/" if config.key == "instagram" else f"/{config.key}",
                "active": config.key == active_key,
                "placeholder": False,
            }
        )
    return platforms


def build_platform_payload(adapter: PlatformAdapter) -> dict[str, Any]:
    config = adapter.platform_config()
    return {
        "platformName": config.name,
        "workspaceSubtitle": config.workspace_subtitle,
        "heroTitle": config.hero_title,
        "heroText": config.hero_text,
        "linkLabel": config.link_label,
        "linkPlaceholder": config.link_placeholder,
        "roundsLabel": config.rounds_label,
        "progressCardLabel": config.progress_card_label,
        "currentItemLabel": config.current_item_label,
        "browserSessionTitle": config.browser_session_title,
        "browserSessionDescription": config.browser_session_description,
        "activityLogsTitle": config.activity_logs_title,
        "depthTagLabel": config.depth_tag_label,
        "latestModeLabel": config.latest_mode_label,
        "reviewTitle": f"Review {config.name} extraction setup",
        "linksFoundLabel": "Posts Found",
        "defaultOutputFile": config.default_output_file,
        "defaultLatestMode": config.default_latest_mode,
        "collectionTypeEnabled": config.collection_type_enabled,
        "collectionTypeLabel": config.collection_type_label,
        "collectionTypeOptions": config.collection_type_options,
        "apiBase": config.api_base,
        "wsPath": config.ws_path,
        "linkPayloadKey": config.link_payload_key,
    }


def render_dashboard(adapter: PlatformAdapter) -> str:
    config = adapter.platform_config()
    payload = build_platform_payload(adapter)
    payload["platforms"] = build_platform_switch(adapter.key)
    return render_template(
        "dashboard.html",
        platform_config=payload,
        stats=[
            {"label": "Posts Found", "value": "0"},
            {"label": "Progress", "value": "0%"},
            {"label": "Success Rate", "value": "0%"},
            {"label": "Errors", "value": "0"},
        ],
        features=build_features(adapter),
        platform_config_json=json.dumps(payload),
    )


def build_features(adapter: PlatformAdapter) -> list[dict[str, str]]:
    if adapter.key == "facebook":
        return [
            {
                "tag": "FB",
                "title": "Manual Login Control",
                "description": "If Facebook triggers login or verification, the system pauses and waits for you to finish in Chromium.",
            },
            {
                "tag": "LOG",
                "title": "Live Activity Logs",
                "description": "Follow real-time Facebook progress and checkpoint messages from the backend.",
            },
            {
                "tag": "ETL",
                "title": "SQLite Buffering",
                "description": "Post metrics are buffered and deduplicated before Excel export.",
            },
            {
                "tag": "XLS",
                "title": "Excel Export",
                "description": "Export visible Facebook metrics into an Excel workbook.",
            },
        ]
    return [
        {
            "tag": "IG",
            "title": "Manual Login Control",
            "description": "When Instagram requests login or verification, the system waits for manual completion.",
        },
        {
            "tag": "LOG",
            "title": "Live Activity Logs",
            "description": "Monitor Instagram progress, scroll rounds, and extraction detail in real time.",
        },
        {
            "tag": "ETL",
            "title": "SQLite Buffering",
            "description": "Post metrics are buffered and deduplicated before Excel export.",
        },
        {
            "tag": "XLS",
            "title": "Excel Export",
            "description": "Export visible Instagram metrics into an Excel workbook.",
        },
    ]


@app.route("/")
def instagram_dashboard():
    return render_dashboard(get_platform_adapter("instagram"))


@app.get("/assets/<path:filename>")
def asset_file(filename: str):
    asset_path = (APP_ROOT / filename).resolve()
    if APP_ROOT not in asset_path.parents or not asset_path.is_file():
        return jsonify({"success": False, "error": "Asset not found."}), 404
    return send_file(asset_path)


@app.route("/facebook")
def facebook_dashboard():
    return render_dashboard(get_platform_adapter("facebook"))


@app.post("/api/validate")
def validate_instagram():
    payload = request.get_json(silent=True) or {}
    config, errors = parse_payload(get_platform_adapter("instagram"), payload)
    if errors:
        return jsonify({"success": False, "error": "\n".join(errors)}), 400
    return jsonify({"success": True, "message": "Instagram setup validated.", "config": payload})


@app.post("/facebook/api/validate")
def validate_facebook():
    payload = request.get_json(silent=True) or {}
    config, errors = parse_payload(get_platform_adapter("facebook"), payload)
    if errors:
        return jsonify({"success": False, "error": "\n".join(errors)}), 400
    return jsonify({"success": True, "message": "Facebook setup validated.", "config": payload})


@app.post("/api/start")
def start_instagram():
    payload = request.get_json(silent=True) or {}
    config, errors = parse_payload(get_platform_adapter("instagram"), payload)
    if errors:
        return jsonify({"success": False, "error": "\n".join(errors)}), 400
    try:
        INSTAGRAM.start(config)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    return jsonify({"success": True, "message": "Browser session created. Complete login and wait for GO.", "status": INSTAGRAM.snapshot()})


@app.post("/facebook/api/start")
def start_facebook():
    payload = request.get_json(silent=True) or {}
    config, errors = parse_payload(get_platform_adapter("facebook"), payload)
    if errors:
        return jsonify({"success": False, "error": "\n".join(errors)}), 400
    try:
        FACEBOOK.start(config)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    return jsonify({"success": True, "message": "Browser session created. Complete login and wait for GO.", "status": FACEBOOK.snapshot()})


@app.get("/api/status")
def status_instagram():
    return jsonify(INSTAGRAM.snapshot(include_logs=True))


@app.get("/facebook/api/status")
def status_facebook():
    return jsonify(FACEBOOK.snapshot(include_logs=True))


@app.post("/api/go")
def go_instagram():
    if not INSTAGRAM.request_go():
        return jsonify({"success": False, "error": "Please complete login/verification first."}), 409
    return jsonify({"success": True, "message": "GO signal received."})


@app.post("/facebook/api/go")
def go_facebook():
    if not FACEBOOK.request_go():
        return jsonify({"success": False, "error": "Please complete login/verification first."}), 409
    return jsonify({"success": True, "message": "GO signal received."})


@app.post("/api/cancel")
def cancel_instagram():
    if not INSTAGRAM.request_cancel():
        return jsonify({"success": False, "error": "No active Instagram job to cancel."}), 409
    return jsonify({"success": True, "message": "Instagram extraction cancelled."})


@app.post("/api/clear-logs")
def clear_logs_instagram():
    INSTAGRAM.clear_logs()
    return jsonify({"success": True, "message": "Logs cleared."})


@app.post("/api/focus-browser")
def focus_browser_instagram():
    INSTAGRAM.focus_browser()
    return jsonify({"success": True, "message": "Browser focus requested."})


@app.post("/facebook/api/cancel")
def cancel_facebook():
    if not FACEBOOK.request_cancel():
        return jsonify({"success": False, "error": "No active Facebook job to cancel."}), 409
    return jsonify({"success": True, "message": "Facebook extraction cancelled."})


@app.post("/facebook/api/clear-logs")
def clear_logs_facebook():
    FACEBOOK.clear_logs()
    return jsonify({"success": True, "message": "Logs cleared."})


@app.post("/facebook/api/focus-browser")
def focus_browser_facebook():
    FACEBOOK.focus_browser()
    return jsonify({"success": True, "message": "Browser focus requested."})


@app.get("/api/download")
def download_instagram():
    output_file = INSTAGRAM.output_file
    if not output_file or not Path(output_file).exists():
        return jsonify({"success": False, "error": "Excel file not found."}), 404
    return send_file(Path(output_file).resolve(), as_attachment=True, download_name=Path(output_file).name)


@app.get("/facebook/api/download")
def download_facebook():
    output_file = FACEBOOK.output_file
    if not output_file or not Path(output_file).exists():
        return jsonify({"success": False, "error": "Excel file not found."}), 404
    return send_file(Path(output_file).resolve(), as_attachment=True, download_name=Path(output_file).name)


@sock.route("/ws/dashboard")
def ws_instagram(ws):
    DASHBOARD.register("instagram", ws)
    try:
        ws.send(json.dumps({"type": "snapshot", "data": INSTAGRAM.snapshot(include_logs=True)}))
        while True:
            message = ws.receive()
            if message is None:
                break
    finally:
        DASHBOARD.unregister("instagram", ws)


@sock.route("/facebook/ws/dashboard")
def ws_facebook(ws):
    DASHBOARD.register("facebook", ws)
    try:
        ws.send(json.dumps({"type": "snapshot", "data": FACEBOOK.snapshot(include_logs=True)}))
        while True:
            message = ws.receive()
            if message is None:
                break
    finally:
        DASHBOARD.unregister("facebook", ws)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
