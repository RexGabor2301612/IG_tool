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

import facebook_to_excel as scraper


app = Flask(__name__)
sock = Sock(app)


class ScrapeCancelled(Exception):
    """Raised when the user cancels an active Facebook extraction job."""


@dataclass
class WebScrapeConfig:
    target_url: str
    scroll_rounds: int
    start_date: datetime
    end_date: Optional[datetime]
    output_file: str
    collection_type: str
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


class LiveCommandBus:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queue: list[dict[str, Any]] = []

    def reset(self) -> None:
        with self.lock:
            self.queue = []

    def push(self, command: dict[str, Any]) -> None:
        with self.lock:
            self.queue.append(command)

    def drain(self) -> list[dict[str, Any]]:
        with self.lock:
            items = list(self.queue)
            self.queue.clear()
            return items


class ScrapeJobState:
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
        self.page_ready = False
        self.login_required = False
        self.verification_required = False
        self.ready_to_scrape = False
        self.browser_url = ""
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None

    def add_log(self, level: str, action: str, details: str = "") -> None:
        with self.lock:
            entry = {
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": level.upper(),
                "action": action,
                "details": details,
            }
            self.logs.insert(0, entry)
            self.logs = self.logs[:250]
        broadcast_dashboard_event("log", entry)
        broadcast_job_snapshot(include_logs=False)

    def update(self, **kwargs: Any) -> None:
        with self.lock:
            for key, value in kwargs.items():
                setattr(self, key, value)
        broadcast_job_snapshot(include_logs=False)

    def request_cancel(self) -> bool:
        with self.lock:
            if self.status not in {"preparing", "loading_session", "waiting_login", "waiting_verification", "ready", "running"}:
                return False
            self.cancel_requested = True
            self.status = "cancelled"
            self.active_task = "Cancelling extraction"
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
                or not self.page_ready
                or not self.ready_to_scrape
            ):
                return False
            self.go_requested = True
            return True

    def should_go(self) -> bool:
        with self.lock:
            return self.go_requested

    def snapshot(self, include_logs: bool = True) -> dict[str, Any]:
        with self.lock:
            eligible_total = self.posts_in_range + self.failed_extractions
            if eligible_total > 0:
                success_rate = round(100 * self.posts_success / eligible_total)
            else:
                success_rate = 0

            health = max(0, 100 - min(self.errors * 8, 70))
            payload = {
                "status": self.status,
                "activeTask": self.active_task,
                "currentPost": self.current_post,
                "currentScrollRound": self.current_scroll_round,
                "totalScrollRounds": self.total_scroll_rounds,
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
                "outputFile": self.output_file,
                "config": self.config_summary,
                "cancelRequested": self.cancel_requested,
                "goRequested": self.go_requested,
                "browserSessionCreated": self.browser_session_created,
                "pageReady": self.page_ready,
                "loginRequired": self.login_required,
                "verificationRequired": self.verification_required,
                "readyToScrape": self.ready_to_scrape,
                "browserUrl": self.browser_url,
                "canGo": (
                    self.status == "ready"
                    and self.browser_session_created
                    and self.page_ready
                    and not self.verification_required
                    and self.ready_to_scrape
                    and not self.go_requested
                ),
                "canDownload": self.status == "completed" and bool(self.output_file) and Path(self.output_file).exists(),
            }
            if include_logs:
                payload["logs"] = list(self.logs)
            payload.update(browser_mode_payload())
            return payload


JOB = ScrapeJobState()
JOB_THREAD: Optional[threading.Thread] = None
DASHBOARD_HUB = DashboardHub()
CONTROL_BUS = LiveCommandBus()
LOGIN_READY_TIMEOUT = 180000
PAGE_READY_TIMEOUT = 30000
VERIFICATION_READY_TIMEOUT = 1800000


def using_local_browser_window() -> bool:
    return scraper.uses_local_browser_window()


def browser_mode_payload() -> dict[str, Any]:
    return {
        "browserMode": scraper.browser_runtime_mode(),
        "browserModeLabel": scraper.browser_mode_label(),
        "browserModeNote": scraper.browser_mode_note(),
        "previewInteractive": False,
        "localBrowserWindow": using_local_browser_window(),
    }


def login_wait_message() -> str:
    if using_local_browser_window():
        return (
            "A Chromium window has been opened for Facebook. Complete the Facebook login there, "
            "wait for the target page/feed to appear, then return here and click GO / START EXTRACTION."
        )
    return (
        "Facebook login is required, but this environment cannot open a local interactive browser window. "
        "Run the Facebook tool locally with PLAYWRIGHT_INTERACTIVE_BROWSER=true, or provide storage state / backend login support."
    )


def broadcast_dashboard_event(event_type: str, data: dict[str, Any]) -> None:
    DASHBOARD_HUB.broadcast(event_type, data)


def broadcast_job_snapshot(include_logs: bool = False) -> None:
    broadcast_dashboard_event("snapshot", JOB.snapshot(include_logs=include_logs))


