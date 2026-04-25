from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from openpyxl import Workbook
from playwright.sync_api import Error as PlaywrightError


DATE_INPUT_FORMAT = "%Y-%m-%d"
VALID_FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "fb.com", "www.fb.com"}
INVALID_FILENAME_CHARS = set('<>:"/\\|?*')
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
VERIFICATION_URL_TOKENS = [
    "/checkpoint/",
    "/checkpoint",
    "/login/identify",
    "/two_step_verification",
    "/authentication/",
    "/authentication",
    "/authentication?",
    "/captcha/",
    "recaptcha",
]

PLAYWRIGHT_STORAGE_STATE = os.getenv("PLAYWRIGHT_STORAGE_STATE", "").strip() or None
DEFAULT_STORAGE_STATE_FILE = Path("storage_states/facebook_auth.json")
DEFAULT_USER_DATA_DIR = Path("storage_states/facebook_user_data")
PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").strip().lower() not in {"0", "false", "no", "off"}
PLAYWRIGHT_AUTO_INSTALL = os.getenv("PLAYWRIGHT_AUTO_INSTALL", "true").strip().lower() not in {"0", "false", "no", "off"}
RUNNING_ON_RENDER = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID") or os.getenv("RENDER_EXTERNAL_URL"))
HAS_LOCAL_DESKTOP = os.name == "nt" or bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))
PLAYWRIGHT_INTERACTIVE_BROWSER = os.getenv(
    "PLAYWRIGHT_INTERACTIVE_BROWSER",
    "true" if (HAS_LOCAL_DESKTOP and not RUNNING_ON_RENDER) else "false",
).strip().lower() in {"1", "true", "yes", "on"}
PLAYWRIGHT_BROWSER_CHANNEL = os.getenv(
    "PLAYWRIGHT_BROWSER_CHANNEL",
    "chrome" if (HAS_LOCAL_DESKTOP and not RUNNING_ON_RENDER) else "",
).strip().lower()
ACTIVE_BROWSER_ENGINE = "chromium"

# -----------------------------------------------------------------------------
# Facebook login credentials
# Prefer environment variables in production or shared environments:
#   FACEBOOK_USERNAME
#   FACEBOOK_PASSWORD
#
# PUT TEST ACCOUNT LOGIN DETAILS HERE only if you intentionally need a local
# development fallback. Keep them backend-only and never expose them to JS/HTML.
# -----------------------------------------------------------------------------
TEST_FACEBOOK_USERNAME = ""
TEST_FACEBOOK_PASSWORD = ""
FACEBOOK_USERNAME = os.getenv("FACEBOOK_USERNAME", "").strip() or TEST_FACEBOOK_USERNAME.strip()
FACEBOOK_PASSWORD = os.getenv("FACEBOOK_PASSWORD", "").strip() or TEST_FACEBOOK_PASSWORD.strip()

POST_GOTO_TIMEOUT = 35000
LOGIN_FORM_TIMEOUT = 12000
LOGIN_READY_TIMEOUT = 180000
LOGIN_POST_SUBMIT_TIMEOUT = 15000
PAGE_READY_TIMEOUT = 12000
SCROLL_WAIT_TIMEOUT = 1800
PROFILE_RETRY_MS = 800
BASE_POST_DELAY = 0.15
SLOW_SCROLL_SECONDS = 2.0
SLOW_POST_SECONDS = 4.0
COMMENT_LOAD_WAIT_MS = 650
COMMENT_EXPANSION_ROUNDS = 4

POST_LINK_SELECTOR = (
    "a[href*='/posts/'], "
    "a[href*='/videos/'], "
    "a[href*='/permalink/'], "
    "a[href*='story_fbid='], "
    "a[href*='/photo.php'], "
    "a[href*='/watch/?v=']"
)
FEED_READY_SELECTOR = "div[role='feed'], div[role='feed'] div[role='article']"
PAGE_READY_SELECTOR = f"{POST_LINK_SELECTOR}, {FEED_READY_SELECTOR}"
PAGE_SHELL_READY_SELECTOR = (
    "div[role='main'] h1, "
    "div[role='main'] div[role='tablist'], "
    "div[role='main'] a[href*='/about'], "
    "div[role='main'] a[href*='/followers'], "
    "div[role='main'] a[href*='/photos'], "
    "div[role='main'] a[href*='/videos']"
)
LOGIN_FORM_SELECTOR = "input[name='email'], input[name='pass'], form[action*='login']"

LogHook = Callable[[str, str, str], None]
ProgressHook = Callable[[int, int, int], None]


@dataclass
class CommentData:
    post_url: str
    commenter_name: str
    comment_text: str
    comment_date_raw: str = ""
    thread_type: str = "Comment"


@dataclass
class PostData:
    url: str
    post_type: str
    post_date_raw: str
    post_date_obj: Optional[datetime]
    reactions: Optional[int]
    comments_count: Optional[int]
    shares: Optional[int]
    notes: str = ""
    comments_preview: list[CommentData] = field(default_factory=list)


def emit_log(log_hook: Optional[LogHook], level: str, action: str, details: str = "") -> None:
    if log_hook is None:
        return
    try:
        log_hook(level, action, details)
    except Exception:
        pass


def emit_progress(progress_hook: Optional[ProgressHook], scroll_round: int, total_rounds: int, posts_found: int) -> None:
    if progress_hook is None:
        return
    try:
        progress_hook(scroll_round, total_rounds, posts_found)
    except Exception:
        pass


def uses_local_browser_window() -> bool:
    return PLAYWRIGHT_INTERACTIVE_BROWSER and HAS_LOCAL_DESKTOP and not RUNNING_ON_RENDER


def browser_runtime_mode() -> str:
    return "local_window" if uses_local_browser_window() else "headless_session"


