from __future__ import annotations

import threading
import time
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request, send_file
from playwright.sync_api import sync_playwright

import instagram_to_excel as scraper


app = Flask(__name__)


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
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None

    def add_log(self, level: str, action: str, details: str = "") -> None:
        with self.lock:
            self.logs.insert(
                0,
                {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "level": level.upper(),
                    "action": action,
                    "details": details,
                },
            )
            self.logs = self.logs[:250]

    def update(self, **kwargs: Any) -> None:
        with self.lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def request_cancel(self) -> bool:
        with self.lock:
            if self.status not in {"running", "stopping"}:
                return False

            self.cancel_requested = True
            self.status = "stopping"
            self.active_task = "Stopping scrape job"
            return True

    def should_cancel(self) -> bool:
        with self.lock:
            return self.cancel_requested

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            eligible_total = self.posts_in_range if self.posts_in_range > 0 else (self.posts_success + self.failed_extractions)
            if eligible_total > 0:
                success_rate = round(100 * self.posts_success / eligible_total)
            elif self.posts_checked > 0 and self.failed_extractions == 0:
                success_rate = 100
            else:
                success_rate = 0
            health = max(0, 100 - min(self.errors * 8, 70))

            return {
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
                "logs": list(self.logs),
                "cancelRequested": self.cancel_requested,
                "canDownload": self.status == "completed" and bool(self.output_file) and Path(self.output_file).exists(),
            }


JOB = ScrapeJobState()
JOB_THREAD: Optional[threading.Thread] = None
LOGIN_READY_TIMEOUT = 180000
HEADLESS_PROFILE_READY_TIMEOUT = 45000


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
            "description": "The backend uses the working visible action-row extraction logic for likes, comments, shares, and reposts.",
            "icon": "MET",
        },
        {
            "title": "Live Activity Logs",
            "description": "The dashboard polls backend job state for scroll progress, current post, warnings, and completion status.",
            "icon": "LOG",
        },
        {
            "title": "Excel Export",
            "description": "Results are saved to the confirmed .xlsx filename with stable row pairing per post.",
            "icon": "XLS",
        },
    ]


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

    profile_url = scraper.normalize_instagram_profile_url(str(payload.get("instagramLink", "")))
    if profile_url is None:
        errors.append("Enter a valid Instagram profile link, for example https://www.instagram.com/username/.")

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
        return None, errors, overwrite_required

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


def collect_post_links_with_progress(page, config: WebScrapeConfig) -> list[str]:
    JOB.update(active_task="Collecting post links", total_scroll_rounds=config.scroll_rounds)
    JOB.add_log("INFO", "Profile ready", "Starting profile grid scan.")

    def progress_hook(scroll_round: int, total_rounds: int, posts_found: int) -> None:
        progress_value = 0 if scroll_round <= 0 else min(20, round(20 * scroll_round / max(total_rounds, 1)))
        JOB.update(
            current_scroll_round=scroll_round,
            total_scroll_rounds=total_rounds,
            posts_found=posts_found,
            progress=progress_value,
        )

    diagnostics: dict[str, Any] = {}
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


def wait_for_profile_after_login(page, context, profile_url: str) -> None:
    """Give the user time to complete Instagram login in the opened browser."""
    JOB.update(active_task="Waiting for Instagram profile")
    if scraper.PLAYWRIGHT_HEADLESS:
        timeout_ms = HEADLESS_PROFILE_READY_TIMEOUT
        JOB.add_log(
            "INFO",
            "Waiting for profile",
            "Headless cloud mode: waiting for a public profile grid. Manual login is not available.",
        )
    else:
        timeout_ms = LOGIN_READY_TIMEOUT
        JOB.add_log("INFO", "Waiting for profile", "Log in in the opened browser if Instagram asks.")

    if JOB.should_cancel():
        raise ScrapeCancelled("Cancelled while waiting for Instagram login/profile.")

    scraper.prepare_profile_page(
        page,
        context,
        profile_url,
        timeout_ms=timeout_ms,
        log_hook=JOB.add_log,
    )
    JOB.add_log("SUCCESS", "Profile detected", "Post grid is visible; scraping can continue.")