def current_page_url(page, fallback: str = "") -> str:
    try:
        return page.url or fallback
    except Exception:
        return fallback


def sync_browser_url(page, fallback: str = "") -> None:
    JOB.update(browser_url=current_page_url(page, fallback))


def mark_page_ready(page, target_url: str, *, waiting_for_go: bool) -> None:
    url = current_page_url(page, target_url)
    JOB.update(
        status="ready" if waiting_for_go else "running",
        active_task="Ready for extraction" if waiting_for_go else "Page ready",
        browser_session_created=True,
        page_ready=True,
        login_required=False,
        verification_required=False,
        ready_to_scrape=waiting_for_go,
        browser_url=url,
        current_post=url,
    )


def open_login_form_in_same_tab(page, target_url: str) -> None:
    login_url = scraper.manual_login_url(target_url)
    if "/login" in current_page_url(page):
        return
    page.goto(login_url, wait_until="domcontentloaded", timeout=scraper.POST_GOTO_TIMEOUT)


def drain_control_commands(page) -> None:
    for command in CONTROL_BUS.drain():
        if command.get("action") == "focus_browser":
            try:
                page.bring_to_front()
            except Exception:
                pass


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
            "title": "Validated Facebook Inputs",
            "description": "The target link, collection depth, date coverage, and export filename are checked before a run begins.",
            "icon": "FB",
        },
        {
            "title": "Manual Login Control",
            "description": "If Facebook requires authentication, the system pauses and waits for manual login in the real Chromium window.",
            "icon": "LOG",
        },
        {
            "title": "Reliable Extraction",
            "description": "The backend collects visible public Facebook post metrics and skips unavailable data instead of inventing numbers.",
            "icon": "MET",
        },
        {
            "title": "Excel Export",
            "description": "Each run produces a workbook with post data and optional visible comment samples plus diagnostics when no rows match.",
            "icon": "XLS",
        },
    ]


def config_to_summary(config: WebScrapeConfig) -> dict[str, str]:
    return {
        "facebookLink": config.target_url,
        "scrollRounds": str(config.scroll_rounds),
        "startDate": config.start_date.strftime(scraper.DATE_INPUT_FORMAT),
        "endDate": config.end_date.strftime(scraper.DATE_INPUT_FORMAT) if config.end_date else "",
        "dateCoverage": scraper.format_date_coverage(config.start_date, config.end_date),
        "outputFile": config.output_file,
        "collectionType": config.collection_type.replace("_", " "),
    }


def validate_request_payload(payload: dict[str, Any]) -> tuple[Optional[WebScrapeConfig], list[str], bool]:
    errors: list[str] = []
    overwrite_required = False

    target_url = scraper.normalize_facebook_target_url(str(payload.get("facebookLink", "")))
    if target_url is None:
        errors.append("Enter a valid Facebook post, page, or profile link.")

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

    collection_type = str(payload.get("collectionType", "posts_only")).strip() or "posts_only"
    if collection_type not in {"posts_only", "posts_with_comments"}:
        errors.append("Collection type must be Posts only or Posts with visible comments.")

    overwrite = bool(payload.get("overwrite", False))
    if output_file and Path(output_file).exists() and not overwrite:
        overwrite_required = True
        errors.append(f"{output_file} already exists. Confirm overwrite or enter a new filename.")

    if errors:
        return None, errors, overwrite_required

    return (
        WebScrapeConfig(
            target_url=target_url or "",
            scroll_rounds=scroll_rounds,
            start_date=start_date or datetime.now(),
            end_date=end_date,
            output_file=output_file,
            collection_type=collection_type,
            overwrite=overwrite,
        ),
        [],
        False,
    )