def browser_mode_label() -> str:
    return "Opened Browser Window" if uses_local_browser_window() else "Headless Browser Session"


def browser_engine_label() -> str:
    engine = (ACTIVE_BROWSER_ENGINE or "chromium").strip().lower()
    if engine == "chrome":
        return "Google Chrome"
    if engine == "msedge":
        return "Microsoft Edge"
    return "Chromium"


def browser_mode_note() -> str:
    if uses_local_browser_window():
        return (
            f"A real {browser_engine_label()} window opens locally for Facebook login and verification "
            "using a persistent local browser profile. Keep that browser open, avoid refreshing the page, "
            "and click GO only after the target page is visible."
        )
    return "This environment cannot open a local Chromium window. Manual Facebook login requires a local run with PLAYWRIGHT_INTERACTIVE_BROWSER=true."


def preview_input_supported() -> bool:
    return False


def get_storage_state_path(require_exists: bool = False) -> Optional[Path]:
    if PLAYWRIGHT_STORAGE_STATE:
        path = Path(PLAYWRIGHT_STORAGE_STATE)
    elif uses_local_browser_window():
        path = DEFAULT_STORAGE_STATE_FILE
    else:
        return None
    if require_exists and not path.exists():
        return None
    return path


def has_saved_storage_state() -> bool:
    return get_storage_state_path(require_exists=True) is not None


def storage_state_label() -> str:
    path = get_storage_state_path()
    return str(path) if path is not None else ""


def get_user_data_dir() -> Optional[Path]:
    if not uses_local_browser_window():
        return None
    return DEFAULT_USER_DATA_DIR


def has_login_credentials() -> bool:
    return bool(FACEBOOK_USERNAME and FACEBOOK_PASSWORD)


def load_or_create_context(browser):
    context_options = {
        "viewport": {"width": 1440, "height": 960},
        "locale": "en-US",
    }
    storage_path = get_storage_state_path(require_exists=True)
    if storage_path is not None:
        context_options["storage_state"] = str(storage_path)
    context = browser.new_context(**context_options)
    return context, storage_path


def wait_for_selector(page, selector: str, timeout_ms: int) -> bool:
    try:
        page.locator(selector).first.wait_for(timeout=timeout_ms)
        return True
    except Exception:
        return False


def normalize_excel_filename(raw_value: str) -> Optional[str]:
    value = raw_value.strip()
    if not value:
        return None
    if not value.lower().endswith(".xlsx"):
        value = f"{value}.xlsx"
    if any(char in INVALID_FILENAME_CHARS for char in value):
        return None
    return value


def normalize_facebook_target_url(raw_value: str) -> Optional[str]:
    value = raw_value.strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = f"https://{value}"
    try:
        parsed = urlparse(value)
    except Exception:
        return None

    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or hostname not in VALID_FACEBOOK_HOSTS:
        return None

    if not parsed.path or parsed.path == "/":
        return None

    normalized_path = parsed.path.rstrip("/") or "/"
    return urlunparse(("https", "www.facebook.com", normalized_path, "", parsed.query, ""))


def format_date_coverage(start_date: datetime, end_date: Optional[datetime]) -> str:
    if end_date is None:
        return f"{start_date.strftime(DATE_INPUT_FORMAT)} to latest visible content"
    return f"{start_date.strftime(DATE_INPUT_FORMAT)} to {end_date.strftime(DATE_INPUT_FORMAT)}"


