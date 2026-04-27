"""
Instagram scraper - unified interface for Instagram content extraction.

This module wraps the existing instagram_to_excel logic and provides
a clean interface for integration with the new core modules.
"""

from typing import Optional, Callable, Any
from datetime import datetime
from pathlib import Path

# Import the existing Instagram scraper module for now
import instagram_to_excel as legacy_scraper


def detect_instagram_ready(page) -> tuple[bool, str]:
    """
    Detect if Instagram profile is ready for extraction.
    
    Uses the existing instagram_strong_ready_signal from the legacy module.
    """
    return legacy_scraper.instagram_strong_ready_signal(page)


def collect_instagram_posts(
    page,
    scroll_rounds: int,
    start_date: datetime,
    log_hook: Optional[Callable] = None,
    progress_hook: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
) -> list[str]:
    """
    Collect Instagram post links through scrolling.
    
    Args:
        page: Playwright page object
        scroll_rounds: Number of scroll rounds
        start_date: Target start date for collection
        log_hook: Optional logging callback
        progress_hook: Optional progress callback
        cancel_check: Optional cancellation check callback
    
    Returns:
        List of post URLs collected
    """
    return legacy_scraper.collect_post_links(
        page,
        max_posts=None,
        scroll_rounds=scroll_rounds,
        target_start_date=start_date,
        log_hook=log_hook,
        progress_hook=progress_hook,
        cancel_check=cancel_check,
    )


def extract_instagram_metrics(
    page,
    post_url: str,
    raw_date: str,
    date_obj: Optional[datetime],
    post_type: str,
    log_hook: Optional[Callable] = None,
) -> Any:
    """
    Extract metrics from a loaded Instagram post.
    
    Args:
        page: Playwright page with post loaded
        post_url: URL of the post
        raw_date: Raw date string from page
        date_obj: Parsed datetime object
        post_type: Type of post (image, video, carousel, reel)
        log_hook: Optional logging callback
    
    Returns:
        PostData object with extracted metrics
    """
    return legacy_scraper.extract_metrics_from_loaded_post(
        page,
        post_url,
        raw_date,
        date_obj,
        post_type,
        log_hook=log_hook,
    )


def scrape_instagram(
    profile_url: str,
    scroll_rounds: int,
    start_date: datetime,
    end_date: Optional[datetime],
    output_file: str,
    log_hook: Optional[Callable] = None,
    progress_hook: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
) -> dict[str, Any]:
    """
    Run full Instagram scrape job.
    
    This is a high-level orchestration function that coordinates
    browser setup, link collection, metric extraction, and export.
    
    Args:
        profile_url: Instagram profile URL
        scroll_rounds: Number of scroll rounds
        start_date: Start date for collection
        end_date: End date (or None for latest)
        output_file: Output Excel filename
        log_hook: Optional logging callback
        progress_hook: Optional progress callback
        cancel_check: Optional cancellation callback
    
    Returns:
        Dictionary with job results (success, error, output_file, etc.)
    """
    # This will be orchestrated by the main app.py for now
    # Enhanced with core modules in future
    return {
        "success": False,
        "error": "Use main app.py for orchestration",
    }
