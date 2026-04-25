"""
S&R Extract — Instagram Comment Collector
==========================================
Collects all visible comments from a list of Instagram post URLs.

Strategy per post:
  1. Navigate to post URL (reuses existing browser session)
  2. Wait for the post dialog/article to appear
  3. Click "View all X comments" if present
  4. Repeatedly click "Load more comments" until no more appear
  5. Collect commenter username + comment text + timestamp
  6. Return flat list of dicts

Selectors are intentionally listed with multiple fallbacks because
Instagram changes its class names regularly.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Selector banks — ordered by priority (most stable first)
# ---------------------------------------------------------------------------

_VIEW_ALL_SELECTORS = [
    "article a:has-text('View all')",
    "article span:has-text('View all')",
    "div[role='dialog'] a:has-text('View all')",
    "div[role='dialog'] span:has-text('View all')",
]

_LOAD_MORE_SELECTORS = [
    "button:has-text('Load more comments')",
    "span:has-text('Load more comments')",
    "div[role='button']:has-text('Load more comments')",
    "button:has-text('View more comments')",
]

_COMMENT_ITEM_SELECTORS = [
    "article ul ul li",
    "div[role='dialog'] ul ul li",
    "article ul li:not(:first-child)",
]

_COMMENTER_SELECTORS = [
    "a[role='link']:first-child span",
    "h3 a",
    "h2 a",
    "a[href*='/'] span",
]

_COMMENT_TEXT_SELECTORS = [
    "div._a9zs span",
    "div._a9zr span",
    "span[class*='_aacl']",
    "span:not([class*='icon'])",
]

_TIMESTAMP_SELECTORS = [
    "time",
    "time[datetime]",
    "a time",
]

_POST_READY_SELECTORS = [
    "article",
    "div[role='dialog'] article",
    "div[role='presentation'] article",
    "main article",
]

_POST_GOTO_TIMEOUT = 30_000
_WAIT_AFTER_EXPAND_MS = 1_000
_LOAD_MORE_ROUNDS = 25
_WAIT_BETWEEN_ROUNDS_S = 0.8


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
                loc.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _extract_comments_from_page(page, post_url: str) -> list[dict]:
    """Extract all currently visible comments from the loaded post page."""
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
                                # Prefer the datetime attribute
                                ts = el.get_attribute("datetime") or el.inner_text(timeout=400) or ""
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
                break   # found items with the current selector — stop trying others
        except Exception:
            continue

    return comments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_all_comments_ig(
    page,
    post_links: list[str],
    log_hook: Optional[Callable[[str, str, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> list[dict]:
    """
    Collect all visible comments from the given Instagram post links.

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

    _log("INFO", "Comment collection started", f"Collecting comments from {total} Instagram posts.")

    for index, url in enumerate(post_links, start=1):
        if _cancelled():
            _log("WARN", "Comment collection cancelled", f"Stopped at post {index}/{total}.")
            break

        _log("INFO", f"Opening post {index}/{total}", url)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_POST_GOTO_TIMEOUT)
            page.wait_for_timeout(600)

            # Wait for post to render
            if not _wait_for_any(page, _POST_READY_SELECTORS, timeout_ms=5_000):
                _log("WARN", "Post not ready", f"Timed out waiting for post article at {url}")
                continue

            # Click "View all X comments" if present
            _click_first_visible(page, _VIEW_ALL_SELECTORS, timeout_ms=1_200)
            page.wait_for_timeout(_WAIT_AFTER_EXPAND_MS)

            # Expand all comments
            for round_num in range(_LOAD_MORE_ROUNDS):
                if _cancelled():
                    break
                loaded = _click_first_visible(page, _LOAD_MORE_SELECTORS, timeout_ms=1_000)
                if not loaded:
                    break
                page.wait_for_timeout(int(_WAIT_BETWEEN_ROUNDS_S * 1_000))
                _log("INFO", f"Expanded comments round {round_num + 1}", f"Post {index}/{total}")

            # Extract all visible comments
            post_comments = _extract_comments_from_page(page, url)
            _log("INFO", f"Comments collected", f"{len(post_comments)} comments from post {index}/{total}")
            all_comments.extend(post_comments)

        except Exception as exc:
            _log("WARN", f"Comment collection failed for post {index}/{total}",
                 f"{type(exc).__name__}: {exc}")
            continue

        # Brief pause between posts
        time.sleep(0.5)

    _log("SUCCESS", "Instagram comment collection complete",
         f"Total comments collected: {len(all_comments)}")
    return all_comments
