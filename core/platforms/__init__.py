from .base import PlatformAdapter, PlatformConfig, ScrapeInputs
from .instagram import InstagramAdapter
from .facebook import FacebookAdapter
from .registry import get_platform_adapter, list_platform_adapters

__all__ = [
    "PlatformAdapter",
    "PlatformConfig",
    "ScrapeInputs",
    "InstagramAdapter",
    "FacebookAdapter",
    "get_platform_adapter",
    "list_platform_adapters",
]