def wait_for_user_login(page, context, target_url: str, *, waiting_for_go: bool) -> None:
    message = login_wait_message()
    if using_local_browser_window():
        try:
            page.bring_to_front()
        except Exception:
            pass

    JOB.update(
        status="waiting_login",
        active_task="Waiting for Facebook login",
        browser_session_created=True,
        page_ready=False,
        login_required=True,
        verification_required=False,
        ready_to_scrape=False,
        browser_url=current_page_url(page, target_url),
        current_post=current_page_url(page, target_url),
    )
    JOB.add_log("WARN", "Login required", "Facebook login is required before loading posts or comments.")
    JOB.add_log("WARN", "Waiting for manual login", message)
    broadcast_dashboard_event("login_required", {"message": message, "url": current_page_url(page, target_url)})

    if not using_local_browser_window():
        raise RuntimeError(message)

    login_deadline = time.monotonic() + (LOGIN_READY_TIMEOUT / 1000)
    verification_deadline = None
    login_form_logged = False
    login_page_opened = False
    returned_to_target = False
    login_submit_logged = False
    login_loop_logged = False
    verification_logged = False
    last_verification_ping = 0.0

    while True:
        if JOB.should_cancel():
            raise ScrapeCancelled("Cancelled while waiting for Facebook login.")

        drain_control_commands(page)
        sync_browser_url(page, target_url)

        current_url = current_page_url(page, target_url)
        if verification_logged and scraper.url_indicates_checkpoint_or_verification(current_url):
            JOB.update(
                status="waiting_verification",
                active_task="Facebook verification required",
                browser_session_created=True,
                page_ready=False,
                login_required=True,
                verification_required=True,
                ready_to_scrape=False,
                browser_url=current_url,
                current_post=current_url,
            )
            if time.monotonic() - last_verification_ping >= 12:
                JOB.add_log(
                    "INFO",
                    "Still waiting for verification",
                    "Facebook verification is still active. Complete it manually in the opened browser.",
                )
                last_verification_ping = time.monotonic()
            if verification_deadline is not None and time.monotonic() >= verification_deadline:
                raise TimeoutError("Facebook verification did not complete before timeout. Please try again after completing the checkpoint manually.")
            time.sleep(1.25)
            continue

        checkpoint_required, checkpoint_reason = scraper.detect_checkpoint_or_verification(page)
        if checkpoint_required:
            if verification_deadline is None:
                verification_deadline = time.monotonic() + (VERIFICATION_READY_TIMEOUT / 1000)
            JOB.update(
                status="waiting_verification",
                active_task="Facebook verification required",
                browser_session_created=True,
                page_ready=False,
                login_required=True,
                verification_required=True,
                ready_to_scrape=False,
                browser_url=current_page_url(page, target_url),
                current_post=current_page_url(page, target_url),
            )
            if not verification_logged:
                JOB.add_log("WARN", "Verification required", checkpoint_reason)
                if "captcha" in checkpoint_reason.lower() or "recaptcha" in checkpoint_reason.lower():
                    JOB.add_log("WARN", "reCAPTCHA detected", "Facebook is requiring a manual reCAPTCHA/security check before the page can be used.")
                JOB.add_log("WARN", "Waiting for user to complete verification", "Facebook verification required. Please complete it manually in the opened browser.")
                JOB.add_log("INFO", "Do not refresh or close browser", "Keep the same Chromium window open and let Facebook finish the verification flow.")
                broadcast_dashboard_event(
                    "verification_required",
                    {
                        "message": "Facebook verification required. Complete it manually in the opened browser and do not refresh or close the window.",
                        "url": current_page_url(page, target_url),
                    },
                )
                verification_logged = True
                last_verification_ping = time.monotonic()
            elif time.monotonic() - last_verification_ping >= 10:
                JOB.add_log("INFO", "Verification still pending", "Facebook verification is still active. Complete it manually in the opened browser and keep the same tab open.")
                last_verification_ping = time.monotonic()
            if verification_deadline is not None and time.monotonic() >= verification_deadline:
                raise TimeoutError("Facebook verification did not complete before timeout. Please try again after completing the checkpoint manually.")
            time.sleep(1.0)
            continue

        if verification_logged and verification_deadline is not None and time.monotonic() >= verification_deadline:
            raise TimeoutError("Facebook verification did not complete before timeout. Please try again after completing the checkpoint manually.")

        if scraper.page_ready_for_collection(page, target_url):
            JOB.add_log("INFO", "Checking session cookies", "Verifying whether Facebook session cookies are present.")
            cookies_present = scraper.has_authenticated_session(context)
            if (verification_logged or login_submit_logged) and not cookies_present:
                JOB.add_log("WARN", "Waiting for verified session", "The page became visible, but Facebook session cookies are still missing. Waiting for verification or login completion to finish.")
                time.sleep(0.35)
                continue
            if cookies_present:
                scraper.save_storage_state(context, JOB.add_log)
            mark_page_ready(page, target_url, waiting_for_go=waiting_for_go)
            if verification_logged:
                JOB.add_log("SUCCESS", "Verification completed", "Facebook verification finished and the requested page is accessible.")
            JOB.add_log("SUCCESS", "Facebook page ready", "Facebook page/feed is visible in the existing browser session.")
            if cookies_present:
                JOB.add_log("SUCCESS", "Session cookies detected", "Facebook authentication cookies are present and the session can be reused.")
            else:
                JOB.add_log("INFO", "Public content ready", "The target page appears publicly accessible without a saved authenticated session.")
            if waiting_for_go:
                JOB.add_log("INFO", "Ready for GO signal", "Login completed. Click GO / START EXTRACTION to continue.")
            broadcast_dashboard_event("login_completed", {"message": "Facebook login completed. Page ready.", "url": current_page_url(page, target_url)})
            return

        login_required, login_reason = scraper.detect_login_gate(page)
        login_form_visible = scraper.wait_for_selector(page, scraper.LOGIN_FORM_SELECTOR, 250)

        if login_required:
            JOB.update(
                login_required=True,
                verification_required=False,
                page_ready=False,
                ready_to_scrape=False,
                browser_url=current_page_url(page, target_url),
            )
            returned_to_target = False
            if login_form_visible:
                if not login_form_logged:
                    JOB.add_log("INFO", "Login form detected", "Facebook login form is visible in the current browser tab.")
                    login_form_logged = True
                if login_submit_logged and not login_loop_logged:
                    JOB.add_log("WARN", "Login loop detected", "The Facebook login form reappeared after submission. The session is not established yet or Facebook requires extra verification.")
                    login_loop_logged = True
                login_page_opened = True
            elif not login_page_opened:
                try:
                    JOB.add_log("INFO", "Opening Facebook login form", "Navigating the current browser tab to Facebook's login form.")
                    open_login_form_in_same_tab(page, target_url)
                    sync_browser_url(page, target_url)
                    login_page_opened = True
                except Exception as exc:
                    JOB.add_log("WARN", "Login form navigation failed", f"{type(exc).__name__}: {exc}")
            time.sleep(0.3)
            continue

        if login_page_opened and login_form_logged and not login_form_visible and not login_submit_logged:
            JOB.add_log("INFO", "Login submitted", "The Facebook login form disappeared. Waiting for navigation, session cookies, and page readiness.")
            login_submit_logged = True

        if (
            not returned_to_target
            and "facebook.com" in current_url
            and "/login" not in current_url
            and "/checkpoint/" not in current_url
            and "recover" not in current_url
            and scraper.has_authenticated_session(context)
            and current_url.rstrip("/") != target_url.rstrip("/")
        ):
            try:
                JOB.add_log("INFO", "Login session detected", "Login gate cleared. Returning to the requested Facebook target in the same browser tab.")
                page.goto(target_url, wait_until="domcontentloaded", timeout=scraper.POST_GOTO_TIMEOUT)
                scraper.apply_local_page_preferences(page)
                sync_browser_url(page, target_url)
                returned_to_target = True
                continue
            except Exception as exc:
                JOB.add_log("WARN", "Target reload after login failed", f"{type(exc).__name__}: {exc}")

        if time.monotonic() >= login_deadline:
            raise TimeoutError("Facebook login was required, but the session was not completed before timeout.")

        time.sleep(0.3)


