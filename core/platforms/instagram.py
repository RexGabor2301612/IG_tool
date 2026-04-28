from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

from .base import PlatformAdapter, PlatformConfig

import instagram_to_excel as ig


class InstagramAdapter(PlatformAdapter):
    key = "instagram"
    name = "Instagram"

    def platform_config(self) -> PlatformConfig:
        return PlatformConfig(
            key=self.key,
            name=self.name,
            workspace_subtitle="Instagram extraction workspace",
            hero_title="Instagram Extraction Workspace",
            hero_text=(
                "Enter a profile link, choose date coverage and scroll depth, then open the browser session "
                "to collect visible public metrics into Excel."
            ),
            link_label="Instagram profile link",
            link_placeholder="https://www.instagram.com/username/",
            rounds_label="Scroll rounds",
            progress_card_label="Profile Scrolled",
            current_item_label="Current Post",
            browser_session_title="Manual Login & GO Signal",
            browser_session_description=(
                "Click Run / Start to open Chromium. Log in there if Instagram asks, then return here and click GO / Start Extraction."
            ),
            activity_logs_title="Instagram Activity Logs",
            depth_tag_label="Max Scroll",
            latest_mode_label="Collect from start date up to latest post",
            default_output_file="instagram_extract.xlsx",
            default_latest_mode=True,
            collection_type_enabled=False,
            collection_type_label="",
            collection_type_options=[],
            api_base="",
            ws_path="/ws/dashboard",
            link_payload_key="instagramLink",
        )

    def normalize_url(self, raw_value: str) -> Optional[str]:
        return ig.normalize_instagram_profile_url(raw_value)

    def format_date_coverage(self, start_date: datetime, end_date: Optional[datetime]) -> str:
        return ig.format_date_coverage(start_date, end_date)

    def launch_browser(self):
        return ig.launch_browser

    def open_login_form(self, page, target_url: str) -> None:
        login_url = "https://www.instagram.com/accounts/login/"
        if "accounts/login" in (page.url or ""):
            return
        page.goto(login_url, wait_until="domcontentloaded", timeout=ig.POST_GOTO_TIMEOUT)

    def detect_login_gate(self, page) -> tuple[bool, str]:
        if ig.wait_for_selector(page, ig.LOGIN_FORM_SELECTOR, 300):
            return True, "Instagram login form detected."
        try:
            body_text = page.locator("body").inner_text(timeout=500).lower()
        except Exception:
            body_text = ""
        if "log in to continue" in body_text or "sign up" in body_text:
            return True, "Instagram login wall detected."
        return False, ""

    def detect_verification_gate(self, page) -> tuple[bool, str]:
        url = ""
        try:
            url = page.url or ""
        except Exception:
            url = ""
        if any(token in url for token in ["/challenge/", "/two_factor", "/checkpoint/", "/security/"]):
            return True, "Instagram verification checkpoint detected."
        try:
            body_text = page.locator("body").inner_text(timeout=600).lower()
        except Exception:
            body_text = ""
        phrases = [
            "confirm your identity",
            "security check",
            "suspicious login",
            "verify your account",
            "enter the code",
        ]
        if any(phrase in body_text for phrase in phrases):
            return True, "Instagram verification required before continuing."
        return False, ""

    def page_ready_for_collection(self, page) -> bool:
        return ig.profile_ready_for_collection(page)

    def auto_login_if_needed(self, page, context, target_url: str, log_hook: Optional[Callable[[str, str, str], None]] = None) -> bool:
        return ig.auto_login_if_needed(page, context, target_url, log_hook=log_hook)

    def save_storage_state(self, context, log_hook: Optional[Callable[[str, str, str], None]] = None) -> None:
        ig.save_storage_state(context, log_hook=log_hook)

    def collect_post_links(
        self,
        page,
        scroll_rounds: int,
        start_date: datetime,
        log_hook: Optional[Callable[[str, str, str], None]] = None,
        progress_hook: Optional[Callable[[int, int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        diagnostics: Optional[dict[str, Any]] = None,
    ) -> list[str]:
        return ig.collect_post_links(
            page,
            max_posts=None,
            scroll_rounds=scroll_rounds,
            target_start_date=start_date,
            log_hook=log_hook,
            progress_hook=progress_hook,
            cancel_check=cancel_check,
            diagnostics=diagnostics,
        )

    def extract_post(self, page, url: str, collection_type: Optional[str], log_hook: Optional[Callable[[str, str, str], None]] = None) -> Any:
        return ig.extract_post_data(page, url, log_hook=log_hook)

    def post_to_record(self, post: Any) -> dict[str, Any]:
        return {
            "url": getattr(post, "url", ""),
            "timestamp": (getattr(post, "post_date_obj", None) or datetime.now()).isoformat(),
            "likes": int(getattr(post, "likes", 0) or 0),
            "comments": int(getattr(post, "comments", 0) or 0),
            "shares": int(getattr(post, "shares", 0) or 0),
            "text_preview": getattr(post, "post_type", "") or "",
        }

    def export_excel(self, posts: list[Any], output_file: str, coverage_label: str, collection_type: Optional[str]) -> None:
        ig.save_grouped_excel(posts, output_file, coverage_label)

    def uses_local_browser_window(self) -> bool:
        return ig.uses_local_browser_window()