def parse_count(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    text = value.strip().upper().replace(",", "")
    match = re.match(r"^(\d+(?:\.\d+)?)([KMB]?)$", text)
    if match:
        number = float(match.group(1))
        suffix = match.group(2)
        if suffix == "K":
            number *= 1_000
        elif suffix == "M":
            number *= 1_000_000
        elif suffix == "B":
            number *= 1_000_000_000
        return int(number)

    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def get_target_page_slug(target_url: str) -> str:
    try:
        parsed = urlparse(target_url)
    except Exception:
        return ""
    path = (parsed.path or "").strip("/")
    if not path:
        return ""
    return path.split("/")[0].strip().lower()


def normalize_facebook_post_url(raw_value: str, target_url: str = "") -> Optional[str]:
    if not raw_value:
        return None

    try:
        parsed = urlparse(raw_value)
    except Exception:
        return None

    host = (parsed.hostname or "").lower()
    if host and host not in VALID_FACEBOOK_HOSTS:
        return None

    path = (parsed.path or "").rstrip("/")
    lower_path = path.lower()
    if not any(marker in lower_path for marker in ("/posts/", "/videos/", "/permalink/", "/watch/", "/photo.php")) and "story_fbid=" not in (parsed.query or "").lower():
        return None

    target_slug = get_target_page_slug(target_url)
    if target_slug and path.strip("/"):
        first_segment = path.strip("/").split("/")[0].strip().lower()
        slug_style = any(marker in lower_path for marker in ("/posts/", "/videos/", "/photos/", "/reels/", "/permalink/"))
        if slug_style and first_segment and first_segment not in {target_slug, "photo.php", "watch", "permalink.php"}:
            return None

    keep_query_keys = {"story_fbid", "id", "fbid", "set", "type", "theater", "v"}
    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        key_lower = key.lower()
        if key_lower in {"comment_id", "reply_comment_id", "notif_id", "notif_t", "ref", "__cft__", "__tn__"}:
            continue
        if key_lower in keep_query_keys:
            query_pairs.append((key, value))

    query = urlencode(query_pairs, doseq=True)
    normalized = parsed._replace(params="", query=query, fragment="")
    return urlunparse(normalized)


def dedupe_post_links(raw_links: list[str], target_url: str = "") -> list[str]:
    seen: dict[str, bool] = {}
    for raw_value in raw_links:
        normalized = normalize_facebook_post_url(raw_value, target_url=target_url)
        if not normalized or normalized in seen:
            continue
        seen[normalized] = True
    return list(seen.keys())


def parse_facebook_datetime(raw_value: str) -> Optional[datetime]:
    if not raw_value:
        return None

    text = raw_value.strip()
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    now = datetime.now()

    short_relative_match = re.fullmatch(r"(\d+)\s*([smhdw])", normalized)
    if short_relative_match:
        amount = int(short_relative_match.group(1))
        unit = short_relative_match.group(2)
        if unit == "s":
            return now - timedelta(seconds=amount)
        if unit == "m":
            return now - timedelta(minutes=amount)
        if unit == "h":
            return now - timedelta(hours=amount)
        if unit == "d":
            return now - timedelta(days=amount)
        if unit == "w":
            return now - timedelta(weeks=amount)

    relative_match = re.search(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", normalized)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        if unit == "second":
            return now - timedelta(seconds=amount)
        if unit == "minute":
            return now - timedelta(minutes=amount)
        if unit == "hour":
            return now - timedelta(hours=amount)
        if unit == "day":
            return now - timedelta(days=amount)
        if unit == "week":
            return now - timedelta(weeks=amount)
        if unit == "month":
            return now - timedelta(days=amount * 30)
        if unit == "year":
            return now - timedelta(days=amount * 365)

    if normalized.startswith("today"):
        return now

    yesterday_match = re.search(r"yesterday(?: at (.+))?$", normalized)
    if yesterday_match:
        base = now - timedelta(days=1)
        time_part = (yesterday_match.group(1) or "").strip()
        if time_part:
            for time_fmt in ("%I:%M %p", "%I %p"):
                try:
                    time_value = datetime.strptime(time_part.upper(), time_fmt)
                    return base.replace(hour=time_value.hour, minute=time_value.minute, second=0, microsecond=0)
                except ValueError:
                    continue
        return base

    formats = [
        "%A, %B %d, %Y at %I:%M %p",
        "%B %d, %Y at %I:%M %p",
        "%B %d at %I:%M %p",
        "%b %d, %Y at %I:%M %p",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d",
        "%b %d",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.year == 1900:
                parsed = parsed.replace(year=now.year)
            return parsed
        except ValueError:
            continue

    timestamp_match = re.search(r"\b(1\d{9,12})\b", text)
    if timestamp_match:
        try:
            raw_ts = int(timestamp_match.group(1))
            if raw_ts > 10_000_000_000:
                raw_ts = raw_ts / 1000
            return datetime.fromtimestamp(raw_ts)
        except Exception:
            return None

    return None


def profile_content_visible(page, timeout_ms: int = 500) -> bool:
    return wait_for_selector(page, PAGE_READY_SELECTOR, timeout_ms)


def page_shell_visible(page, timeout_ms: int = 500) -> bool:
    return wait_for_selector(page, PAGE_SHELL_READY_SELECTOR, timeout_ms)


def visible_post_anchor_count(page) -> int:
    try:
        return int(page.locator(POST_LINK_SELECTOR).count())
    except Exception:
        return 0


def has_authenticated_session(context) -> bool:
    try:
        cookies = context.cookies()
    except Exception:
        return False
    return any(cookie.get("name") == "c_user" for cookie in cookies)


def url_indicates_checkpoint_or_verification(url: str) -> bool:
    normalized = (url or "").lower()
    return any(token in normalized for token in VERIFICATION_URL_TOKENS)


def current_url_matches_target(current_url: str, target_url: str) -> bool:
    if not current_url or not target_url:
        return False
    try:
        current = urlparse(current_url)
        target = urlparse(target_url)
    except Exception:
        return False
    current_host = (current.hostname or "").lower()
    target_host = (target.hostname or "").lower()
    if current_host != target_host:
        return False
    current_path = (current.path or "/").rstrip("/") or "/"
    target_path = (target.path or "/").rstrip("/") or "/"
    return current_path == target_path


def detect_checkpoint_or_verification(page) -> tuple[bool, str]:
    current_url = ""
    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""

    if url_indicates_checkpoint_or_verification(current_url):
        return True, "Facebook checkpoint or verification page detected."

    try:
        body_text = page.locator("body").inner_text(timeout=600).lower()
    except Exception:
        body_text = ""

    challenge_phrases = [
        "confirm your identity",
        "check your notifications on another device",
        "enter the code we sent",
        "approve from another device",
        "security check",
        "verification required",
        "suspicious login attempt",
        "suspicious login",
        "checkpoint",
        "verify your account",
        "review recent login",
        "recaptcha",
        "captcha",
        "google's recaptcha enterprise",
        "protect the security of your account",
        "authentication required",
    ]
    if any(phrase in body_text for phrase in challenge_phrases):
        return True, "Facebook requires verification before the page can be used."

    return False, ""


def detect_login_gate(page) -> tuple[bool, str]:
    if wait_for_selector(page, LOGIN_FORM_SELECTOR, 350):
        return True, "Facebook login form detected."

    blocked, blocked_reason = detect_checkpoint_or_verification(page)
    if blocked:
        return True, blocked_reason

    current_url = ""
    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""

    if any(token in current_url for token in ["/login", "/checkpoint", "/recover"]):
        return True, "Facebook login or checkpoint page detected."

    try:
        body_text = page.locator("body").inner_text(timeout=600).lower()
    except Exception:
        body_text = ""

    blocking_phrases = [
        "log in to continue",
        "you must log in",
        "see more posts from",
        "continue on facebook",
        "create new account",
        "continue reading this story by logging in",
        "see more from",
        "log in to see more",
    ]
    if any(phrase in body_text for phrase in blocking_phrases):
        return True, "Facebook login wall detected."

    login_cta_visible = False
    try:
        login_cta_visible = page.locator(
            "a[href*='/login'], a[href*='login.php'], button[name='login'], form[action*='login'] button"
        ).count() > 0
    except Exception:
        login_cta_visible = False

    if login_cta_visible and ("log in" in body_text or "sign up" in body_text):
        return True, "Facebook requires login before deeper content can be collected."

    if ("log in" in body_text and "sign up" in body_text) and visible_post_anchor_count(page) < 5:
        return True, "Facebook public gate detected."

    return False, ""


def page_ready_for_collection(page, target_url: str = "") -> bool:
    login_required, _ = detect_login_gate(page)
    if login_required:
        return False
    if visible_post_anchor_count(page) > 0:
        return True
    if wait_for_selector(page, FEED_READY_SELECTOR, 800):
        return True
    current_url = ""
    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""
    if target_url and current_url_matches_target(current_url, target_url) and page_shell_visible(page, 800):
        return True
    return False


def validate_session(page, context=None, target_url: str = "") -> dict[str, Any]:
    checkpoint_required, checkpoint_reason = detect_checkpoint_or_verification(page)
    if checkpoint_required:
        return {
            "state": "verification_required",
            "reason": checkpoint_reason,
            "url": getattr(page, "url", target_url) or target_url,
            "cookiesPresent": bool(context and has_authenticated_session(context)),
        }

    login_required, login_reason = detect_login_gate(page)
    if login_required:
        return {
            "state": "login_required",
            "reason": login_reason,
            "url": getattr(page, "url", target_url) or target_url,
            "cookiesPresent": bool(context and has_authenticated_session(context)),
        }

    if page_ready_for_collection(page, target_url):
        return {
            "state": "ready",
            "reason": "Facebook page or feed content is visible.",
            "url": getattr(page, "url", target_url) or target_url,
            "cookiesPresent": bool(context and has_authenticated_session(context)),
        }

    return {
        "state": "unknown",
        "reason": "Facebook session could not be confirmed yet.",
        "url": getattr(page, "url", target_url) or target_url,
        "cookiesPresent": bool(context and has_authenticated_session(context)),
    }


def manual_login_url(target_url: str) -> str:
    return f"https://www.facebook.com/login.php?next={quote(target_url, safe='')}"


def click_button_if_visible(page, pattern: str, timeout_ms: int = 1500) -> bool:
    try:
        button = page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE)).first
        if button.count() > 0:
            button.click(timeout=timeout_ms)
            return True
    except Exception:
        pass
    return False


def save_storage_state(context, log_hook: Optional[LogHook] = None) -> None:
    path = get_storage_state_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            context.storage_state(path=str(path), indexed_db=True)
        except TypeError:
            context.storage_state(path=str(path))
        emit_log(log_hook, "INFO", "Storage state saved", str(path))
    except Exception as exc:
        emit_log(log_hook, "WARN", "Session save skipped", type(exc).__name__)


def auto_login_if_needed(page, context, target_url: str, log_hook: Optional[LogHook] = None) -> bool:
    if not has_login_credentials():
        return False
    if not wait_for_selector(page, LOGIN_FORM_SELECTOR, LOGIN_FORM_TIMEOUT):
        return False

    emit_log(log_hook, "INFO", "Facebook login", "Login form detected. Attempting env-based sign-in.")
    try:
        page.locator("input[name='email']").first.fill(FACEBOOK_USERNAME)
        page.locator("input[name='pass']").first.fill(FACEBOOK_PASSWORD)
        if page.locator("button[name='login']").count() > 0:
            page.locator("button[name='login']").first.click(timeout=3000)
        else:
            page.locator("button[type='submit']").first.click(timeout=3000)
    except Exception as exc:
        emit_log(log_hook, "WARN", "Facebook login failed", type(exc).__name__)
        return False

    try:
        page.wait_for_load_state("domcontentloaded", timeout=LOGIN_POST_SUBMIT_TIMEOUT)
    except Exception:
        pass

    click_button_if_visible(page, r"not now|skip|maybe later")
    page.goto(target_url, wait_until="domcontentloaded", timeout=POST_GOTO_TIMEOUT)
    apply_local_page_preferences(page)

    if page_ready_for_collection(page) and has_authenticated_session(context):
        save_storage_state(context, log_hook)
        emit_log(log_hook, "SUCCESS", "Facebook login", "Session is ready and the target page is visible.")
        return True

    emit_log(log_hook, "WARN", "Facebook login incomplete", "Target page did not become ready after sign-in.")
    return False


def route_nonessential_resources(route) -> None:
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        route.abort()
        return
    route.continue_()


def install_playwright_browsers() -> None:
    command = [sys.executable, "-m", "playwright", "install", "chromium", "chromium-headless-shell"]
    subprocess.run(command, check=True, timeout=240)


def launch_browser(playwright):
    global ACTIVE_BROWSER_ENGINE
    headless = False if uses_local_browser_window() else PLAYWRIGHT_HEADLESS
    launch_options = {
        "headless": headless,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-notifications",
            "--deny-permission-prompts",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    if uses_local_browser_window():
        user_data_dir = get_user_data_dir()
        if user_data_dir is None:
            raise RuntimeError("Facebook local browser mode requires a user data directory.")
        user_data_dir.mkdir(parents=True, exist_ok=True)
        persistent_options = {
            "headless": False,
            "viewport": {"width": 1440, "height": 960},
            "locale": "en-US",
            "args": launch_options["args"],
        }
        if PLAYWRIGHT_BROWSER_CHANNEL:
            persistent_options["channel"] = PLAYWRIGHT_BROWSER_CHANNEL
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                **persistent_options,
            )
            ACTIVE_BROWSER_ENGINE = PLAYWRIGHT_BROWSER_CHANNEL or "chromium"
        except PlaywrightError as exc:
            message = str(exc)
            missing_browser = "Executable doesn't exist" in message or "Please run the following command" in message
            if PLAYWRIGHT_BROWSER_CHANNEL:
                fallback_options = dict(persistent_options)
                fallback_options.pop("channel", None)
                try:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(user_data_dir),
                        **fallback_options,
                    )
                    ACTIVE_BROWSER_ENGINE = "chromium"
                    return None, context
                except PlaywrightError:
                    pass
            if not (PLAYWRIGHT_AUTO_INSTALL and missing_browser):
                raise
            install_playwright_browsers()
            fallback_options = dict(persistent_options)
            fallback_options.pop("channel", None)
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                **fallback_options,
            )
            ACTIVE_BROWSER_ENGINE = "chromium"
        return None, context

    try:
        browser = playwright.chromium.launch(**launch_options)
        ACTIVE_BROWSER_ENGINE = "chromium"
    except PlaywrightError as exc:
        message = str(exc)
        missing_browser = "Executable doesn't exist" in message or "Please run the following command" in message
        if not (PLAYWRIGHT_AUTO_INSTALL and missing_browser):
            raise
        install_playwright_browsers()
        browser = playwright.chromium.launch(**launch_options)
        ACTIVE_BROWSER_ENGINE = "chromium"

    context, _ = load_or_create_context(browser)
    return browser, context