def wait_for_go_signal(page, target_url: str) -> None:
    mark_page_ready(page, target_url, waiting_for_go=True)
    JOB.add_log("INFO", "Ready for GO signal", "Page ready. Click GO / START EXTRACTION to begin Facebook scrolling and extraction.")

    while not JOB.should_go():
        if JOB.should_cancel():
            raise ScrapeCancelled("Cancelled while waiting for GO signal.")
        drain_control_commands(page)
        sync_browser_url(page, target_url)
        time.sleep(0.2)

    JOB.update(
        status="running",
        active_task="Starting Facebook collection",
        ready_to_scrape=False,
        login_required=False,
        verification_required=False,
    )
    JOB.add_log("SUCCESS", "GO signal received", "Starting Facebook scrolling and extraction.")
    JOB.add_log("INFO", "Starting extraction", "Beginning Facebook scroll/load collection and metric extraction.")


def ensure_login_ready(page, context, target_url: str, active_task: str, delay_reason: str = "") -> bool:
    login_required, reason = scraper.detect_login_gate(page)
    if not login_required:
        return False

    JOB.add_log("WARN", "Login required", reason or "Facebook login is required.")
    if delay_reason:
        JOB.add_log("WARN", "Collection delayed because login is required", delay_reason)
    wait_for_user_login(page, context, target_url, waiting_for_go=False)
    JOB.add_log("INFO", "Resuming after login", f"Facebook content is ready again. Continuing {active_task.lower()}.")
    return True


def collect_post_links_with_progress(page, config: WebScrapeConfig) -> list[str]:
    JOB.update(active_task="Loading Facebook posts", total_scroll_rounds=config.scroll_rounds)
    JOB.add_log("INFO", "Starting scroll", "Beginning Facebook scroll collection.")

    def progress_hook(scroll_round: int, total_rounds: int, posts_found: int) -> None:
        progress_value = 0 if scroll_round <= 0 else min(20, round(20 * scroll_round / max(total_rounds, 1)))
        JOB.update(
            current_scroll_round=scroll_round,
            total_scroll_rounds=total_rounds,
            posts_found=posts_found,
            progress=progress_value,
        )

    diagnostics: dict[str, Any] = {}
    links = scraper.collect_post_links(
        page,
        scroll_rounds=config.scroll_rounds,
        target_url=config.target_url,
        log_hook=JOB.add_log,
        progress_hook=progress_hook,
        cancel_check=JOB.should_cancel,
        diagnostics=diagnostics,
    )
    stop_reason = diagnostics.get("stopReason")
    if stop_reason:
        JOB.add_log("INFO", "Scroll stop reason", str(stop_reason))
    return links


