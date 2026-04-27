"""
Facebook scraper - unified interface for Facebook content extraction.

This module wraps the existing facebook_to_excel logic and provides
a clean interface for integration with the new core modules.
"""

from typing import Optional, Callable, Any
from datetime import datetime
from pathlib import Path

# Import the existing Facebook scraper module for now
import facebook_to_excel as legacy_scraper


def detect_facebook_ready(page) -> tuple[bool, str]:
    """
    Detect if Facebook page is ready for extraction.
    
    Uses existing readiness detection from the legacy module.
    """
    try:
        return legacy_scraper.facebook_strong_ready_signal(page)
    except AttributeError:
        # Fallback if function doesn't exist
        return False, "Facebook readiness check not available"


def collect_facebook_posts(
    page,
    load_rounds: int,
    start_date: datetime,
    log_hook: Optional[Callable] = None,
    progress_hook: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
) -> list[str]:
    """
    Collect Facebook post links through loading/scrolling.
    
    Args:
        page: Playwright page object
        load_rounds: Number of load rounds
        start_date: Target start date for collection
        log_hook: Optional logging callback
        progress_hook: Optional progress callback
        cancel_check: Optional cancellation check callback
    
    Returns:
        List of post URLs collected
    """
    try:
        return legacy_scraper.collect_post_links(
            page,
            max_posts=None,
            scroll_rounds=load_rounds,
            target_start_date=start_date,
            log_hook=log_hook,
            progress_hook=progress_hook,
            cancel_check=cancel_check,
        )
    except Exception:
        return []


def extract_facebook_metrics(
    page,
    post_url: str,
    raw_date: str,
    date_obj: Optional[datetime],
    post_type: str,
    log_hook: Optional[Callable] = None,
) -> Any:
    """
    Extract metrics from a loaded Facebook post.
    
    Args:
        page: Playwright page with post loaded
        post_url: URL of the post
        raw_date: Raw date string from page
        date_obj: Parsed datetime object
        post_type: Type of post
        log_hook: Optional logging callback
    
    Returns:
        PostData object with extracted metrics
    """
    try:
        return legacy_scraper.extract_metrics_from_loaded_post(
            page,
            post_url,
            raw_date,
            date_obj,
            post_type,
            log_hook=log_hook,
        )
    except Exception:
        return None


def scrape_facebook(
    target_url: str,
    load_rounds: int,
    start_date: datetime,
    end_date: Optional[datetime],
    output_file: str,
    collection_type: str = "posts_only",
    log_hook: Optional[Callable] = None,
    progress_hook: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
) -> dict[str, Any]:
    """
    Run full Facebook scrape job.
    
    This is a high-level orchestration function that coordinates
    browser setup, link collection, metric extraction, and export.
    
    Args:
        target_url: Facebook page/profile URL
        load_rounds: Number of load rounds
        start_date: Start date for collection
        end_date: End date (or None for latest)
        output_file: Output Excel filename
        collection_type: "posts_only" or "posts_with_comments"
        log_hook: Optional logging callback
        progress_hook: Optional progress callback
        cancel_check: Optional cancellation callback
    
    Returns:
        Dictionary with job results (success, error, output_file, etc.)
    """
    # This will be orchestrated by app_fb.py for now
    # Enhanced with core modules in future
    return {
        "success": False,
        "error": "Use app_fb.py for orchestration",
    }
