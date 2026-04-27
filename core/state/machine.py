"""Formalized state machine for scraping jobs."""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple
from datetime import datetime


class ScrapeState(Enum):
    """Canonical states for scraping workflow."""
    
    # Setup phase
    SETUP = "setup"
    VALIDATION = "validation"
    
    # Browser & session phase
    BROWSER_INIT = "browser_init"
    SESSION_LOADING = "session_loading"
    
    # Login & verification phase
    WAITING_LOGIN = "waiting_login"
    CAPTCHA_DETECTED = "captcha_detected"
    WAITING_VERIFICATION = "waiting_verification"
    
    # Readiness phase
    PAGE_READINESS_CHECK = "page_readiness_check"
    PAGE_READY = "page_ready"
    
    # User confirmation
    WAITING_USER_CONFIRM = "waiting_user_confirm"
    
    # Collection phase
    COLLECTION_RUNNING = "collection_running"
    COLLECTION_PAUSED = "collection_paused"
    
    # Completion
    COLLECTION_COMPLETED = "collection_completed"
    COLLECTION_FAILED = "collection_failed"
    COLLECTION_CANCELLED = "collection_cancelled"


class StateTransition:
    """Defines valid state transitions and guards."""
    
    # Valid transitions: from_state -> list of valid to_states
    VALID_TRANSITIONS: Dict[ScrapeState, list] = {
        ScrapeState.SETUP: [ScrapeState.VALIDATION],
        ScrapeState.VALIDATION: [ScrapeState.BROWSER_INIT, ScrapeState.VALIDATION],
        ScrapeState.BROWSER_INIT: [ScrapeState.SESSION_LOADING],
        ScrapeState.SESSION_LOADING: [ScrapeState.WAITING_LOGIN, ScrapeState.PAGE_READINESS_CHECK],
        ScrapeState.WAITING_LOGIN: [ScrapeState.PAGE_READINESS_CHECK, ScrapeState.CAPTCHA_DETECTED],
        ScrapeState.CAPTCHA_DETECTED: [ScrapeState.WAITING_VERIFICATION, ScrapeState.COLLECTION_FAILED],
        ScrapeState.WAITING_VERIFICATION: [ScrapeState.PAGE_READINESS_CHECK, ScrapeState.CAPTCHA_DETECTED],
        ScrapeState.PAGE_READINESS_CHECK: [ScrapeState.PAGE_READY, ScrapeState.WAITING_LOGIN],
        ScrapeState.PAGE_READY: [ScrapeState.WAITING_USER_CONFIRM],
        ScrapeState.WAITING_USER_CONFIRM: [ScrapeState.COLLECTION_RUNNING, ScrapeState.COLLECTION_CANCELLED],
        ScrapeState.COLLECTION_RUNNING: [
            ScrapeState.COLLECTION_PAUSED,
            ScrapeState.COLLECTION_COMPLETED,
            ScrapeState.COLLECTION_FAILED,
            ScrapeState.COLLECTION_CANCELLED,
        ],
        ScrapeState.COLLECTION_PAUSED: [ScrapeState.COLLECTION_RUNNING, ScrapeState.COLLECTION_CANCELLED],
    }
    
    # Terminal states (no further transitions)
    TERMINAL_STATES = {
        ScrapeState.COLLECTION_COMPLETED,
        ScrapeState.COLLECTION_FAILED,
        ScrapeState.COLLECTION_CANCELLED,
    }
    
    @classmethod
    def is_valid(cls, from_state: ScrapeState, to_state: ScrapeState) -> bool:
        """Check if transition is valid."""
        if from_state not in cls.VALID_TRANSITIONS:
            return False
        return to_state in cls.VALID_TRANSITIONS[from_state]
    
    @classmethod
    def is_terminal(cls, state: ScrapeState) -> bool:
        """Check if state is terminal (no transitions)."""
        return state in cls.TERMINAL_STATES
    
    @classmethod
    def can_transition_from(cls, state: ScrapeState) -> bool:
        """Check if state can transition to another."""
        return state not in cls.TERMINAL_STATES