def wait_until_page_ready_or_login_completed(page, context, target_url: str) -> None:
    saved_session_path = scraper.get_storage_state_path(require_exists=True)
    JOB.update(
        status="loading_session",
        active_task="Loading saved session",
        browser_session_created=True,
        page_ready=False,
        login_required=False,
        verification_required=False,
        ready_to_scrape=False,
        browser_url=target_url,
        current_post=target_url,
    )
    JOB.add_log("INFO", "Checking saved session", "Checking whether a Playwright storage_state file is available for Facebook.")
    if saved_session_path is not None:
        JOB.add_log("INFO", "Saved session found", str(saved_session_path))
    else:
        JOB.add_log("INFO", "Saved session missing", "No saved Facebook storage_state file was found. Manual login may be required.")
    JOB.add_log("INFO", "Browser opened", "Opened the Facebook browser session.")
    JOB.add_log("INFO", "Opened target", target_url)
    page.goto(target_url, wait_until="domcontentloaded", timeout=scraper.POST_GOTO_TIMEOUT)
    scraper.apply_local_page_preferences(page)
    sync_browser_url(page, target_url)
    JOB.add_log("INFO", "Checking login state", "Checking whether Facebook requires login before extraction.")

    if JOB.should_cancel():
        raise ScrapeCancelled("Cancelled while preparing Facebook target.")

    session_state = scraper.validate_session(page, context, target_url)
    if saved_session_path is not None:
        if session_state["state"] == "ready":
            JOB.add_log("SUCCESS", "Saved session valid", session_state["reason"])
        elif session_state["state"] == "verification_required":
            JOB.add_log("WARN", "Saved session expired", "Stored Facebook session requires verification before it can be reused.")
        elif session_state["state"] == "login_required":
            JOB.add_log("WARN", "Saved session expired", "Stored Facebook session no longer bypasses the login wall.")
        else:
            JOB.add_log("WARN", "Saved session expired", "Stored Facebook session could not be validated yet.")

    if session_state["state"] == "verification_required":
        JOB.add_log("WARN", "Verification required", session_state["reason"] or "Facebook verification is required.")
        wait_for_user_login(page, context, target_url, waiting_for_go=True)
        return

    if session_state["state"] == "login_required":
        JOB.add_log("WARN", "Login required", session_state["reason"] or "Facebook login is required.")
        if scraper.auto_login_if_needed(page, context, target_url, log_hook=JOB.add_log):
            mark_page_ready(page, target_url, waiting_for_go=True)
            JOB.add_log("SUCCESS", "Saved session valid", "Facebook login completed and storage state is ready to reuse.")
            JOB.add_log("SUCCESS", "Facebook page ready", "Target Facebook content is visible.")
            JOB.add_log("INFO", "Ready for GO signal", "Click GO / START EXTRACTION to continue.")
            return
        wait_for_user_login(page, context, target_url, waiting_for_go=True)
        return

    if session_state["state"] == "ready":
        mark_page_ready(page, target_url, waiting_for_go=True)
        if session_state.get("cookiesPresent"):
            JOB.add_log("SUCCESS", "Session cookies detected", "Facebook authentication cookies are present and the session can be reused.")
        else:
            JOB.add_log("INFO", "Public content ready", "The target Facebook page appears accessible without an authenticated session.")
        JOB.add_log("SUCCESS", "Facebook page ready", "Facebook page/feed is visible and ready.")
        JOB.add_log("INFO", "Ready for GO signal", "Click GO / START EXTRACTION to continue.")
        return

    if scraper.auto_login_if_needed(page, context, target_url, log_hook=JOB.add_log):
        mark_page_ready(page, target_url, waiting_for_go=True)
        JOB.add_log("SUCCESS", "Login completed", "Saved Facebook session restored successfully.")
        JOB.add_log("SUCCESS", "Facebook page ready", "Target Facebook content is visible.")
        JOB.add_log("INFO", "Ready for GO signal", "Click GO / START EXTRACTION to continue.")
        return

    JOB.update(status="loading_session", active_task="Waiting for Facebook page")
    JOB.add_log("INFO", "Waiting for page", "Waiting for the Facebook page/feed to load.")
    deadline = time.monotonic() + (PAGE_READY_TIMEOUT / 1000)
    while time.monotonic() < deadline:
        if JOB.should_cancel():
            raise ScrapeCancelled("Cancelled while waiting for Facebook page readiness.")

        drain_control_commands(page)
        sync_browser_url(page, target_url)
        session_state = scraper.validate_session(page, context, target_url)
        if session_state["state"] == "ready":
            mark_page_ready(page, target_url, waiting_for_go=True)
            if session_state.get("cookiesPresent"):
                JOB.add_log("SUCCESS", "Session cookies detected", "Facebook authentication cookies are present and the session can be reused.")
            else:
                JOB.add_log("INFO", "Public content ready", "The target Facebook page appears publicly accessible.")
            JOB.add_log("SUCCESS", "Facebook page ready", "Facebook page/feed is visible and ready.")
            JOB.add_log("INFO", "Ready for GO signal", "Click GO / START EXTRACTION to continue.")
            return
        if session_state["state"] == "verification_required":
            JOB.add_log("WARN", "Verification required", session_state["reason"] or "Facebook verification is required.")
            wait_for_user_login(page, context, target_url, waiting_for_go=True)
            return
        if session_state["state"] == "login_required":
            JOB.add_log("WARN", "Login required", session_state["reason"] or "Facebook login is required.")
            wait_for_user_login(page, context, target_url, waiting_for_go=True)
            return
        time.sleep(0.25)

    raise TimeoutError("Facebook page/feed did not become ready before timeout.")


