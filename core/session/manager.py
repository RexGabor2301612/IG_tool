"""Persistent Playwright session management system."""

import json
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page


@dataclass
class SessionConfig:
    """Configuration for session."""
    platform: str  # "instagram" or "facebook"
    headless: bool = False
    user_data_dir: Optional[Path] = None
    storage_state_file: Optional[Path] = None
    
    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "headless": self.headless,
            "user_data_dir": str(self.user_data_dir) if self.user_data_dir else None,
            "storage_state_file": str(self.storage_state_file) if self.storage_state_file else None,
        }


class PlaywrightSessionManager:
    """
    Thread-safe Playwright session manager.
    
    Features:
    - Single browser instance per job (no duplicates)
    - Persistent session reuse (storage_state)
    - Automatic session save on close
    - Thread-safe operations
    - Graceful cleanup
    """
    
    def __init__(self, sessions_dir: Path = Path("storage_states")):
        """Initialize session manager.
        
        Args:
            sessions_dir: Directory to store session files
        """
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        
        self.lock = threading.RLock()
        self.active_sessions: dict = {}  # session_id -> {browser, context, page, config}
    
    def create_session(self, config: SessionConfig) -> Tuple[str, Optional[str]]:
        """Create new browser session.
        
        Args:
            config: SessionConfig with platform, headless, etc.
            
        Returns:
            (session_id: str, error: Optional[str])
        """
        session_id = str(uuid.uuid4())[:8]
        
        try:
            playwright = sync_playwright().start()
            
            # Launch browser
            browser = playwright.chromium.launch(
                headless=config.headless,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            
            # Create context (with persistent storage if available)
            context_kwargs = {}
            if config.storage_state_file and config.storage_state_file.exists():
                context_kwargs["storage_state"] = str(config.storage_state_file)
            
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            
            with self.lock:
                self.active_sessions[session_id] = {
                    "playwright": playwright,
                    "browser": browser,
                    "context": context,
                    "page": page,
                    "config": config,
                    "created_at": str(Path.ctime(Path(__file__))),
                }
            
            return session_id, None
        
        except Exception as e:
            return None, str(e)
    
    def get_page(self, session_id: str) -> Optional[Page]:
        """Get page object for session.
        
        Args:
            session_id: Session ID
            
        Returns:
            Page object or None
        """
        with self.lock:
            session = self.active_sessions.get(session_id)
            if session:
                return session["page"]
        return None
    
    def get_context(self, session_id: str) -> Optional[BrowserContext]:
        """Get context object for session.
        
        Args:
            session_id: Session ID
            
        Returns:
            BrowserContext object or None
        """
        with self.lock:
            session = self.active_sessions.get(session_id)
            if session:
                return session["context"]
        return None
    
    def save_session_storage(self, session_id: str) -> Tuple[bool, str]:
        """Save session storage state (cookies, etc.).
        
        Args:
            session_id: Session ID
            
        Returns:
            (success: bool, message: str)
        """
        with self.lock:
            session = self.active_sessions.get(session_id)
            if not session:
                return False, f"Session {session_id} not found"
            
            config = session["config"]
            if not config.storage_state_file:
                return False, "No storage_state_file configured"
            
            try:
                context = session["context"]
                storage_state = context.storage_state()
                
                config.storage_state_file.parent.mkdir(parents=True, exist_ok=True)
                with open(config.storage_state_file, "w") as f:
                    json.dump(storage_state, f, indent=2)
                
                return True, f"Session saved to {config.storage_state_file}"
            
            except Exception as e:
                return False, str(e)
    
    def close_session(self, session_id: str, save_state: bool = True) -> Tuple[bool, str]:
        """Close browser session and cleanup.
        
        Args:
            session_id: Session ID
            save_state: Whether to save storage state before closing
            
        Returns:
            (success: bool, message: str)
        """
        with self.lock:
            session = self.active_sessions.pop(session_id, None)
            if not session:
                return False, f"Session {session_id} not found"
        
        try:
            # Save state if requested
            if save_state:
                config = session["config"]
                if config.storage_state_file:
                    try:
                        storage_state = session["context"].storage_state()
                        config.storage_state_file.parent.mkdir(parents=True, exist_ok=True)
                        with open(config.storage_state_file, "w") as f:
                            json.dump(storage_state, f, indent=2)
                    except Exception as e:
                        print(f"Failed to save state: {e}")
            
            # Close context
            if session.get("context"):
                session["context"].close()
            
            # Close browser
            if session.get("browser"):
                session["browser"].close()
            
            # Stop playwright
            if session.get("playwright"):
                session["playwright"].stop()
            
            return True, f"Session {session_id} closed"
        
        except Exception as e:
            return False, str(e)
    
    def is_session_active(self, session_id: str) -> bool:
        """Check if session is active.
        
        Args:
            session_id: Session ID
            
        Returns:
            True if active
        """
        with self.lock:
            return session_id in self.active_sessions
    
    def list_sessions(self) -> list:
        """List all active session IDs.
        
        Returns:
            List of session IDs
        """
        with self.lock:
            return list(self.active_sessions.keys())
    
    def close_all_sessions(self, save_state: bool = True):
        """Close all active sessions.
        
        Args:
            save_state: Whether to save storage state for each
        """
        session_ids = self.list_sessions()
        for session_id in session_ids:
            self.close_session(session_id, save_state=save_state)
    
    def cleanup_storage_state_file(self, platform: str) -> Tuple[bool, str]:
        """Get or create storage state file path for platform.
        
        Args:
            platform: "instagram" or "facebook"
            
        Returns:
            (Path, error: Optional[str])
        """
        file_path = self.sessions_dir / f"{platform}_auth.json"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path
