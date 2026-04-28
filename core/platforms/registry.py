from __future__ import annotations

from .base import PlatformAdapter
from .instagram import InstagramAdapter
from .facebook import FacebookAdapter


_ADAPTERS: dict[str, PlatformAdapter] = {
    "instagram": InstagramAdapter(),
    "facebook": FacebookAdapter(),
}


def get_platform_adapter(platform_key: str) -> PlatformAdapter:
    key = (platform_key or "").strip().lower()
    if key not in _ADAPTERS:
        raise ValueError(f"Unknown platform: {platform_key}")
    return _ADAPTERS[key]


def list_platform_adapters() -> list[PlatformAdapter]:
    return list(_ADAPTERS.values())
