"""High-accuracy data extractor with retry logic."""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Tuple
import re

from playwright.sync_api import Page, Locator


@dataclass
class ExtractionConfig:
    """Configuration for extraction."""
    max_retries: int = 3
    retry_delay: float = 0.5
    normalize_numbers: bool = True
    iso_timestamps: bool = True


@dataclass
class ExtractedPost:
    """Extracted post data."""
    url: str
    timestamp: str  # ISO format
    likes: int
    comments: int
    shares: int
    text_preview: str = ""
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    def is_valid(self) -> bool:
        """Check if extraction is valid."""
        return bool(self.url and self.timestamp and self.likes >= 0)


class DataExtractor:
    """High-accuracy data extractor with automatic retry."""
    
    def __init__(self, config: ExtractionConfig = None):
        self.config = config or ExtractionConfig()
    
    def extract_post(self, page: Page, post_element: Locator, selectors) -> Tuple[Optional[ExtractedPost], str]:
        """Extract post data with retry logic.
        
        Args:
            page: Playwright Page
            post_element: Locator for post element
            selectors: PlatformSelectors instance
            
        Returns:
            (ExtractedPost or None, error_message: str)
        """
        for attempt in range(1, self.config.max_retries + 1):
            try:
                # Extract each field
                url, url_err = self._extract_url(page, post_element, selectors)
                if url_err and attempt < self.config.max_retries:
                    continue
                
                timestamp, ts_err = self._extract_timestamp(page, post_element, selectors)
                if ts_err and attempt < self.config.max_retries:
                    continue
                
                likes, likes_err = self._extract_likes(page, post_element, selectors)
                comments, comm_err = self._extract_comments(page, post_element, selectors)
                shares, shares_err = self._extract_shares(page, post_element, selectors)
                
                # Construct post
                post = ExtractedPost(
                    url=url or "",
                    timestamp=timestamp or "",
                    likes=likes or 0,
                    comments=comments or 0,
                    shares=shares or 0,
                )
                
                if not post.is_valid():
                    if attempt < self.config.max_retries:
                        continue
                    errors = [url_err, ts_err, likes_err, comm_err, shares_err]
                    return None, "; ".join([e for e in errors if e])
                
                return post, ""
            
            except Exception as e:
                if attempt == self.config.max_retries:
                    return None, str(e)
        
        return None, "Max retries exceeded"
    
    def _extract_url(self, page: Page, post_element: Locator, selectors) -> Tuple[Optional[str], Optional[str]]:
        """Extract post URL."""
        try:
            url_element = post_element.locator(selectors.post_url)
            if url_element.count() > 0:
                href = url_element.first.get_attribute("href")
                if href:
                    # Ensure absolute URL
                    if not href.startswith("http"):
                        href = f"{page.url.split('/')[0]}//{page.url.split('//')[1]}{href}"
                    return href, None
            return None, "Post URL not found"
        except Exception as e:
            return None, str(e)
    
    def _extract_timestamp(self, page: Page, post_element: Locator, selectors) -> Tuple[Optional[str], Optional[str]]:
        """Extract and normalize timestamp to ISO format."""
        try:
            ts_element = post_element.locator(selectors.post_timestamp)
            if ts_element.count() > 0:
                timestamp_text = ts_element.first.get_attribute("datetime") or ts_element.first.text_content()
                
                if timestamp_text:
                    # Normalize to ISO
                    iso_ts = self._normalize_timestamp(timestamp_text)
                    if iso_ts:
                        return iso_ts, None
            
            return None, "Timestamp not found"
        except Exception as e:
            return None, str(e)
    
    def _extract_likes(self, page: Page, post_element: Locator, selectors) -> Tuple[Optional[int], Optional[str]]:
        """Extract likes count."""
        try:
            likes_element = post_element.locator(selectors.likes_count)
            if likes_element.count() > 0:
                likes_text = likes_element.first.text_content()
                likes_num = self._normalize_number(likes_text)
                if likes_num is not None:
                    return likes_num, None
            
            return 0, None  # Assume 0 if not found
        except Exception as e:
            return None, str(e)
    
    def _extract_comments(self, page: Page, post_element: Locator, selectors) -> Tuple[Optional[int], Optional[str]]:
        """Extract comments count."""
        try:
            comments_element = post_element.locator(selectors.comments_count)
            if comments_element.count() > 0:
                comments_text = comments_element.first.text_content()
                comments_num = self._normalize_number(comments_text)
                if comments_num is not None:
                    return comments_num, None
            
            return 0, None
        except Exception as e:
            return None, str(e)
    
    def _extract_shares(self, page: Page, post_element: Locator, selectors) -> Tuple[Optional[int], Optional[str]]:
        """Extract shares count."""
        try:
            shares_element = post_element.locator(selectors.shares_count)
            if shares_element.count() > 0:
                shares_text = shares_element.first.text_content()
                shares_num = self._normalize_number(shares_text)
                if shares_num is not None:
                    return shares_num, None
            
            return 0, None
        except Exception as e:
            return None, str(e)
    
    def _normalize_number(self, text: str) -> Optional[int]:
        """Normalize number text (e.g., '1.2k' -> 1200).
        
        Args:
            text: Text with number
            
        Returns:
            Normalized integer or None
        """
        if not text:
            return None
        
        text = text.strip().lower()
        
        # Extract number part
        match = re.search(r"[\d.]+", text)
        if not match:
            return None
        
        num_str = match.group()
        num = float(num_str)
        
        # Apply multiplier if present
        if "k" in text:
            num *= 1000
        elif "m" in text:
            num *= 1_000_000
        elif "b" in text:
            num *= 1_000_000_000
        
        return int(num)
    
    def _normalize_timestamp(self, ts_str: str) -> Optional[str]:
        """Normalize timestamp to ISO format.
        
        Args:
            ts_str: Timestamp string (various formats)
            
        Returns:
            ISO format or None
        """
        if not ts_str:
            return None
        
        # Try common formats
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y, %I:%M %p",
            "%B %d, %Y at %I:%M %p",
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(ts_str.strip(), fmt)
                return dt.isoformat() + "Z"
            except ValueError:
                continue
        
        # If already ISO, return as-is
        if ts_str.endswith("Z") or "T" in ts_str:
            return ts_str
        
        return None
