from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional


@dataclass
class PlatformConfig:
    key: str
    name: str
    workspace_subtitle: str
    hero_title: str
    hero_text: str
    link_label: str
    link_placeholder: str
    rounds_label: str
    progress_card_label: str
    current_item_label: str
    browser_session_title: str
    browser_session_description: str
    activity_logs_title: str
    depth_tag_label: str
    latest_mode_label: str
    default_output_file: str
    default_latest_mode: bool
    collection_type_enabled: bool
    collection_type_label: str
    collection_type_options: list[dict[str, str]]
    api_base: str
    ws_path: str
    link_payload_key: str


@dataclass
class ScrapeInputs:
    target_url: str
    scroll_rounds: int
    start_date: datetime
    end_date: Optional[datetime]
    output_file: str
    collection_type: Optional[str] = None


class PlatformAdapter:
    key: str
    name: str

    def platform_config(self) -> PlatformConfig:
        raise NotImplementedError

    def normalize_url(self, raw_value: str) -> Optional[str]:
        raise NotImplementedError

    def format_date_coverage(self, start_date: datetime, end_date: Optional[datetime]) -> str:
        raise NotImplementedError

    def launch_browser(self):
        raise NotImplementedError

    def open_login_form(self, page, target_url: str) -> None:
        raise NotImplementedError

    def detect_login_gate(self, page) -> tuple[bool, str]:
        raise NotImplementedError

    def detect_verification_gate(self, page) -> tuple[bool, str]:
        raise NotImplementedError

    def page_ready_for_collection(self, page) -> bool:
        raise NotImplementedError

    def auto_login_if_needed(self, page, context, target_url: str, log_hook: Optional[Callable[[str, str, str], None]] = None) -> bool:
        raise NotImplementedError

    def save_storage_state(self, context, log_hook: Optional[Callable[[str, str, str], None]] = None) -> None:
        raise NotImplementedError

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
        raise NotImplementedError

    def extract_post(self, page, url: str, collection_type: Optional[str], log_hook: Optional[Callable[[str, str, str], None]] = None) -> Any:
        raise NotImplementedError

    def post_to_record(self, post: Any) -> dict[str, Any]:
        raise NotImplementedError

    def export_excel(self, posts: list[Any], output_file: str, coverage_label: str, collection_type: Optional[str]) -> None:
        raise NotImplementedError

    def uses_local_browser_window(self) -> bool:
        raise NotImplementedError
