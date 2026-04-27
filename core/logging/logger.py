"""Production-grade logging system with real-time streaming."""

import json
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Callable


class LogLevel(Enum):
    """Log levels for categorization."""
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARN = "WARN"
    ERROR = "ERROR"


@dataclass
class LogEntry:
    """Single log entry with timestamp, level, action, and details."""
    timestamp: str  # ISO format
    level: str
    action: str
    details: str
    
    def to_dict(self):
        return asdict(self)
    
    def to_json(self):
        return json.dumps(self.to_dict())


class ProductionLogger:
    """
    Thread-safe logger with real-time streaming to WebSocket clients.
    
    Features:
    - Structured log entries (time, level, action, details)
    - Real-time streaming callbacks
    - Persistent SQLite storage
    - Thread-safe operations
    - Deduplication of repeat messages
    """
    
    def __init__(self, max_buffer: int = 1000, persistence_dir: Optional[Path] = None):
        """Initialize logger.
        
        Args:
            max_buffer: Maximum logs to keep in memory
            persistence_dir: Directory to store logs (SQLite)
        """
        self.max_buffer: int = max_buffer
        self.buffer: List[LogEntry] = []
        self.lock = threading.RLock()
        self.streaming_callbacks: List[Callable[[LogEntry], None]] = []
        self.persistence_dir = persistence_dir
        
        # Dedup tracking: (action, details) → last_time
        self.last_seen: dict = {}
        self.dedup_window: float = 0.5  # seconds
        
        if persistence_dir:
            persistence_dir.mkdir(parents=True, exist_ok=True)
            self._init_sqlite()
    
    def _init_sqlite(self):
        """Initialize SQLite persistence."""
        import sqlite3
        db_path = self.persistence_dir / "logs.db"
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Failed to init SQLite: {e}")
    
    def _persist_log(self, entry: LogEntry):
        """Persist log entry to SQLite."""
        if not self.persistence_dir:
            return
        
        try:
            import sqlite3
            db_path = self.persistence_dir / "logs.db"
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO logs (timestamp, level, action, details)
                   VALUES (?, ?, ?, ?)""",
                (entry.timestamp, entry.level, entry.action, entry.details)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Failed to persist log: {e}")
    
    def _should_deduplicate(self, action: str, details: str) -> bool:
        """Check if entry should be dedup'd based on recent history."""
        key = (action, details)
        now = time.time()
        
        if key in self.last_seen:
            if now - self.last_seen[key] < self.dedup_window:
                return True
        
        self.last_seen[key] = now
        return False
    
    def log(self, level: LogLevel, action: str, details: str) -> LogEntry:
        """Log an entry and stream to all subscribers.
        
        Args:
            level: LogLevel (INFO, SUCCESS, WARN, ERROR)
            action: Short action description
            details: Full details
            
        Returns:
            LogEntry that was created
        """
        if self._should_deduplicate(action, details):
            return None  # Deduplicated
        
        entry = LogEntry(
            timestamp=datetime.utcnow().isoformat() + "Z",
            level=level.value,
            action=action,
            details=details
        )
        
        with self.lock:
            self.buffer.append(entry)
            if len(self.buffer) > self.max_buffer:
                self.buffer.pop(0)
        
        # Persist
        self._persist_log(entry)
        
        # Stream to subscribers
        self._broadcast_log(entry)
        
        return entry
    
    def info(self, action: str, details: str) -> LogEntry:
        """Log INFO level."""
        return self.log(LogLevel.INFO, action, details)
    
    def success(self, action: str, details: str) -> LogEntry:
        """Log SUCCESS level."""
        return self.log(LogLevel.SUCCESS, action, details)
    
    def warn(self, action: str, details: str) -> LogEntry:
        """Log WARN level."""
        return self.log(LogLevel.WARN, action, details)
    
    def error(self, action: str, details: str) -> LogEntry:
        """Log ERROR level."""
        return self.log(LogLevel.ERROR, action, details)
    
    def _broadcast_log(self, entry: LogEntry):
        """Send log to all streaming subscribers."""
        with self.lock:
            for callback in self.streaming_callbacks:
                try:
                    callback(entry)
                except Exception as e:
                    print(f"Streaming callback error: {e}")
    
    def subscribe(self, callback: Callable[[LogEntry], None]):
        """Subscribe to log stream.
        
        Args:
            callback: Function(LogEntry) called for each new log
        """
        with self.lock:
            self.streaming_callbacks.append(callback)
    
    def unsubscribe(self, callback: Callable[[LogEntry], None]):
        """Unsubscribe from log stream."""
        with self.lock:
            if callback in self.streaming_callbacks:
                self.streaming_callbacks.remove(callback)
    
    def get_recent(self, count: int = 100) -> List[LogEntry]:
        """Get last N log entries."""
        with self.lock:
            return self.buffer[-count:]
    
    def clear(self):
        """Clear buffer (not persistent logs)."""
        with self.lock:
            self.buffer.clear()
    
    def get_summary(self) -> dict:
        """Get log statistics."""
        with self.lock:
            total = len(self.buffer)
            levels = {}
            for entry in self.buffer:
                levels[entry.level] = levels.get(entry.level, 0) + 1
            
            return {
                "total": total,
                "by_level": levels,
                "oldest": self.buffer[0].timestamp if self.buffer else None,
                "newest": self.buffer[-1].timestamp if self.buffer else None,
            }
