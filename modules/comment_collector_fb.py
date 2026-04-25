"""
S&R Extract — Facebook Comment Collector
=========================================
Collects all visible comments from a list of Facebook post URLs.

Strategy per post:
  1. Navigate to post URL (reuses existing browser session)
  2. Switch to "All comments" filter if available (not Most Relevant)
  3. Repeatedly click "View more comments" / "View X more comments"
  4. Click "See more" on truncated comments
  5. Click "View X replies" on threaded comments
  6. Collect commenter name + comment text + timestamp
  7. Return flat list of dicts

Facebook changes its DOM layout frequently; multiple selector fallbacks
are used at every step.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Selector banks
# ---------------------------------------------------------------------------

_ALL_COMMENTS_SELECTORS = [
    "div[role='tablist'] span:has-text('All comments')",
    "div[role='tablist'] div:has-text('All comments')",
    "span:has-text('Most relevant')",        # click to open filter dropdown
    "div[role='button']:has-text('Most relevant')",
]

_ALL_COMMENTS_OPTION_SELECTORS = [
    "div[role='option']:has-text('All comments')",
    "div[role='menuitem']:has-text('All comments')",
    "span:has-text('All comments')",
]

_VIEW_MORE_SELECTORS = [
    "div[role='button']:has-text('View more comments')",
    "span:has-text('View more comments')",
    "div[role='button']:has-text('View') span:has-text('more comments')",
]

_VIEW_MORE_REGEX = re.compile(r"view\s+\d+\s+more\s+comment", re.IGNORECASE)

_VIEW_REPLIES_SELECTORS = [
    "div[role='button']:has-text('View') span:has-text('repl')",
    "span:has-text('replies')",
    "div[role='button']:has-text('replies')",
]

_SEE_MORE_SELECTORS = [
    "div[role='button']:has-text('See more')",
    "span:has-text('See more')",
]

_COMMENT_ITEM_SELECTORS = [
    "div[role='article']",
    "div[data-testid='UFI2Comment/root_depth_0']",
    "ul[role='list'] > li",
]

_COMMENTER_SELECTORS = [
    "a[role='link']:has(> span)",
    "h4 a",
    "h3 a",
    "a[href*='facebook.com/'] span",
]

_COMMENT_TEXT_SELECTORS = [
    "div[dir='auto']",
    "span[dir='auto']",
    "div[data-testid='UFI2Comment/body'] div[dir='auto']",
]

_TIMESTAMP_SELECTORS = [
    "a time",
    "time[datetime]",
    "abbr[data-utime]",
    "a[href*='comment_id'] span",
]

_POST_READY_SELECTORS = [
    "div[role='article']",
    "div[data-pagelet='FeedUnit']",
    "div[role='main']",
]

_POST_GOTO_TIMEOUT = 35_000
_WAIT_AFTER_NAVIGATE_MS = 1_500
_WAIT_AFTER_CLICK_MS = 900
_LOAD_MORE_ROUNDS = 30
_VIEW_REPLIES_ROUNDS = 15
_SEE_MORE_ROUNDS = 10
_WAIT_BETWEEN_ROUNDS_S = 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_for_any(page, selectors: list[str], timeout_ms: int = 3_000) -> bool:
    for selector in selectors:
        try:
            page.locator(selector).first.wait_for(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def _click_first_visible(page, selectors: list[str], timeout_ms: int = 1_500) -> bool:
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0:
                loc.scroll_into_view_if_needed(timeout=timeout_ms)
                loc.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _click_all_regex_buttons(page, pattern: re.Pattern, timeout_ms: int = 1_000) -> int:
    """Click all visible buttons whose text matches a regex. Returns count clicked."""
    clicked = 0
    try:
        buttons = page.locator("div[role='button'], span[role='button']").all()
        for btn in buttons:
            try:
                text = btn.inner_text(timeout=300) or ""
                if pattern.search(text):
                    btn.click(timeout=timeout_ms)
                    clicked += 1
                    page.wait_for_timeout(200)
            except Exception:
                continue
    except Exception:
        pass
    return clicked


def _expand_all_comments(page, log_hook, post_index: int, total: int) -> None:
    """Click every expansion control until none remain."""
    def _log(level, action, detail=""):
        if log_hook:
            try:
                log_hook(level, action, detail)
            except Exception:
                pass

    # 1. Switch to "All comments"
    switched = _click_first_visible(page, _ALL_COMMENTS_SELECTORS, timeout_ms=1_500)
    if switched:
        page.wait_for_timeout(700)
        # In case we opened a dropdown — pick "All comments" option
        _click_first_visible(page, _ALL_COMMENTS_OPTION_SELECTORS, timeout_ms=1_200)
        page.wait_for_timeout(700)

    # 2. Expand "View more comments" (top-level)
    for round_num in range(_LOAD_MORE_ROUNDS):
        clicked = _click_first_visible(page, _VIEW_MORE_SELECTORS, timeout_ms=1_000)
        regex_clicked = _click_all_regex_buttons(page, _VIEW_MORE_REGEX, timeout_ms=900)
        if not clicked and not regex_clicked:
            break
        page.wait_for_timeout(int(_WAIT_BETWEEN_ROUNDS_S * 1_000))
        _log("INFO", f"Expanded view-more round {round_num + 1}",
             f"Post {post_index}/{total}")

    # 3. Expand replies
    for _ in range(_VIEW_REPLIES_ROUNDS):
        if not _click_first_visible(page, _VIEW_REPLIES_SELECTORS, timeout_ms=900):
            break
        page.wait_for_timeout(600)

    # 4. Expand truncated "See more" comments
    for _ in range(_SEE_MORE_ROUNDS):
        if not _click_first_visible(page, _SEE_MORE_SELECTORS, timeout_ms=800):
            break
        page.wait_for_timeout(300)


def _extract_comments_from_page(page, post_url: str) -> list[dict]:
    """Extract all currently visible comments from the loaded FB post page."""
    comments: list[dict] = []

    for item_selector in _COMMENT_ITEM_SELECTORS:
        try:
            items = page.locator(item_selector).all()
            if not items:
                continue

            for item in items:
                try:
                    # Commenter name
                    commenter = ""
                    for sel in _COMMENTER_SELECTORS:
                        try:
                            el = item.locator(sel).first
                            if el.count() > 0:
                                commenter = (el.inner_text(timeout=500) or "").strip()
                                if commenter:
                                    break
                        except Exception:
                            continue

                    # Comment text
                    text = ""
                    for sel in _COMMENT_TEXT_SELECTORS:
                        try:
                            el = item.locator(sel).first
                            if el.count() > 0:
                                text = (el.inner_text(timeout=500) or "").strip()
                                if text and text != commenter:
                                    break
                        except Exception:
                            continue

                    # Timestamp
                    timestamp = ""
                    for sel in _TIMESTAMP_SELECTORS:
                        try:
                            el = item.locator(sel).first
                            if el.count() > 0:
                                ts = (
                                    el.get_attribute("datetime")
                                    or el.get_attribute("data-utime")
                                    or el.inner_text(timeout=400)
                                    or ""
                                )
                                timestamp = ts.strip()
                                if timestamp:
                                    break
                        except Exception:
                            continue

                    if text and commenter:
                        comments.append({
                            "post_url":  post_url,
                            "commenter": commenter,
                            "text":      text,
                            "timestamp": timestamp,
                        })
                except Exception:
                    continue

            if comments:
                break
        except Exception:
            continue

    return comments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_all_comments_fb(
    page,
    post_links: list[str],
    log_hook: Optional[Callable[[str, str, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> list[dict]:
    """
    Collect all visible comments from the given Facebook post links.

    Parameters
    ----------
    page        : Playwright Page object (already authenticated)
    post_links  : ordered list of post URLs to visit
    log_hook    : optional callback(level, action, details)
    cancel_check: optional callback that returns True when cancelled

    Returns
    -------
    List of comment dicts: {post_url, commenter, text, timestamp}
    """
    def _log(level: str, action: str, details: str = "") -> None:
        if log_hook:
            try:
                log_hook(level, action, details)
            except Exception:
                pass

    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    all_comments: list[dict] = []
    total = len(post_links)

    _log("INFO", "Comment collection started",
         f"Collecting comments from {total} Facebook posts.")

    for index, url in enumerate(post_links, start=1):
        if _cancelled():
            _log("WARN", "Comment collection cancelled", f"Stopped at post {index}/{total}.")
            break

        _log("INFO", f"Opening post {index}/{total}", url)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_POST_GOTO_TIMEOUT)
            page.wait_for_timeout(_WAIT_AFTER_NAVIGATE_MS)

            # Wait for post to be ready
            if not _wait_for_any(page, _POST_READY_SELECTORS, timeout_ms=6_000):
                _log("WARN", "Post not ready",
                     f"Timed out waiting for post content at {url}")
                continue

            # Expand all available comments
            _expand_all_comments(page, log_hook, index, total)

            # Extract all visible comments
            post_comments = _extract_comments_from_page(page, url)
            _log("INFO", "Comments collected",
                 f"{len(post_comments)} comments from post {index}/{total}")
            all_comments.extend(post_comments)

        except Exception as exc:
            _log("WARN", f"Comment collection failed for post {index}/{total}",
                 f"{type(exc).__name__}: {exc}")
            continue

        # Polite pause between posts
        time.sleep(0.6)

    _log("SUCCESS", "Facebook comment collection complete",
         f"Total comments collected: {len(all_comments)}")
    return all_comments
