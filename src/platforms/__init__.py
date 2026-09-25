"""Platform packages."""

from src.platforms.instagram import InstagramAuth
from src.platforms.tiktok import TikTokAuth
from src.platforms.youtube import YouTubeAuth

__all__ = ["InstagramAuth", "TikTokAuth", "YouTubeAuth"]