@dataclass
class ScrapeJobState:
    """Thread-safe state container for a scrape job."""
    
    # Core state
    current_state: ScrapeState = ScrapeState.SETUP
    
    # Lifecycle timestamps
    created_at: float = field(default_factory=time.time)
    state_entered_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    
    # Browser & session info
    browser_id: str = ""
    session_file: str = ""
    session_loaded: bool = False
    session_valid: bool = False
    
    # Login & verification
    login_required: bool = True
    login_attempted: int = 0
    login_failed: int = 0
    verification_required: bool = False
    captcha_detected: bool = False
    captcha_attempts: int = 0
    
    # Page readiness
    page_ready: bool = False
    posts_loaded: int = 0
    
    # Collection progress
    total_posts_found: int = 0
    posts_extracted: int = 0
    scroll_rounds: int = 0
    extraction_errors: int = 0
    
    # Control signals
    user_confirmed_go: bool = False
    cancel_requested: bool = False
    pause_requested: bool = False
    
    # Event-backed handoff
    go_event: threading.Event = field(default_factory=threading.Event)
    
    # Thread safety
    lock: threading.RLock = field(default_factory=threading.RLock)
    
    def __post_init__(self):
        self.state_entered_at = time.time()
        self.updated_at = time.time()
    
    def transition_to(self, new_state: ScrapeState, reason: str = "") -> Tuple[bool, str]:
        """Attempt state transition with validation.
        
        Args:
            new_state: Target state
            reason: Reason for transition (for logging)
            
        Returns:
            (success: bool, message: str)
        """
        with self.lock:
            # Check if valid transition
            if not StateTransition.is_valid(self.current_state, new_state):
                msg = f"Invalid transition {self.current_state.value} → {new_state.value}"
                return False, msg
            
            # Prevent transitions from terminal states
            if StateTransition.is_terminal(self.current_state):
                msg = f"Cannot transition from terminal state {self.current_state.value}"
                return False, msg
            
            # Perform transition
            prev_state = self.current_state
            self.current_state = new_state
            self.state_entered_at = time.time()
            self.updated_at = time.time()
            
            msg = f"{prev_state.value} → {new_state.value}"
            if reason:
                msg += f" ({reason})"
            
            return True, msg
    
    def request_go(self) -> Tuple[bool, str]:
        """Request GO signal (user confirmation).
        
        Returns:
            (success: bool, reason: str)
        """
        with self.lock:
            if self.current_state != ScrapeState.PAGE_READY:
                return False, f"System not ready (state={self.current_state.value})"
            
            if self.cancel_requested:
                return False, "Cancellation already requested"
            
            self.user_confirmed_go = True
            self.go_event.set()
            self.updated_at = time.time()
            
            return True, "GO signal accepted"
    
    def wait_for_go(self, timeout: Optional[float] = None) -> bool:
        """Wait for GO signal from user.
        
        Args:
            timeout: Max seconds to wait
            
        Returns:
            True if GO received, False if timeout/cancelled
        """
        return self.go_event.wait(timeout=timeout)
    
    def request_cancel(self) -> Tuple[bool, str]:
        """Request cancellation.
        
        Returns:
            (success: bool, reason: str)
        """
        with self.lock:
            if StateTransition.is_terminal(self.current_state):
                return False, "Job already finished"
            
            self.cancel_requested = True
            self.go_event.set()  # Wake up any waiters
            self.updated_at = time.time()
            
            return True, "Cancellation requested"
    
    def request_pause(self) -> Tuple[bool, str]:
        """Request pause during collection.
        
        Returns:
            (success: bool, reason: str)
        """
        with self.lock:
            if self.current_state != ScrapeState.COLLECTION_RUNNING:
                return False, f"Cannot pause from state {self.current_state.value}"
            
            self.pause_requested = True
            self.updated_at = time.time()
            
            return True, "Pause requested"
    
    def resume_collection(self) -> Tuple[bool, str]:
        """Resume paused collection.
        
        Returns:
            (success: bool, reason: str)
        """
        with self.lock:
            if self.current_state != ScrapeState.COLLECTION_PAUSED:
                return False, f"Cannot resume from state {self.current_state.value}"
            
            self.pause_requested = False
            self.updated_at = time.time()
            
            return True, "Resume requested"
    
    def reset(self):
        """Reset job to initial state."""
        with self.lock:
            self.current_state = ScrapeState.SETUP
            self.state_entered_at = time.time()
            self.updated_at = time.time()
            self.created_at = time.time()
            
            self.browser_id = ""
            self.session_file = ""
            self.session_loaded = False
            self.session_valid = False
            
            self.login_required = True
            self.login_attempted = 0
            self.login_failed = 0
            self.verification_required = False
            self.captcha_detected = False
            self.captcha_attempts = 0
            
            self.page_ready = False
            self.posts_loaded = 0
            
            self.total_posts_found = 0
            self.posts_extracted = 0
            self.scroll_rounds = 0
            self.extraction_errors = 0
            
            self.user_confirmed_go = False
            self.cancel_requested = False
            self.pause_requested = False
            
            self.go_event.clear()
    
    def snapshot(self) -> dict:
        """Get current state snapshot for UI."""
        with self.lock:
            return {
                "state": self.current_state.value,
                "state_entered_at": self.state_entered_at,
                "updated_at": self.updated_at,
                "browser_id": self.browser_id,
                "session_loaded": self.session_loaded,
                "login_required": self.login_required,
                "login_attempted": self.login_attempted,
                "verification_required": self.verification_required,
                "captcha_detected": self.captcha_detected,
                "page_ready": self.page_ready,
                "posts_loaded": self.posts_loaded,
                "total_posts_found": self.total_posts_found,
                "posts_extracted": self.posts_extracted,
                "scroll_rounds": self.scroll_rounds,
                "extraction_errors": self.extraction_errors,
                "user_confirmed_go": self.user_confirmed_go,
                "cancel_requested": self.cancel_requested,
            }
