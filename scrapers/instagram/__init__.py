"""Instagram scraper module."""
from .scraper import (
    scrape_instagram,
    detect_instagram_ready,
    collect_instagram_posts,
    extract_instagram_metrics,
)

__all__ = [
    "scrape_instagram",
    "detect_instagram_ready",
    "collect_instagram_posts",
    "extract_instagram_metrics",
]
