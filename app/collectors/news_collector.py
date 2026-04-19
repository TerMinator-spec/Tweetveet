"""News API collector (NewsAPI.org + GNews) for cricket content."""

from datetime import datetime, timezone
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class NewsCollector(BaseCollector):
    """Collects cricket news from NewsAPI.org and GNews APIs.

    Aggregates results from both sources and normalizes them.
    Either or both APIs can be disabled via empty keys.
    """

    NEWSAPI_URL = "https://newsapi.org/v2/everything"
    GNEWS_URL = "https://gnews.io/api/v4/search"

    def __init__(self):
        super().__init__("news")
        self.newsapi_key = settings.newsapi_key
        self.gnews_key = settings.gnews_api_key

    async def _fetch_newsapi(self) -> list[dict[str, Any]]:
        """Fetch cricket articles from NewsAPI.org."""
        if not self.newsapi_key:
            self.logger.info("NewsAPI key not set — skipping")
            return []

        params = {
            "q": "cricket OR IPL OR \"T20 World Cup\" OR BCCI",
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 30,
            "apiKey": self.newsapi_key,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(self.NEWSAPI_URL, params=params)

            if response.status_code == 429:
                self.logger.warning("NewsAPI rate limited")
                return []

            response.raise_for_status()
            data = response.json()

        articles = data.get("articles", [])
        # Tag source for normalization
        for article in articles:
            article["_source"] = "newsapi"
        return articles

    async def _fetch_gnews(self) -> list[dict[str, Any]]:
        """Fetch cricket articles from GNews API."""
        if not self.gnews_key:
            self.logger.info("GNews key not set — skipping")
            return []

        params = {
            "q": "cricket",
            "lang": "en",
            "max": 30,
            "sortby": "publishedAt",
            "token": self.gnews_key,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(self.GNEWS_URL, params=params)

            if response.status_code == 429:
                self.logger.warning("GNews rate limited")
                return []

            response.raise_for_status()
            data = response.json()

        articles = data.get("articles", [])
        for article in articles:
            article["_source"] = "gnews"
        return articles

    async def _fetch(self) -> list[dict[str, Any]]:
        """Fetch from both NewsAPI and GNews concurrently."""
        import asyncio

        results = await asyncio.gather(
            self._fetch_newsapi(),
            self._fetch_gnews(),
            return_exceptions=True,
        )

        all_articles = []
        for result in results:
            if isinstance(result, Exception):
                self.logger.error(f"Sub-fetch failed: {result}")
                continue
            all_articles.extend(result)

        # Filter for today's news (last 24 hours)
        now = datetime.now(timezone.utc)
        recent_articles = []
        for item in all_articles:
            pub_raw = item.get("publishedAt")
            if not pub_raw:
                continue
            try:
                pub_date = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                if (now - pub_date).total_seconds() <= 86400:
                    recent_articles.append(item)
            except Exception:
                pass

        return recent_articles

    def _normalize(self, item: dict[str, Any]) -> dict[str, Any]:
        """Normalize a news article into CricketSource fields."""
        source_tag = item.get("_source", "newsapi")
        source_type = "newsapi" if source_tag == "newsapi" else "gnews"

        # Parse published date
        published = None
        published_raw = item.get("publishedAt")
        if published_raw:
            try:
                published = datetime.fromisoformat(
                    published_raw.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                published = datetime.now(timezone.utc)

        # Extract image — NewsAPI uses 'urlToImage', GNews uses 'image'
        image_url = item.get("urlToImage") or item.get("image")

        # Build title from headline
        title = item.get("title", "")
        if not title:
            title = (item.get("description") or "")[:500]

        return {
            "source_type": source_type,
            "external_id": None,
            "title": title[:500],
            "body": item.get("description") or item.get("content") or "",
            "url": item.get("url", ""),
            "author": item.get("author") or item.get("source", {}).get("name", ""),
            "media_url": image_url,
            "published_at": published,
            "engagement_score": 0.0,
        }
