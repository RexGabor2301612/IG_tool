from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

from .base import PlatformAdapter, PlatformConfig

import facebook_to_excel as fb


class FacebookAdapter(PlatformAdapter):
    key = "facebook"
    name = "Facebook"

    def __init__(self) -> None:
        self._target_url: str = ""

    def platform_config(self) -> PlatformConfig:
        return PlatformConfig(
            key=self.key,
            name=self.name,
            workspace_subtitle="Facebook extraction workspace",
            hero_title="Facebook Extraction Workspace",
            hero_text=(
                "Enter a public Facebook page, profile, or post link. The system will open a real browser session "
                "and wait for manual login/verification before scraping."
            ),
            link_label="Facebook link",
            link_placeholder="https://www.facebook.com/...",
            rounds_label="Load rounds",
            progress_card_label="Load Progress",
            current_item_label="Current Item",
            browser_session_title="Manual Login & GO Signal",
            browser_session_description=(
                "Click Run / Start to open Chromium. Log in there if Facebook asks, then return here and click GO / Start Extraction."
            ),
            activity_logs_title="Facebook Activity Logs",
            depth_tag_label="Depth",
            latest_mode_label="Collect from start date up to latest visible content",
            default_output_file="facebook_extract.xlsx",
            default_latest_mode=True,
            collection_type_enabled=True,
            collection_type_label="Collection type",
            collection_type_options=[
                {"value": "posts_only", "label": "Posts only"},
                {"value": "posts_with_comments", "label": "Posts with visible comments"},
            ],
            api_base="/facebook/api",
            ws_path="/facebook/ws/dashboard",
            link_payload_key="facebookLink",
        )

    def normalize_url(self, raw_value: str) -> Optional[str]:
        return fb.normalize_facebook_target_url(raw_value)
    def set_target_url(self, target_url: str) -> None:
        self._target_url = target_url or ""

    def format_date_coverage(self, start_date: datetime, end_date: Optional[datetime]) -> str:
        return fb.format_date_coverage(start_date, end_date)

    def launch_browser(self):
        return fb.launch_browser

    def open_login_form(self, page, target_url: str) -> None:
        login_url = fb.manual_login_url(target_url)
        if "/login" in (page.url or ""):
            return
        page.goto(login_url, wait_until="domcontentloaded", timeout=fb.POST_GOTO_TIMEOUT)

    def detect_login_gate(self, page) -> tuple[bool, str]:
        return fb.detect_login_gate(page)

    def detect_verification_gate(self, page) -> tuple[bool, str]:
        return fb.detect_checkpoint_or_verification(page)

    def page_ready_for_collection(self, page) -> bool:
        return fb.page_ready_for_collection(page)

    def auto_login_if_needed(self, page, context, target_url: str, log_hook: Optional[Callable[[str, str, str], None]] = None) -> bool:
        return fb.auto_login_if_needed(page, context, target_url, log_hook=log_hook)

    def save_storage_state(self, context, log_hook: Optional[Callable[[str, str, str], None]] = None) -> None:
        fb.save_storage_state(context, log_hook=log_hook)

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
        return fb.collect_post_links(
            page,
            scroll_rounds=scroll_rounds,
            log_hook=log_hook,
            progress_hook=progress_hook,
            cancel_check=cancel_check,
            diagnostics=diagnostics,
        )

    def extract_post(self, page, url: str, collection_type: Optional[str], log_hook: Optional[Callable[[str, str, str], None]] = None) -> Any:
        feed_first = fb.extract_post_from_feed(page, url, collection_type or "posts_only", log_hook=log_hook)
        if feed_first is not None:
            return feed_first

        raw_date, date_obj, post_type, scope_snapshot = fb.open_post_for_extraction(page, url, log_hook=log_hook)
        post_data = fb.extract_metrics_from_loaded_post(
            page,
            url,
            raw_date,
            date_obj,
            post_type,
            collection_type or "posts_only",
            log_hook=log_hook,
            scope_snapshot=scope_snapshot,
        )
        if self._target_url:
            try:
                page.goto(self._target_url, wait_until="domcontentloaded", timeout=fb.POST_GOTO_TIMEOUT)
                page.wait_for_timeout(800)
            except Exception:
                pass
        return post_data

    def post_to_record(self, post: Any) -> dict[str, Any]:
        if isinstance(post, dict):
            post_date = post.get("post_date_obj") or datetime.now()
            return {
                "url": post.get("post_link", ""),
                "timestamp": post_date.isoformat() if hasattr(post_date, "isoformat") else datetime.now().isoformat(),
                "likes": int(post.get("reactions") or 0) if str(post.get("reactions")) != "N/A" else 0,
                "comments": int(post.get("comments_count") or 0) if str(post.get("comments_count")) != "N/A" else 0,
                "shares": int(post.get("shares") or 0) if str(post.get("shares")) != "N/A" else 0,
                "text_preview": post.get("post_type", "") or "",
            }

        return {
            "url": getattr(post, "url", ""),
            "timestamp": (getattr(post, "post_date_obj", None) or datetime.now()).isoformat(),
            "likes": int(getattr(post, "reactions", 0) or 0),
            "comments": int(getattr(post, "comments_count", 0) or 0),
            "shares": int(getattr(post, "shares", 0) or 0),
            "text_preview": getattr(post, "post_type", "") or "",
        }

    def export_excel(self, posts: list[Any], output_file: str, coverage_label: str, collection_type: Optional[str]) -> None:
        fb.save_facebook_excel(posts, output_file, coverage_label, collection_type or "posts_only")

    def set_run_diagnostics(self, payload: dict[str, Any]) -> None:
        fb.set_run_diagnostics(payload)

    def uses_local_browser_window(self) -> bool:
        return fb.uses_local_browser_window()
