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
        self.posts_processed = 0
        self.posts_success = 0
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
            success_rate = round(100 * self.posts_success / self.posts_processed) if self.posts_processed else 0
            health = max(0, 100 - min(self.errors * 8, 70))

            return {
                "status": self.status,
                "activeTask": self.active_task,
                "currentPost": self.current_post,
                "currentScrollRound": self.current_scroll_round,
                "totalScrollRounds": self.total_scroll_rounds,
                "postsFound": self.posts_found,
                "postsProcessed": self.posts_processed,
                "postsSuccess": self.posts_success,
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

    latest_mode = bool(payload.get("latestMode", True))
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
        "dateCoverage": scraper.format_date_coverage(config.start_date, config.end_date),
        "outputFile": config.output_file,
    }


def collect_post_links_with_progress(page, config: WebScrapeConfig) -> list[str]:
    links: dict[str, bool] = {}
    stagnant = 0
    link_locator = page.locator("a[href*='/p/'], a[href*='/reel/']")

    scraper.wait_for_profile_ready(page)
    JOB.update(active_task="Collecting post links", total_scroll_rounds=config.scroll_rounds)
    JOB.add_log("INFO", "Profile ready", "Starting profile grid scan.")

    for scroll_round in range(1, config.scroll_rounds + 1):
        if JOB.should_cancel():
            raise ScrapeCancelled("Cancelled during profile scrolling.")

        JOB.update(current_scroll_round=scroll_round, active_task="Collecting post links")

        try:
            link_locator.first.wait_for(timeout=scraper.PROFILE_LINK_WAIT_TIMEOUT)
            found = link_locator.evaluate_all("els => els.map(a => a.href)")
        except Exception as exc:
            JOB.update(errors=JOB.snapshot()["errors"] + 1)
            JOB.add_log("WARN", f"Scroll round {scroll_round} failed", type(exc).__name__)
            page.wait_for_timeout(scraper.PROFILE_RETRY_MS)
            continue

        before = len(links)
        for href in found:
            if href:
                clean_url = href.split("?")[0]
                if clean_url not in links:
                    links[clean_url] = True

        new_count = len(links) - before
        JOB.update(posts_found=len(links), progress=min(20, round(20 * scroll_round / config.scroll_rounds)))

        if new_count:
            stagnant = 0
            JOB.add_log("INFO", f"Scroll round {scroll_round}", f"+{new_count} links, total {len(links)}.")
        else:
            stagnant += 1
            JOB.add_log("INFO", f"Scroll round {scroll_round}", f"No new links ({stagnant}/{scraper.MAX_STAGNANT_ROUNDS}).")

        if stagnant >= scraper.MAX_STAGNANT_ROUNDS:
            JOB.add_log("INFO", "Link collection stopped", "No new links after repeated scrolls.")
            break

        prev_count = len(links)
        page.mouse.wheel(0, 4000)
        try:
            page.wait_for_function(
                """(prev) => {
                    const anchors = Array.from(document.querySelectorAll("a[href*='/p/'], a[href*='/reel/']"));
                    const unique = new Set(anchors.map(a => a.href.split("?")[0]));
                    return unique.size > prev;
                }""",
                arg=prev_count,
                timeout=scraper.SCROLL_WAIT_TIMEOUT,
            )
        except Exception:
            page.wait_for_timeout(scraper.SCROLL_FALLBACK_MS)

    return list(links.keys())


def wait_for_profile_after_login(page) -> None:
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

    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        if JOB.should_cancel():
            raise ScrapeCancelled("Cancelled while waiting for Instagram login/profile.")

        try:
            page.locator("a[href*='/p/'], a[href*='/reel/']").first.wait_for(timeout=2000)
            break
        except Exception:
            continue
    else:
        raise TimeoutError(
            "Instagram profile grid did not become visible. The profile may require login, "
            "Instagram may be blocking the cloud server, or the page loaded too slowly."
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

    browser = None
    context = None
    try:
        with sync_playwright() as p:
            browser, context = scraper.launch_browser(p)
            context.route("**/*", scraper.route_nonessential_resources)

            page = context.new_page()
            page.goto(config.profile_url, wait_until="domcontentloaded", timeout=scraper.POST_GOTO_TIMEOUT)
            JOB.add_log("INFO", "Browser opened", "Cloud-safe headless Playwright context started.")
            if scraper.PLAYWRIGHT_STORAGE_STATE:
                JOB.add_log("INFO", "Storage state loaded", scraper.PLAYWRIGHT_STORAGE_STATE)
            wait_for_profile_after_login(page)

            links = collect_post_links_with_progress(page, config)
            JOB.update(posts_found=len(links), active_task="Extracting post data")
            JOB.add_log("SUCCESS", "Link collection complete", f"Found {len(links)} unique post links.")

            all_posts = []
            total_links = len(links)
            for index, link in enumerate(links, start=1):
                if JOB.should_cancel():
                    raise ScrapeCancelled("Cancelled during post extraction.")

                JOB.update(
                    active_task="Extracting post data",
                    current_post=link,
                    posts_processed=index - 1,
                    progress=20 + round(70 * (index - 1) / max(total_links, 1)),
                )
                JOB.add_log("INFO", f"Processing {index}/{total_links}", link)

                try:
                    post = scraper.extract_post_data(page, link)
                    all_posts.append(post)
                    success = post.likes is not None or post.comments is not None
                    if success:
                        snapshot = JOB.snapshot()
                        JOB.update(posts_success=snapshot["postsSuccess"] + 1)
                        JOB.add_log(
                            "SUCCESS",
                            "Extracted post",
                            f"Likes: {post.likes}, Comments: {post.comments}, Shares: {post.shares}, Date: {scraper.format_post_date(post)}",
                        )
                    else:
                        snapshot = JOB.snapshot()
                        JOB.update(errors=snapshot["errors"] + 1)
                        JOB.add_log("WARN", "Metrics incomplete", link)
                except Exception as exc:
                    snapshot = JOB.snapshot()
                    JOB.update(errors=snapshot["errors"] + 1)
                    JOB.add_log("WARN", "Post extraction failed", f"{link} ({type(exc).__name__})")

                JOB.update(posts_processed=index, progress=20 + round(70 * index / max(total_links, 1)))
                time.sleep(scraper.BASE_POST_DELAY)

            if JOB.should_cancel():
                raise ScrapeCancelled("Cancelled before saving Excel output.")

            filtered_posts = [
                post for post in all_posts if scraper.post_matches_date_coverage(post, config.start_date, config.end_date)
            ]
            removed_count = len(all_posts) - len(filtered_posts)
            if removed_count:
                JOB.add_log("INFO", "Date filter applied", f"Filtered out {removed_count} posts outside selected coverage.")

            JOB.update(active_task="Saving Excel file", progress=95)
            scraper.save_grouped_excel(
                filtered_posts,
                config.output_file,
                scraper.format_date_coverage(config.start_date, config.end_date),
            )
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
