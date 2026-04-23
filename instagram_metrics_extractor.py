"""
Instagram Metrics Extractor (Selenium)
======================================
Collect Instagram post links and extract date, likes, comments, and repost/share
counts from the visible post action row.

Usage:
    python instagram_metrics_extractor.py
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import InvalidSessionIdException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# ============================================================================
# CONFIGURATION
# ============================================================================

USER_DATA_DIR = "ig_profile_data"
PROFILE_URL = "https://www.instagram.com/cebuanalhuillier/"

# False = process TEST_URLS only. True = crawl profile first.
USE_PROFILE_CRAWL = False

TEST_URLS = [
    "https://www.instagram.com/cebuanalhuillier/p/DXap3k2HDTo/",
    "https://www.instagram.com/cebuanalhuillier/p/DXZJTlBlIXo/",
    "https://www.instagram.com/cebuanalhuillier/p/DXWQaJfmR_6/",
]

MAX_SCROLL_ROUNDS = 8
MAX_STAGNANT_ROUNDS = 4
POST_LOAD_RETRIES = 2


# ============================================================================
# DATA MODEL
# ============================================================================

@dataclass
class PostMetrics:
    link: str
    post_type: str
    date_raw: str
    date_obj: Optional[datetime]
    likes: Optional[int]
    comments: Optional[int]
    shares: int
    method: str


# ============================================================================
# GENERAL HELPERS
# ============================================================================

COUNT_RE = re.compile(r"^(\d+(?:\.\d+)?)([KMB]?)$")


def parse_count(text: Optional[str]) -> Optional[int]:
    """Convert Instagram count text like '1,234', '4.5K', or '2M' to int."""
    if not text:
        return None

    cleaned = text.strip().upper().replace(",", "").replace(" ", "")
    match = COUNT_RE.match(cleaned)
    if not match:
        digits = re.sub(r"[^\d]", "", cleaned)
        return int(digits) if digits else None

    number = float(match.group(1))
    suffix = match.group(2)
    if suffix == "K":
        number *= 1_000
    elif suffix == "M":
        number *= 1_000_000
    elif suffix == "B":
        number *= 1_000_000_000
    return round(number)


def parse_datetime(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None

    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def infer_post_type(url: str) -> str:
    lowered = url.lower()
    if "/reel/" in lowered:
        return "Reel"
    if "/tv/" in lowered:
        return "Video"
    if "/p/" in lowered:
        return "Photo/Video"
    return "Unknown"


def is_driver_alive(driver: webdriver.Chrome) -> bool:
    try:
        driver.execute_script("return 1")
        return True
    except (InvalidSessionIdException, WebDriverException):
        return False


# ============================================================================
# SELENIUM BOOTSTRAP
# ============================================================================

def find_browser_binary() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]

    for path in candidates:
        if path and os.path.exists(path):
            return path

    raise RuntimeError(
        "No Chromium-based browser found. Install Google Chrome, Brave, or Edge."
    )


def build_driver() -> webdriver.Chrome:
    """Create a Selenium session using Selenium Manager for driver matching."""
    user_data_dir = str(Path(f"{USER_DATA_DIR}_chrome").resolve())
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    options = webdriver.ChromeOptions()
    options.binary_location = find_browser_binary()
    options.page_load_strategy = "eager"
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=en-US")

    print(f"Using browser binary: {options.binary_location}")

    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(45)
        driver.execute_script("return 1")
        print("Driver bootstrap: browser started successfully.")
        return driver
    except Exception as exc:
        raise RuntimeError(
            "Unable to start Selenium browser session.\n"
            f"Browser used: {options.binary_location}\n"
            f"Error: {exc}"
        )


# ============================================================================
# WAIT HELPERS
# ============================================================================

def wait_for_profile_ready(driver: webdriver.Chrome, timeout: int = 12) -> None:
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "a[href*='/p/'], a[href*='/reel/'], a[href*='/tv/']")
            )
        )
    except TimeoutException:
        pass


def wait_for_post_ready(driver: webdriver.Chrome, timeout: int = 10) -> None:
    for selector, wait_time in (("article", timeout), ("time", 4)):
        try:
            WebDriverWait(driver, wait_time).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
        except TimeoutException:
            pass


def wait_for_action_row(driver: webdriver.Chrome, timeout: int = 8) -> None:
    selector = (
        "main section svg[aria-label*='Like'], "
        "main section svg[aria-label*='Comment'], "
        "main section svg[aria-label*='Repost'], "
        "article section svg[aria-label*='Like'], "
        "article section svg[aria-label*='Comment']"
    )
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
        )
    except TimeoutException:
        pass


# ============================================================================
# LINK COLLECTION
# ============================================================================

def collect_post_links(driver: webdriver.Chrome, profile_url: str) -> list[str]:
    driver.get(profile_url)
    wait_for_profile_ready(driver)

    links: set[str] = set()
    stagnant_rounds = 0

    print(f"Collecting profile links: rounds={MAX_SCROLL_ROUNDS}")
    for round_index in range(1, MAX_SCROLL_ROUNDS + 1):
        before = len(links)
        anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='/p/'], a[href*='/reel/'], a[href*='/tv/']")

        for anchor in anchors:
            href = anchor.get_attribute("href")
            if href:
                links.add(href.split("?")[0])

        gained = len(links) - before
        stagnant_rounds = stagnant_rounds + 1 if gained == 0 else 0
        print(f"  Scroll {round_index}: +{gained} links, total={len(links)}")

        if stagnant_rounds >= MAX_STAGNANT_ROUNDS:
            break

        driver.execute_script("window.scrollBy(0, 4000);")
        time.sleep(0.6)

    print(f"Collection complete: {len(links)} links\n")
    return list(links)


# ============================================================================
# METRIC EXTRACTION
# ============================================================================

ACTION_ROW_METRICS_SCRIPT = r"""
return (() => {
  const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const isMetricText = (text) => /^\d[\d.,]*(?:\s*[KMBkmb])?$/.test(text);

  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function rectInfo(el) {
    const rect = el.getBoundingClientRect();
    return {
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
      centerX: rect.left + rect.width / 2,
      centerY: rect.top + rect.height / 2
    };
  }

  function labelText(el) {
    return normalize([
      el.getAttribute?.('aria-label'),
      el.getAttribute?.('title'),
      el.getAttribute?.('alt'),
      el.innerText,
      el.textContent
    ].filter(Boolean).join(' '));
  }

  function metricFromLabel(label) {
    const lower = normalize(label).toLowerCase();
    if (/\b(unlike|like)\b/.test(lower)) return 'like';
    if (/\bcomment\b/.test(lower)) return 'comment';
    if (/\brepost\b/.test(lower)) return 'repost';
    return null;
  }

  const rows = Array.from(document.querySelectorAll('main section, article section, section'))
    .filter(isVisible)
    .map((row) => {
      const labels = Array.from(row.querySelectorAll("svg[aria-label], img[alt], [role='button'], button, a"))
        .filter(isVisible)
        .map(labelText);
      const metrics = new Set(labels.map(metricFromLabel).filter(Boolean));
      const hasShareOrSave = labels.some((label) => /\b(share|save)\b/i.test(label));
      const rect = rectInfo(row);
      const score = (rect.height * 5) + (rect.width * 0.05) - (hasShareOrSave ? 100 : 0);
      return { row, metrics, score };
    })
    .filter((item) => item.metrics.has('like') && item.metrics.has('comment'))
    .sort((a, b) => a.score - b.score);

  const row = rows[0]?.row;
  if (!row) return {};

  const actions = [];
  const seenActions = new Set();
  for (const el of Array.from(row.querySelectorAll("svg[aria-label], img[alt], [role='button'], button, a"))) {
    if (!isVisible(el)) continue;

    const metric = metricFromLabel(labelText(el));
    if (!metric) continue;

    const actionEl = el.closest("button, a, [role='button']") || el;
    if (!isVisible(actionEl)) continue;

    const rect = rectInfo(actionEl);
    const key = `${metric}:${Math.round(rect.centerX)}:${Math.round(rect.centerY)}`;
    if (seenActions.has(key)) continue;
    seenActions.add(key);
    actions.push({ metric, rect });
  }
  actions.sort((a, b) => a.rect.centerX - b.rect.centerX);

  const counts = [];
  const seenCounts = new Set();
  for (const el of Array.from(row.querySelectorAll('span, div, a, button'))) {
    if (!isVisible(el)) continue;

    const text = normalize(el.innerText || el.textContent);
    if (!isMetricText(text)) continue;

    const rect = rectInfo(el);
    const key = `${text}:${Math.round(rect.centerX)}:${Math.round(rect.centerY)}`;
    if (seenCounts.has(key)) continue;
    seenCounts.add(key);
    counts.push({ text, rect });
  }

  const result = {};
  const usedCountIndexes = new Set();
  for (let i = 0; i < actions.length; i += 1) {
    const action = actions[i];
    const nextAction = actions[i + 1];
    const rightLimit = nextAction ? nextAction.rect.centerX - 3 : action.rect.centerX + 120;

    const best = counts
      .map((count, index) => ({ count, index }))
      .filter(({ count, index }) => {
        if (usedCountIndexes.has(index)) return false;
        const dx = count.rect.centerX - action.rect.centerX;
        const dy = Math.abs(count.rect.centerY - action.rect.centerY);
        return dx >= 0 && count.rect.centerX <= rightLimit && dy <= 28;
      })
      .sort((a, b) => {
        const aScore = (a.count.rect.centerX - action.rect.centerX)
          + Math.abs(a.count.rect.centerY - action.rect.centerY) * 2;
        const bScore = (b.count.rect.centerX - action.rect.centerX)
          + Math.abs(b.count.rect.centerY - action.rect.centerY) * 2;
        return aScore - bScore;
      })[0];

    if (best) {
      result[action.metric] = best.count.text;
      usedCountIndexes.add(best.index);
    }
  }

  return result;
})();
"""


def extract_action_row_metrics(driver: webdriver.Chrome) -> tuple[Optional[int], Optional[int], int, str]:
    try:
        result = driver.execute_script(ACTION_ROW_METRICS_SCRIPT)
    except WebDriverException:
        result = {}

    if not isinstance(result, dict):
        result = {}

    likes = parse_count(result.get("like"))
    comments = parse_count(result.get("comment"))
    shares = parse_count(result.get("repost")) or 0
    method = "action_row" if any(value is not None for value in (likes, comments)) or shares else "failed"
    return likes, comments, shares, method


def extract_date(driver: webdriver.Chrome) -> tuple[str, Optional[datetime]]:
    try:
        time_el = driver.find_element(By.CSS_SELECTOR, "time")
        raw = time_el.get_attribute("datetime") or time_el.text.strip()
        return raw, parse_datetime(raw)
    except Exception:
        return "", None


def extract_post_data(driver: webdriver.Chrome, url: str) -> PostMetrics:
    post_type = infer_post_type(url)
    date_raw = ""
    date_obj: Optional[datetime] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares = 0
    method = "failed"

    for attempt in range(1, POST_LOAD_RETRIES + 1):
        try:
            driver.get(url)
            wait_for_post_ready(driver)
            wait_for_action_row(driver)

            date_raw, date_obj = extract_date(driver)
            likes, comments, shares, method = extract_action_row_metrics(driver)
            print(f"    Attempt {attempt}: method={method}, likes={likes}, comments={comments}, shares={shares}")
            break
        except InvalidSessionIdException:
            raise
        except Exception as exc:
            print(f"    Attempt {attempt}: {type(exc).__name__}")
            if attempt < POST_LOAD_RETRIES:
                time.sleep(1.0)

    return PostMetrics(
        link=url,
        post_type=post_type,
        date_raw=date_raw,
        date_obj=date_obj,
        likes=likes,
        comments=comments,
        shares=shares,
        method=method,
    )


# ============================================================================
# MAIN
# ============================================================================

def print_result(post: PostMetrics) -> None:
    print("  RESULT")
    print(f"    Date:      {post.date_raw or 'N/A'}")
    print(f"    Type:      {post.post_type}")
    print(f"    Likes:     {post.likes}")
    print(f"    Comments:  {post.comments}")
    print(f"    Shares:    {post.shares}")
    print(f"    Method:    {post.method}")


def print_summary(results: list[PostMetrics]) -> None:
    print("\n" + "=" * 88)
    print("SUMMARY")
    print("=" * 88)

    ok_count = 0
    for post in results:
        status = "OK" if post.likes is not None or post.comments is not None else "MISS"
        if status == "OK":
            ok_count += 1

        print(f"[{status}] {post.link}")
        print(
            f"      date={post.date_raw or 'N/A'} "
            f"type={post.post_type} likes={post.likes} "
            f"comments={post.comments} shares={post.shares} method={post.method}"
        )

    print(f"\nSuccess: {ok_count}/{len(results)} posts with at least one metric extracted\n")


def main() -> None:
    print("\n" + "=" * 88)
    print("INSTAGRAM METRICS EXTRACTOR (SELENIUM)")
    print("=" * 88)

    driver = build_driver()
    try:
        if USE_PROFILE_CRAWL:
            input("Log in to Instagram if needed, then press Enter to collect links... ")
            links = collect_post_links(driver, PROFILE_URL)
        else:
            input("Log in to Instagram if needed, then press Enter to test URLs... ")
            links = TEST_URLS

        print(f"\nProcessing {len(links)} URLs...\n")

        results: list[PostMetrics] = []
        for index, url in enumerate(links, 1):
            print("-" * 88)
            print(f"[{index}/{len(links)}] {url}")

            if not is_driver_alive(driver):
                print("Driver session not alive. Rebuilding browser session...")
                driver = build_driver()

            try:
                post = extract_post_data(driver, url)
            except InvalidSessionIdException:
                print("Session became invalid. Rebuilding and retrying once...")
                driver = build_driver()
                post = extract_post_data(driver, url)

            results.append(post)
            print_result(post)
            time.sleep(0.2)

        print_summary(results)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