def run_scrape_job(config: WebScrapeConfig) -> None:
    JOB.update(
        status="running",
        active_task="Opening browser",
        output_file=config.output_file,
        config_summary=config_to_summary(config),
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
    try:
        with sync_playwright() as p:
            browser, context = scraper.launch_browser(p)
            context.route("**/*", scraper.route_nonessential_resources)

            page = context.new_page()
            JOB.add_log("INFO", "Browser opened", "Cloud-safe headless Playwright context started.")
            if scraper.PLAYWRIGHT_STORAGE_STATE:
                JOB.add_log("INFO", "Storage state loaded", scraper.PLAYWRIGHT_STORAGE_STATE)
            wait_for_profile_after_login(page, context, config.profile_url)

            link_collection_started = time.perf_counter()
            links = collect_post_links_with_progress(page, config)
            link_collection_elapsed = time.perf_counter() - link_collection_started
            JOB.update(posts_found=len(links), active_task="Extracting post data")
            JOB.add_log(
                "SUCCESS",
                "Link collection complete",
                f"Found {len(links)} unique post links in {link_collection_elapsed:.2f}s.",
            )

            all_posts = []
            total_links = len(links)
            oldest_post_seen: Optional[datetime] = None
            newest_post_seen: Optional[datetime] = None
            target_date_reached = False
            seen_in_range_post = False
            for index, link in enumerate(links, start=1):
                if JOB.should_cancel():
                    raise ScrapeCancelled("Cancelled during post extraction.")

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
                        needs_cooldown = True
                        JOB.update(posts_checked=index, posts_processed=index, progress=20 + round(70 * index / max(total_links, 1)))
                        time.sleep(scraper.BASE_POST_DELAY)
                        continue

                    if coverage_status == "older_than_start":
                        snapshot = JOB.snapshot()
                        JOB.update(posts_skipped_older=snapshot["postsSkippedOlder"] + 1)
                        JOB.add_log("INFO", "Post skipped", coverage_reason)
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
                        needs_cooldown = True
                        JOB.update(posts_checked=index, posts_processed=index, progress=20 + round(70 * index / max(total_links, 1)))
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
                        log_hook=JOB.add_log,
                    )
                    post_elapsed = time.perf_counter() - post_started
                    all_posts.append(post)

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
                    else:
                        snapshot = JOB.snapshot()
                        JOB.update(failed_extractions=snapshot["failedExtractions"] + 1)
                        JOB.update(errors=snapshot["errors"] + 1)
                        JOB.add_log("WARN", "Metrics incomplete", link)
                        needs_cooldown = True
                    if post_elapsed >= scraper.SLOW_POST_SECONDS:
                        JOB.add_log("WARN", "Slow extraction", f"{link} took {post_elapsed:.2f}s.")
                except Exception as exc:
                    snapshot = JOB.snapshot()
                    JOB.update(failed_extractions=snapshot["failedExtractions"] + 1)
                    JOB.update(errors=snapshot["errors"] + 1)
                    JOB.add_log("WARN", "Post extraction failed", f"{link} ({type(exc).__name__})")
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

            JOB.update(active_task="Saving Excel file", progress=95)
            coverage_label = scraper.format_date_coverage(config.start_date, config.end_date)
            if filtered_posts:
                scraper.save_grouped_excel(
                    filtered_posts,
                    config.output_file,
                    coverage_label,
                )
            else:
                empty_reason = (
                    "No collected posts fell within the selected date coverage after validating post dates."
                )
                scraper.save_empty_result_excel(
                    config.output_file,
                    coverage_label,
                    total_links_collected=len(links),
                    oldest_detected=oldest_post_seen,
                    newest_detected=newest_post_seen,
                    reason=empty_reason,
                )
                JOB.add_log("WARN", "No posts matched range", empty_reason)
            JOB.add_log("SUCCESS", "Excel saved", config.output_file)
            JOB.update(status="completed", active_task="Completed", progress=100, finished_at=time.time())
    except ScrapeCancelled as exc:
        JOB.update(status="stopped", active_task="Stopped", finished_at=time.time())
        JOB.add_log("WARN", "Scrape cancelled", str(exc))
    except Exception as exc:
        snapshot = JOB.snapshot()
        JOB.update(status="failed", active_task="Failed", errors=snapshot["errors"] + 1, finished_at=time.time())
        JOB.add_log("WARN", "Scrape failed", f"{type(exc).__name__}: {exc}")
    finally:
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


@app.route("/")
def home():
    return render_template("index.html", stats=empty_stats(), features=dashboard_features(), logs=[])


@app.get("/assets/<path:filename>")
def asset_file(filename: str):
    allowed_files = {"icons8-about-us.svg", "icons8-expand-50.png"}
    if filename not in allowed_files:
        return jsonify({"ok": False, "errors": ["Asset not allowed."]}), 404

    return send_file(Path(__file__).with_name(filename))


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
        return jsonify({"ok": False, "errors": ["A scraping job is already running."]}), 409

    JOB.reset()
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
    return jsonify({"ok": True, "status": JOB.snapshot()})


@app.post("/api/cancel")
def cancel_scrape():
    if not JOB.request_cancel():
        return jsonify({"ok": False, "errors": ["No active scraping job to cancel."], "status": JOB.snapshot()}), 409

    JOB.add_log("WARN", "Cancellation requested", "The scraper will stop at the next safe checkpoint.")
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