def apply_local_page_preferences(page) -> None:
    if not uses_local_browser_window():
        return
    try:
        page.keyboard.press("Control+0")
        page.wait_for_timeout(60)
        page.keyboard.press("Control+-")
        page.wait_for_timeout(60)
        page.keyboard.press("Control+-")
    except Exception:
        try:
            page.evaluate(
                """() => {
                    const targets = [document.documentElement, document.body].filter(Boolean);
                    for (const node of targets) {
                        node.style.zoom = '75%';
                    }
                }"""
            )
        except Exception:
            return


def collect_visible_post_links(page, target_url: str = "") -> list[str]:
    return page.evaluate(
        """() => {
            const selectors = [
                "a[href*='/posts/']",
                "a[href*='/videos/']",
                "a[href*='/permalink/']",
                "a[href*='story_fbid=']",
                "a[href*='/photo.php']",
                "a[href*='/watch/?v=']",
            ];
            const links = [];
            const articleSelectors = [
                "div[role='feed'] div[role='article']",
                "div[data-pagelet*='FeedUnit']",
                "div[aria-posinset]",
            ];
            const articleNodes = new Set();
            for (const selector of articleSelectors) {
                for (const node of document.querySelectorAll(selector)) {
                    articleNodes.add(node);
                }
            }
            const roots = articleNodes.size ? Array.from(articleNodes) : [document];
            for (const root of roots) {
                for (const selector of selectors) {
                    for (const anchor of root.querySelectorAll(selector)) {
                        const href = (anchor.href || "").split("&__")[0];
                        if (!href) continue;
                        links.push(href);
                    }
                }
            }
            return links;
        }"""
    )