def run_scrape_job(config: WebScrapeConfig) -> None:
    CONTROL_BUS.reset()
    JOB.update(
        status="preparing",
        active_task="Creating browser session",
        output_file=config.output_file,
        config_summary=config_to_summary(config),
        browser_session_created=False,
        page_ready=False,
        login_required=False,
        verification_required=False,
        ready_to_scrape=False,
        browser_url=config.target_url,
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
            f"collectionType={config.collection_type}"
        ),
    )
    JOB.add_log(
        "INFO",
        "Selected collection type",
        "Posts with visible comments" if config.collection_type == "posts_with_comments" else "Posts only",
    )

    browser = None
    context = None
    page = None
    resource_blocking_enabled = False
    try:
        with sync_playwright() as p:
            browser, context = scraper.launch_browser(p)
            existing_pages = []
            try:
                existing_pages = [candidate for candidate in context.pages if not candidate.is_closed()]
            except Exception:
                existing_pages = []

            if existing_pages:
                page = existing_pages[0]
                for extra_page in existing_pages[1:]:
                    try:
                        extra_page.close()
                    except Exception:
                        pass
                JOB.add_log("INFO", "Reusing existing browser tab", "Using the primary tab from the current Facebook browser session.")
            else:
                page = context.new_page()
            JOB.update(browser_session_created=True, browser_url=current_page_url(page, config.target_url))
            JOB.add_log("INFO", "Browser session created", "Created one Playwright browser/context/page for the Facebook job.")
            saved_session_path = scraper.get_storage_state_path(require_exists=True)
            JOB.add_log("INFO", "Checking saved session", "Looking for a Facebook Playwright storage_state file before continuing.")
            if saved_session_path is not None:
                JOB.add_log("INFO", "Saved session found", str(saved_session_path))
            else:
                JOB.add_log("INFO", "Saved session missing", "No saved Facebook storage_state file is available yet.")
            profile_path = scraper.get_user_data_dir()
            if profile_path is not None:
                JOB.add_log("INFO", "Using persistent browser profile", str(profile_path))
            JOB.add_log("INFO", "Browser engine", scraper.browser_engine_label())
            if using_local_browser_window():
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                JOB.add_log(
                    "INFO",
                    "Browser opened",
                    "A real Chromium browser window has been opened. Log in there if Facebook asks, then return here and click GO / START EXTRACTION.",
                )
            else:
                JOB.add_log(
                    "INFO",
                    "Browser opened",
                    "Headless Facebook browser session started. This environment cannot open a local browser window.",
                )

            wait_until_page_ready_or_login_completed(page, context, config.target_url)
            wait_for_go_signal(page, config.target_url)
            context.route("**/*", scraper.route_nonessential_resources)
            resource_blocking_enabled = True
            JOB.add_log(
                "INFO",
                "Extraction optimization enabled",
                "Full rendering stayed on for Facebook login and verification. Non-essential resources are now blocked for extraction.",
            )

            link_collection_started = time.perf_counter()
            links = collect_post_links_with_progress(page, config)
            link_collection_elapsed = time.perf_counter() - link_collection_started
            JOB.update(posts_found=len(links), active_task="Extracting Facebook post data")
            JOB.add_log("SUCCESS", "Collected links", f"Found {len(links)} unique Facebook links in {link_collection_elapsed:.2f}s.")

            all_posts: list[scraper.PostData] = []
            oldest_post_seen: Optional[datetime] = None
            newest_post_seen: Optional[datetime] = None
            seen_in_range_post = False

            for index, link in enumerate(links, start=1):
                if JOB.should_cancel():
                    raise ScrapeCancelled("Cancelled during Facebook data extraction.")

                drain_control_commands(page)
                JOB.update(
                    active_task="Extracting Facebook post data",
                    current_post=link,
                    browser_url=link,
                    posts_checked=index - 1,
                    posts_processed=index - 1,
                    progress=20 + round(70 * (index - 1) / max(len(links), 1)),
                )
                JOB.add_log("INFO", f"Processing {index}/{len(links)}", link)

                try:
                    started = time.perf_counter()
                    raw_date, date_obj, post_type, scope_snapshot = scraper.open_post_for_extraction(
                        page,
                        link,
                        log_hook=JOB.add_log,
                    )
                    detected_date_text = date_obj.strftime(scraper.DATE_INPUT_FORMAT) if date_obj else (raw_date or "Cannot detect")
                    JOB.add_log("INFO", "Post date detected", f"{link} -> {detected_date_text}")

                    if date_obj is not None:
                        if oldest_post_seen is None or date_obj < oldest_post_seen:
                            oldest_post_seen = date_obj
                            JOB.add_log("INFO", "Oldest post so far", oldest_post_seen.strftime(scraper.DATE_INPUT_FORMAT))
                        if newest_post_seen is None or date_obj > newest_post_seen:
                            newest_post_seen = date_obj
                            JOB.add_log("INFO", "Newest post so far", newest_post_seen.strftime(scraper.DATE_INPUT_FORMAT))

                    coverage_status, coverage_reason = scraper.classify_post_date_coverage(
                        date_obj,
                        config.start_date,
                        config.end_date,
                    )

                    if coverage_status == "newer_than_end":
                        snapshot = JOB.snapshot()
                        JOB.update(posts_skipped_newer=snapshot["postsSkippedNewer"] + 1)
                        JOB.add_log("INFO", "Post skipped", coverage_reason)
                        JOB.update(posts_checked=index, posts_processed=index, progress=20 + round(70 * index / max(len(links), 1)))
                        time.sleep(scraper.BASE_POST_DELAY)
                        continue

                    if coverage_status == "older_than_start":
                        snapshot = JOB.snapshot()
                        JOB.update(posts_skipped_older=snapshot["postsSkippedOlder"] + 1)
                        JOB.add_log("INFO", "Post skipped", coverage_reason)
                        JOB.update(posts_checked=index, posts_processed=index, progress=20 + round(70 * index / max(len(links), 1)))
                        if seen_in_range_post:
                            JOB.add_log("INFO", "Stopped because older than start date reached", "Collected links are processed newest to oldest, so the target range has been passed.")
                            break
                        time.sleep(scraper.BASE_POST_DELAY)
                        continue

                    if coverage_status == "unknown_date":
                        snapshot = JOB.snapshot()
                        JOB.update(posts_skipped_unknown=snapshot["postsSkippedUnknown"] + 1)
                        JOB.add_log("WARN", "Post skipped", coverage_reason)
                        JOB.update(posts_checked=index, posts_processed=index, progress=20 + round(70 * index / max(len(links), 1)))
                        time.sleep(scraper.BASE_POST_DELAY)
                        continue

                    seen_in_range_post = True
                    snapshot = JOB.snapshot()
                    JOB.update(posts_in_range=snapshot["postsInRange"] + 1)
                    post = scraper.extract_metrics_from_loaded_post(
                        page,
                        link,
                        raw_date,
                        date_obj,
                        post_type,
                        config.collection_type,
                        log_hook=JOB.add_log,
                        scope_snapshot=scope_snapshot,
                    )
                    all_posts.append(post)
                    snapshot = JOB.snapshot()
                    JOB.update(posts_success=snapshot["postsSuccess"] + 1)
                    JOB.add_log("INFO", "Post included", coverage_reason)
                    JOB.add_log(
                        "SUCCESS",
                        "Extracted data",
                        (
                            f"Reactions: {post.reactions if post.reactions is not None else 'N/A'}, "
                            f"Comments: {post.comments_count if post.comments_count is not None else 'N/A'}, "
                            f"Shares: {post.shares if post.shares is not None else 'N/A'}, "
                            f"Date: {scraper.format_post_date(post)}"
                        ),
                    )
                    elapsed = time.perf_counter() - started
                    if elapsed >= scraper.SLOW_POST_SECONDS:
                        JOB.add_log("WARN", "Slow extraction", f"{link} took {elapsed:.2f}s.")
                except Exception as exc:
                    snapshot = JOB.snapshot()
                    JOB.update(
                        failed_extractions=snapshot["failedExtractions"] + 1,
                        errors=snapshot["errors"] + 1,
                    )
                    JOB.add_log("WARN", "Extraction failed", f"{link} ({type(exc).__name__}: {exc})")

                JOB.update(posts_checked=index, posts_processed=index, progress=20 + round(70 * index / max(len(links), 1)))
                time.sleep(scraper.BASE_POST_DELAY)

            if JOB.should_cancel():
                raise ScrapeCancelled("Cancelled before saving Facebook Excel output.")

            if oldest_post_seen is not None or newest_post_seen is not None:
                JOB.add_log(
                    "INFO",
                    "Collection date span",
                    (
                        f"Newest collected post: {newest_post_seen.strftime(scraper.DATE_INPUT_FORMAT) if newest_post_seen else 'Unknown'} | "
                        f"Oldest collected post: {oldest_post_seen.strftime(scraper.DATE_INPUT_FORMAT) if oldest_post_seen else 'Unknown'}"
                    ),
                )

            coverage_label = scraper.format_date_coverage(config.start_date, config.end_date)
            JOB.update(active_task="Saving Excel file", progress=95)
            if all_posts:
                scraper.save_facebook_excel(all_posts, config.output_file, coverage_label, config.collection_type)
            else:
                reason = "No Facebook posts matched the selected date coverage after validating visible post dates."
                scraper.save_empty_result_excel(
                    config.output_file,
                    coverage_label,
                    total_links_collected=len(links),
                    oldest_detected=oldest_post_seen,
                    newest_detected=newest_post_seen,
                    reason=reason,
                )
                JOB.add_log("WARN", "No matching posts", reason)

            JOB.add_log("SUCCESS", "Excel saved", config.output_file)
            JOB.update(status="completed", active_task="Completed", progress=100, finished_at=time.time(), ready_to_scrape=False)
            broadcast_dashboard_event("job_completed", JOB.snapshot(include_logs=False))
    except ScrapeCancelled as exc:
        JOB.update(status="cancelled", active_task="Cancelled", finished_at=time.time(), ready_to_scrape=False)
        JOB.add_log("WARN", "Job cancelled", str(exc))
    except Exception as exc:
        snapshot = JOB.snapshot()
        JOB.update(status="failed", active_task="Failed", errors=snapshot["errors"] + 1, finished_at=time.time(), ready_to_scrape=False)
        JOB.add_log("WARN", "Job failed", f"{type(exc).__name__}: {exc}")
    finally:
        if resource_blocking_enabled and context is not None:
            try:
                context.unroute("**/*", scraper.route_nonessential_resources)
            except Exception:
                pass
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        JOB.update(
            browser_session_created=False,
            page_ready=False,
            login_required=False,
            verification_required=False,
            ready_to_scrape=False,
            browser_url="",
        )
        broadcast_job_snapshot(include_logs=False)


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

            CONTROL_BUS.push(message)
            if action == "focus_browser":
                JOB.add_log("INFO", "Control received", "Browser focus requested from the dashboard.")
    except Exception:
        pass
    finally:
        DASHBOARD_HUB.unregister(client)


