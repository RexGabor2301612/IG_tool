"""ETL pipeline with Pandas, SQLite, deduplication, and incremental save."""

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict

import pandas as pd


class ExportFormat(Enum):
    """Export formats."""
    EXCEL = "xlsx"
    CSV = "csv"
    JSON = "json"


@dataclass
class DataBuffer:
    """In-memory buffer for posts before persistence."""
    
    posts: List[Dict] = None
    max_size: int = 1000
    lock: threading.Lock = None
    
    def __post_init__(self):
        if self.posts is None:
            self.posts = []
        if self.lock is None:
            self.lock = threading.Lock()
    
    def add(self, post: Dict) -> bool:
        """Add post to buffer.
        
        Args:
            post: Post data dict
            
        Returns:
            True if added, False if buffer full
        """
        with self.lock:
            if len(self.posts) >= self.max_size:
                return False
            self.posts.append(post)
            return True
    
    def flush(self) -> List[Dict]:
        """Get all posts and clear buffer.
        
        Returns:
            List of posts
        """
        with self.lock:
            posts = self.posts.copy()
            self.posts.clear()
            return posts
    
    def size(self) -> int:
        """Get current buffer size."""
        with self.lock:
            return len(self.posts)


class ETLPipeline:
    """
    ETL pipeline with:
    - Incremental per-post persistence
    - Full deduplication
    - Data validation
    - Pandas processing
    - Excel/CSV/JSON export
    """
    
    def __init__(self, output_dir: Path, platform: str):
        """Initialize ETL pipeline.
        
        Args:
            output_dir: Directory for output files
            platform: "instagram" or "facebook"
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.platform = platform
        self.db_path = self.output_dir / f"{platform}_posts.db"
        self.lock = threading.RLock()
        self.dedup_set = set()  # URLs seen
        
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database."""
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    timestamp TEXT NOT NULL,
                    likes INTEGER DEFAULT 0,
                    comments INTEGER DEFAULT 0,
                    shares INTEGER DEFAULT 0,
                    text_preview TEXT,
                    platform TEXT,
                    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Load existing URLs for dedup
            cursor.execute("SELECT url FROM posts")
            for row in cursor.fetchall():
                self.dedup_set.add(row[0])
            
            conn.commit()
            conn.close()
        
        except Exception as e:
            print(f"Failed to init DB: {e}")
    
    def save_post(self, post_data: Dict) -> tuple[bool, Optional[str]]:
        """Incrementally save single post to SQLite.
        
        Args:
            post_data: Post dict with url, timestamp, likes, comments, shares
            
        Returns:
            (success: bool, error: Optional[str])
        """
        # Validate
        if not self._validate_post(post_data):
            return False, "Invalid post data"
        
        url = post_data.get("url", "")
        
        # Deduplicate
        with self.lock:
            if url in self.dedup_set:
                return False, "Duplicate URL"
            
            self.dedup_set.add(url)
        
        # Insert
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO posts (url, timestamp, likes, comments, shares, text_preview, platform)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                post_data.get("url", ""),
                post_data.get("timestamp", ""),
                post_data.get("likes", 0),
                post_data.get("comments", 0),
                post_data.get("shares", 0),
                post_data.get("text_preview", ""),
                self.platform,
            ))
            
            conn.commit()
            conn.close()
            
            return True, None
        
        except sqlite3.IntegrityError:
            return False, "Duplicate URL"
        except Exception as e:
            return False, str(e)
    
    def _validate_post(self, post_data: Dict) -> bool:
        """Validate post data.
        
        Args:
            post_data: Post dict
            
        Returns:
            True if valid
        """
        required = {"url", "timestamp"}
        if not required.issubset(post_data.keys()):
            return False
        
        # Check types
        if not isinstance(post_data.get("likes", 0), (int, float)):
            return False
        if not isinstance(post_data.get("comments", 0), (int, float)):
            return False
        if not isinstance(post_data.get("shares", 0), (int, float)):
            return False
        
        return True
    
    def export_excel(self, output_file: Optional[Path] = None) -> tuple[bool, str]:
        """Export all posts to Excel.
        
        Args:
            output_file: Output file path (default: output_dir/{platform}_posts.xlsx)
            
        Returns:
            (success: bool, file_path: str)
        """
        if not output_file:
            output_file = self.output_dir / f"{self.platform}_posts.xlsx"
        
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            df = pd.read_sql_query("SELECT * FROM posts ORDER BY imported_at DESC", conn)
            conn.close()
            
            if df.empty:
                return False, "No data to export"
            
            # Clean data
            df["likes"] = df["likes"].fillna(0).astype(int)
            df["comments"] = df["comments"].fillna(0).astype(int)
            df["shares"] = df["shares"].fillna(0).astype(int)
            df["text_preview"] = df["text_preview"].fillna("N/A")
            
            # Write
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
                df.to_excel(writer, index=False)
            
            return True, str(output_file)
        
        except Exception as e:
            return False, str(e)
    
    def export_csv(self, output_file: Optional[Path] = None) -> tuple[bool, str]:
        """Export all posts to CSV.
        
        Args:
            output_file: Output file path (default: output_dir/{platform}_posts.csv)
            
        Returns:
            (success: bool, file_path: str)
        """
        if not output_file:
            output_file = self.output_dir / f"{self.platform}_posts.csv"
        
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            df = pd.read_sql_query("SELECT * FROM posts ORDER BY imported_at DESC", conn)
            conn.close()
            
            if df.empty:
                return False, "No data to export"
            
            # Clean data
            df["likes"] = df["likes"].fillna(0).astype(int)
            df["comments"] = df["comments"].fillna(0).astype(int)
            df["shares"] = df["shares"].fillna(0).astype(int)
            df["text_preview"] = df["text_preview"].fillna("N/A")
            
            # Write
            output_file.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(output_file, index=False)
            
            return True, str(output_file)
        
        except Exception as e:
            return False, str(e)
    
    def get_stats(self) -> Dict:
        """Get data statistics.
        
        Returns:
            Dict with total posts, likes sum, comments sum, etc.
        """
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*), SUM(likes), SUM(comments), SUM(shares) FROM posts")
            row = cursor.fetchone()
            conn.close()
            
            if not row[0]:
                return {
                    "total_posts": 0,
                    "total_likes": 0,
                    "total_comments": 0,
                    "total_shares": 0,
                }
            
            return {
                "total_posts": row[0] or 0,
                "total_likes": row[1] or 0,
                "total_comments": row[2] or 0,
                "total_shares": row[3] or 0,
            }
        
        except Exception as e:
            print(f"Stats error: {e}")
            return {}
    
    def add_post(self, post, url: str = None) -> bool:
        """Add a single post during scraping (incremental buffer).
        
        Args:
            post: Post object (instagram_to_excel PostData or similar)
            url: URL (can override post.url if needed)
            
        Returns:
            True if added, False if error
        """
        try:
            post_dict = {
                "url": url or getattr(post, "url", ""),
                "timestamp": getattr(post, "post_date_obj", datetime.now()).isoformat() if hasattr(post, "post_date_obj") else datetime.now().isoformat(),
                "likes": getattr(post, "likes", 0) or 0,
                "comments": getattr(post, "comments", 0) or 0,
                "shares": getattr(post, "shares", 0) or 0,
            }
            success, error = self.save_post(post_dict)
            return success
        except Exception:
            return False
    
    def process(self, posts: List, output_file: str, coverage_label: str = "", platform: str = "instagram") -> Dict:
        """Process a list of posts: deduplicate, validate, export to Excel.
        
        Args:
            posts: List of post objects
            output_file: Output Excel file path
            coverage_label: Date coverage label for metadata
            platform: "instagram" or "facebook"
            
        Returns:
            Dict with success, posts_processed, duplicates_removed, error
        """
        try:
            duplicates = 0
            processed = 0
            
            # Process each post
            for post in posts:
                try:
                    url = getattr(post, "url", "")
                    timestamp = getattr(post, "post_date_obj", datetime.now())
                    if isinstance(timestamp, str):
                        timestamp = datetime.fromisoformat(timestamp)
                    
                    post_dict = {
                        "url": url,
                        "timestamp": timestamp.isoformat(),
                        "likes": int(getattr(post, "likes", 0) or 0),
                        "comments": int(getattr(post, "comments", 0) or 0),
                        "shares": int(getattr(post, "shares", 0) or 0),
                        "text_preview": getattr(post, "notes", "")[:100],
                    }
                    
                    success, error = self.save_post(post_dict)
                    if success:
                        processed += 1
                    else:
                        if "Duplicate" in (error or ""):
                            duplicates += 1
                except Exception:
                    continue
            
            # Export to Excel
            success, file_path = self.export_excel(Path(output_file))
            
            if success:
                return {
                    "success": True,
                    "posts_processed": processed,
                    "duplicates_removed": duplicates,
                    "output_file": file_path,
                    "error": None,
                }
            else:
                return {
                    "success": False,
                    "posts_processed": processed,
                    "duplicates_removed": duplicates,
                    "error": file_path,
                }
        
        except Exception as e:
            return {
                "success": False,
                "posts_processed": 0,
                "duplicates_removed": 0,
                "error": str(e),
            }
    
    def clear(self):
        """Clear all data."""
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM posts")
            conn.commit()
            conn.close()
            
            with self.lock:
                self.dedup_set.clear()
        
        except Exception as e:
            print(f"Failed to clear: {e}")