def get_scroll_state(page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
            const doc = document.scrollingElement || document.documentElement;
            const anchors = new Set(
                Array.from(document.querySelectorAll("a[href*='/posts/'], a[href*='/videos/'], a[href*='/permalink/'], a[href*='story_fbid='], a[href*='/photo.php'], a[href*='/watch/?v=']")).map(a => (a.href || '').split('&__')[0]).filter(Boolean)
            );
            const bodyHeight = Math.max(
                Number(doc.scrollHeight || 0),
                Number(document.body ? document.body.scrollHeight : 0),
                Number(document.documentElement ? document.documentElement.scrollHeight : 0)
            );
            const top = Number(doc.scrollTop || window.pageYOffset || 0);
            const viewport = Number(window.innerHeight || doc.clientHeight || 0);
            return {
                linkCount: anchors.size,
                scrollTop: top,
                scrollHeight: bodyHeight,
                bodyHeight,
                atBottom: top + viewport >= bodyHeight - 32,
            };
        }"""
    )


def apply_scroll_strategy(page, strategy: str) -> None:
    if strategy == "window-scroll":
        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 900));")
    elif strategy == "mouse-wheel":
        page.mouse.wheel(0, 2200)
    elif strategy == "page-down":
        page.keyboard.press("PageDown")
    else:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")


def wait_for_scroll_growth(page, previous_state: dict[str, Any], timeout_ms: int = SCROLL_WAIT_TIMEOUT) -> dict[str, Any]:
    try:
        page.wait_for_function(
            """(prev) => {
                const doc = document.scrollingElement || document.documentElement;
                const anchors = new Set(
                    Array.from(document.querySelectorAll("a[href*='/posts/'], a[href*='/videos/'], a[href*='/permalink/'], a[href*='story_fbid='], a[href*='/photo.php'], a[href*='/watch/?v=']")).map(a => (a.href || '').split('&__')[0]).filter(Boolean)
                );
                const bodyHeight = Math.max(
                    Number(doc.scrollHeight || 0),
                    Number(document.body ? document.body.scrollHeight : 0),
                    Number(document.documentElement ? document.documentElement.scrollHeight : 0)
                );
                const top = Number(doc.scrollTop || window.pageYOffset || 0);
                return anchors.size > prev.linkCount || bodyHeight > prev.bodyHeight + 24 || top > prev.scrollTop + 24;
            }""",
            arg=previous_state,
            timeout=timeout_ms,
        )
    except Exception:
        pass
    return get_scroll_state(page)


def collect_post_links(
    page,
    scroll_rounds: int,
    target_url: str = "",
    log_hook: Optional[LogHook] = None,
    progress_hook: Optional[ProgressHook] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    diagnostics: Optional[dict[str, Any]] = None,
) -> list[str]:
    links: dict[str, bool] = {}
    stagnant_rounds = 0
    stagnant_limit = max(6, min(14, max(6, scroll_rounds // 2))) if scroll_rounds > 0 else 6
    strategies = ("window-scroll", "mouse-wheel", "page-down", "bottom-jump")
    stop_reason = f"Reached max scroll rounds ({scroll_rounds})."

    initial_links = dedupe_post_links(collect_visible_post_links(page, target_url=target_url), target_url=target_url)
    for href in initial_links:
        if href and href not in links:
            links[href] = True

    emit_log(log_hook, "INFO", "Initial content", f"+{len(initial_links)} links visible (total={len(links)}).")
    emit_progress(progress_hook, 0, scroll_rounds, len(links))

    for round_index in range(1, scroll_rounds + 1):
        if cancel_check and cancel_check():
            raise RuntimeError("Cancelled during Facebook scrolling.")

        round_start = time.perf_counter()
        before_state = get_scroll_state(page)
        before_count = len(links)
        strategy_used = "none"
        height_before = before_state["bodyHeight"]
        height_after = height_before
        anchors_after = before_state["linkCount"]

        for strategy in strategies:
            strategy_used = strategy
            apply_scroll_strategy(page, strategy)
            page.wait_for_timeout(120)
            after_state = wait_for_scroll_growth(page, before_state, SCROLL_WAIT_TIMEOUT)
            height_after = after_state["bodyHeight"]
            anchors_after = after_state["linkCount"]
            fresh_links = dedupe_post_links(collect_visible_post_links(page, target_url=target_url), target_url=target_url)
            for href in fresh_links:
                if href and href not in links:
                    links[href] = True
            if len(links) > before_count or after_state["bodyHeight"] > before_state["bodyHeight"] + 24 or after_state["scrollTop"] > before_state["scrollTop"] + 24:
                break
            page.wait_for_timeout(PROFILE_RETRY_MS)

        new_links = len(links) - before_count
        elapsed = time.perf_counter() - round_start
        if new_links == 0:
            stagnant_rounds += 1
            detail = (
                f"+0 new links (total={len(links)}, strategy={strategy_used}, "
                f"height={height_before}->{height_after}, anchors={before_state['linkCount']}->{anchors_after}, "
                f"stagnant={stagnant_rounds}/{stagnant_limit})"
            )
            if before_state["atBottom"]:
                detail += ", bottom reached"
            emit_log(log_hook, "INFO", f"Scroll {round_index}", detail)
        else:
            stagnant_rounds = 0
            emit_log(
                log_hook,
                "INFO",
                f"Scroll {round_index}",
                (
                    f"+{new_links} new links (total={len(links)}, strategy={strategy_used}, "
                    f"height={height_before}->{height_after}, anchors={before_state['linkCount']}->{anchors_after})"
                ),
            )

        if elapsed >= SLOW_SCROLL_SECONDS:
            emit_log(log_hook, "WARN", "Slow scroll", f"Round {round_index} took {elapsed:.2f}s.")

        emit_progress(progress_hook, round_index, scroll_rounds, len(links))

        if stagnant_rounds >= stagnant_limit:
            stop_reason = f"Confirmed stagnation after {stagnant_rounds} rounds with no new Facebook links."
            break

    if diagnostics is not None:
        diagnostics["stopReason"] = stop_reason
        diagnostics["totalLinks"] = len(links)

    emit_log(log_hook, "INFO", "Link collection stop", stop_reason)
    return list(links.keys())


def infer_post_type(url: str) -> str:
    lower_url = url.lower()
    if "/videos/" in lower_url or "/watch/" in lower_url:
        return "Video"
    if "/photo.php" in lower_url:
        return "Photo"
    if "story_fbid=" in lower_url:
        return "Story / Post"
    return "Post"


def extract_post_date(page) -> tuple[str, Optional[datetime]]:
    script = """
        () => {
            const selectors = [
                "abbr",
                "time",
                "a[aria-label][href*='/posts/']",
                "a[aria-label][href*='story_fbid=']",
                "a[aria-label][href*='/videos/']",
            ];
            for (const selector of selectors) {
                const el = document.querySelector(selector);
                if (!el) continue;
                const candidates = [
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('data-utime') || '',
                    el.getAttribute('data-tooltip-content') || '',
                    el.getAttribute('datetime') || '',
                    (el.textContent || '').trim(),
                ].filter(Boolean);
                if (candidates.length) return candidates[0];
            }
            return '';
        }
    """
    raw_value = ""
    try:
        raw_value = page.evaluate(script) or ""
    except Exception:
        raw_value = ""
    return raw_value, parse_facebook_datetime(raw_value)


def count_visible_comment_nodes(page) -> int:
    script = """
        () => {
            const selectors = [
                "div[role='article'] ul li",
                "div[aria-label*='Comment']",
                "div[role='dialog'] ul li",
            ];
            const seen = new Set();
            for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                    if (node && node.innerText && node.innerText.trim().length >= 4) {
                        seen.add(node);
                    }
                }
            }
            return seen.size;
        }
    """
    try:
        return int(page.evaluate(script) or 0)
    except Exception:
        return 0


def expand_visible_comment_threads(page, log_hook: Optional[LogHook] = None, rounds: int = COMMENT_EXPANSION_ROUNDS) -> None:
    click_script = """
        () => {
            const keywords = [
                "view more comments",
                "see more comments",
                "more comments",
                "view previous comments",
                "view more replies",
                "see more replies",
                "more replies",
                "view previous replies",
            ];
            const nodes = Array.from(document.querySelectorAll("div[role='button'], button, span"));
            for (const node of nodes) {
                const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                if (!text) continue;
                if (!keywords.some(keyword => text.includes(keyword))) continue;
                const target = node.closest("div[role='button'], button") || node;
                if (!target || target.disabled) continue;
                const rect = target.getBoundingClientRect();
                if (rect.width < 1 || rect.height < 1) continue;
                target.scrollIntoView({ block: "center", inline: "nearest" });
                target.click();
                return text;
            }
            return "";
        }
    """

    previous_count = count_visible_comment_nodes(page)
    for round_index in range(1, rounds + 1):
        if round_index > 1:
            try:
                page.mouse.wheel(0, 900)
            except Exception:
                page.evaluate("window.scrollBy(0, 900);")
        clicked_label = ""
        try:
            clicked_label = page.evaluate(click_script) or ""
        except Exception:
            clicked_label = ""

        page.wait_for_timeout(COMMENT_LOAD_WAIT_MS)
        current_count = count_visible_comment_nodes(page)
        if clicked_label:
            emit_log(log_hook, "INFO", "Comments expanded", f"Round {round_index}: clicked '{clicked_label}' (visible nodes={current_count}).")
        elif current_count > previous_count:
            emit_log(log_hook, "INFO", "Comments expanded", f"Round {round_index}: more visible comments loaded (visible nodes={current_count}).")
        else:
            emit_log(log_hook, "INFO", "Comments expanded", f"Round {round_index}: no additional visible comments or replies.")

        if not clicked_label and current_count <= previous_count:
            previous_count = current_count
            continue
        previous_count = current_count


def extract_visible_comments(page, post_url: str, limit: int = 25, log_hook: Optional[LogHook] = None) -> list[CommentData]:
    expand_visible_comment_threads(page, log_hook=log_hook)
    script = """
        (limit) => {
            const results = [];
            const seen = new Set();
            const selectors = [
                "div[role='dialog'] ul li",
                "div[role='article'] ul li",
                "div[aria-label*='Comment']",
            ];
            const candidates = [];
            for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                    candidates.push(node);
                }
            }

            const datePattern = /(just now|\\d+\\s*(?:s|m|h|d|w)|\\d+\\s+(?:second|minute|hour|day|week|month|year)s?\\s+ago|yesterday|today|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i;
            const uiTokens = new Set(["like", "reply", "author", "edited", "top fan", "follow", "message"]);

            for (const node of candidates) {
                const text = (node.innerText || '').trim();
                if (!text || text.length < 8 || seen.has(text)) continue;
                const lines = text.split('\\n').map(line => line.trim()).filter(Boolean);
                if (!lines.length) continue;

                let commenter = '';
                const authorCandidate = node.querySelector("strong a, h3 a, a[role='link']");
                if (authorCandidate) {
                    commenter = (authorCandidate.textContent || '').trim();
                }
                if (!commenter) {
                    commenter = lines[0] || '';
                }

                let commentDate = '';
                const dateNode = node.querySelector("abbr, time, a[aria-label]");
                if (dateNode) {
                    commentDate = (
                        dateNode.getAttribute('aria-label') ||
                        dateNode.getAttribute('datetime') ||
                        dateNode.textContent ||
                        ''
                    ).trim();
                }
                if (!commentDate) {
                    const lineMatch = lines.find(line => datePattern.test(line));
                    commentDate = lineMatch || '';
                }

                const threadType = node.closest('ul ul') ? 'Reply' : 'Comment';
                const filtered = lines.filter((line, index) => {
                    const lower = line.toLowerCase();
                    if (!line) return false;
                    if (index === 0 && line === commenter) return false;
                    if (commentDate && line === commentDate) return false;
                    if (uiTokens.has(lower)) return false;
                    if (lower.startsWith('view more repl') || lower.startsWith('see more repl')) return false;
                    if (lower.startsWith('view more comment') || lower.startsWith('see more comment')) return false;
                    return true;
                });

                const commentText = filtered.join(' ').trim() || text;
                const identity = `${threadType}|${commenter}|${commentDate}|${commentText}`;
                if (!commentText || seen.has(identity)) continue;
                seen.add(identity);
                results.push({ commenter, commentText, commentDate, threadType });
                if (results.length >= limit) break;
            }
            return results;
        }
    """
    try:
        rows = page.evaluate(script, arg=limit)
    except Exception:
        return []

    comments: list[CommentData] = []
    for row in rows or []:
        comment_text = (row.get("commentText") or "").strip()
        commenter = (row.get("commenter") or "").strip()
        if not comment_text:
            continue
        comments.append(
            CommentData(
                post_url=post_url,
                commenter_name=commenter or "Unknown",
                comment_text=comment_text,
                comment_date_raw=(row.get("commentDate") or "").strip(),
                thread_type=(row.get("threadType") or "Comment").strip() or "Comment",
            )
        )
    return comments


def extract_text_metrics(page) -> tuple[Optional[int], Optional[int], Optional[int]]:
    try:
        body_text = page.locator("body").inner_text(timeout=2500)
    except Exception:
        body_text = ""

    reactions = None
    comments = None
    shares = None

    reaction_patterns = [
        r"([\d.,KMBkmb]+)\s+reactions?",
        r"([\d.,KMBkmb]+)\s+people reacted",
        r"([\d.,KMBkmb]+)\s+likes?",
    ]
    comment_patterns = [
        r"([\d.,KMBkmb]+)\s+comments?",
        r"([\d.,KMBkmb]+)\s+comment[s]?\b",
    ]
    share_patterns = [
        r"([\d.,KMBkmb]+)\s+shares?",
        r"([\d.,KMBkmb]+)\s+share[s]?\b",
    ]

    for pattern in reaction_patterns:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if match:
            reactions = parse_count(match.group(1))
            break
    for pattern in comment_patterns:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if match:
            comments = parse_count(match.group(1))
            break
    for pattern in share_patterns:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if match:
            shares = parse_count(match.group(1))
            break

    return reactions, comments, shares


def open_post_for_extraction(page, url: str, goto_timeout: int = POST_GOTO_TIMEOUT) -> tuple[str, Optional[datetime], str]:
    page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout)
    wait_for_selector(page, "body", 2000)
    apply_local_page_preferences(page)
    page.wait_for_timeout(200)
    post_type = infer_post_type(url)
    raw_date, date_obj = extract_post_date(page)
    return raw_date, date_obj, post_type


def extract_metrics_from_loaded_post(
    page,
    url: str,
    raw_date: str,
    date_obj: Optional[datetime],
    post_type: str,
    collection_type: str,
    log_hook: Optional[LogHook] = None,
) -> PostData:
    reactions, comments_count, shares = extract_text_metrics(page)
    notes: list[str] = []
    if reactions is None:
        notes.append("Reactions not visible")
    if comments_count is None:
        notes.append("Comments count not visible")
    if shares is None:
        notes.append("Shares not visible")

    comments_preview: list[CommentData] = []
    if collection_type == "posts_with_comments":
        comments_preview = extract_visible_comments(page, url, log_hook=log_hook)
        if not comments_preview:
            notes.append("No visible comment samples captured")

    emit_log(
        log_hook,
        "INFO",
        "Metrics extracted",
        (
            f"{url} -> reactions={reactions if reactions is not None else 'N/A'}, "
            f"comments={comments_count if comments_count is not None else 'N/A'}, "
            f"shares={shares if shares is not None else 'N/A'}, "
            f"date={raw_date or 'N/A'}"
        ),
    )

    return PostData(
        url=url,
        post_type=post_type,
        post_date_raw=raw_date,
        post_date_obj=date_obj,
        reactions=reactions,
        comments_count=comments_count,
        shares=shares,
        notes="; ".join(notes),
        comments_preview=comments_preview,
    )


def classify_post_date_coverage(
    post_date_obj: Optional[datetime],
    start_date: datetime,
    end_date: Optional[datetime],
) -> tuple[str, str]:
    if post_date_obj is None:
        return "unknown_date", "Skipped because the Facebook post date could not be detected."
    if end_date is not None and post_date_obj > end_date:
        return "newer_than_end", f"Skipped because {post_date_obj.strftime(DATE_INPUT_FORMAT)} is newer than end date {end_date.strftime(DATE_INPUT_FORMAT)}."
    if post_date_obj < start_date:
        return "older_than_start", f"Skipped because {post_date_obj.strftime(DATE_INPUT_FORMAT)} is older than start date {start_date.strftime(DATE_INPUT_FORMAT)}."
    return "in_range", f"Included because {post_date_obj.strftime(DATE_INPUT_FORMAT)} is within the selected Facebook date coverage."


def post_matches_date_coverage(post: PostData, start_date: datetime, end_date: Optional[datetime]) -> bool:
    return classify_post_date_coverage(post.post_date_obj, start_date, end_date)[0] == "in_range"


def format_post_date(post: PostData) -> str:
    if post.post_date_obj is not None:
        return post.post_date_obj.strftime(DATE_INPUT_FORMAT)
    return post.post_date_raw or "Cannot detect"


def save_facebook_excel(posts: list[PostData], filename: str, coverage_label: str, collection_type: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Facebook Posts"

    ws["A1"] = coverage_label
    ws["A2"] = f"Collection type: {collection_type.replace('_', ' ')}"
    ws.append([])
    ws.append(["Post Link", "Post Date", "Post Type", "Reactions", "Comments Count", "Shares", "Notes"])

    for post in posts:
        ws.append([
            post.url,
            format_post_date(post),
            post.post_type,
            "" if post.reactions is None else post.reactions,
            "" if post.comments_count is None else post.comments_count,
            "" if post.shares is None else post.shares,
            post.notes,
        ])

    ws.column_dimensions["A"].width = 64
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 44

    comments_sheet = wb.create_sheet("Visible Comments")
    comments_sheet.append(["Post Link", "Thread Type", "Commenter", "Comment Date", "Comment Text"])
    comment_rows = 0
    for post in posts:
        for comment in post.comments_preview:
            comment_rows += 1
            comments_sheet.append([
                comment.post_url,
                comment.thread_type,
                comment.commenter_name,
                comment.comment_date_raw,
                comment.comment_text,
            ])

    if comment_rows == 0:
        comments_sheet["A2"] = "No visible public comment samples were captured for this run."

    comments_sheet.column_dimensions["A"].width = 64
    comments_sheet.column_dimensions["B"].width = 14
    comments_sheet.column_dimensions["C"].width = 24
    comments_sheet.column_dimensions["D"].width = 18
    comments_sheet.column_dimensions["E"].width = 80

    wb.save(filename)


def save_empty_result_excel(
    filename: str,
    coverage_label: str,
    total_links_collected: int,
    oldest_detected: Optional[datetime],
    newest_detected: Optional[datetime],
    reason: str,
) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Facebook Diagnostics"

    ws["A1"] = "No posts found within selected date range."
    ws["A2"] = coverage_label
    ws["A4"] = "Reason"
    ws["B4"] = reason
    ws["A5"] = "Total links collected"
    ws["B5"] = total_links_collected
    ws["A6"] = "Newest detected post date"
    ws["B6"] = newest_detected.strftime(DATE_INPUT_FORMAT) if newest_detected else "Unknown"
    ws["A7"] = "Oldest detected post date"
    ws["B7"] = oldest_detected.strftime(DATE_INPUT_FORMAT) if oldest_detected else "Unknown"

    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 70
    wb.save(filename)