@app.route("/")
def home():
    return render_template("fb.html", stats=empty_stats(), features=dashboard_features(), logs=[])


@app.get("/assets/<path:filename>")
def asset_file(filename: str):
    allowed_files = {"icons8-about-us.svg", "icons8-expand-50.png"}
    if filename not in allowed_files:
        return jsonify({"ok": False, "errors": ["Asset not allowed."]}), 404
    return send_file(Path(filename).resolve())


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
        JOB.add_log("WARN", "Prevented duplicate browser launch", "A Facebook job is already active, so a second browser session was not created.")
        return jsonify({"ok": False, "errors": ["A Facebook extraction job is already running."]}), 409

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
        return jsonify({"ok": False, "errors": ["No active Facebook extraction job to cancel."], "status": JOB.snapshot()}), 409
    JOB.add_log("WARN", "Cancellation requested", "The Facebook extractor will stop at the next safe checkpoint.")
    return jsonify({"ok": True, "status": JOB.snapshot()})


@app.post("/api/go")
def go_signal():
    snapshot = JOB.snapshot()
    if not snapshot.get("browserSessionCreated"):
        JOB.add_log("WARN", "Blocked GO", "GO was rejected because no browser session is active.")
        return jsonify({"ok": False, "errors": ["No browser session is active. Click Run / Start first."], "status": snapshot}), 409
    if snapshot.get("status") in {"waiting_login", "waiting_verification"} or snapshot.get("verificationRequired"):
        JOB.add_log("WARN", "Blocked GO", "GO was rejected because Facebook verification is still required.")
        return jsonify({"ok": False, "errors": ["Complete Facebook verification first."], "status": snapshot}), 409
    if snapshot.get("loginRequired") or not snapshot.get("pageReady"):
        JOB.add_log("WARN", "Blocked GO", "GO was rejected because Facebook login/page readiness is not complete yet.")
        return jsonify({"ok": False, "errors": ["Please finish Facebook login first."], "status": snapshot}), 409
    if snapshot.get("status") != "ready":
        JOB.add_log("WARN", "Blocked GO", f"GO was rejected because the job state is {snapshot.get('status')}.")
        return jsonify({"ok": False, "errors": ["The Facebook extractor is not ready for GO yet."], "status": snapshot}), 409
    if not JOB.request_go():
        return jsonify({"ok": False, "errors": ["The Facebook extractor is not waiting for GO."], "status": JOB.snapshot()}), 409

    JOB.add_log("INFO", "GO signal received", "Reusing the existing browser session and starting Facebook extraction.")
    return jsonify({"ok": True, "status": JOB.snapshot()})


@app.post("/api/focus-browser")
def focus_browser():
    snapshot = JOB.snapshot()
    if snapshot["status"] not in {"preparing", "loading_session", "waiting_login", "waiting_verification", "ready", "running"}:
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
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
