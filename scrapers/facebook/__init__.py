"""Facebook scraper module."""
from .scraper import (
    scrape_facebook,
    detect_facebook_ready,
    collect_facebook_posts,
    extract_facebook_metrics,
)

__all__ = [
    "scrape_facebook",
    "detect_facebook_ready",
    "collect_facebook_posts",
    "extract_facebook_metrics",
]
