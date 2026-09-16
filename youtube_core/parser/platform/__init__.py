"""YouTube 解析器导出入口。"""

from .base import BaseVideoParser
from .youtube import YouTubeParser

__all__ = [
    "BaseVideoParser",
    "YouTubeParser",
]
