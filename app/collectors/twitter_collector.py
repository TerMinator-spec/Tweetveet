"""Twitter/X API v2 collector for cricket content."""

from datetime import datetime, timezone
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class TwitterCollector(BaseCollector):
    """Collects cricket-related tweets via Twitter API v2 recent search.

    Requires a Twitter API Bearer Token with at least Basic tier access.
    Gracefully degrades if the token is missing or access is insufficient.
    """

    SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
    MAX_RESULTS = 50

    def __init__(self):
        super().__init__("twitter")
        self.bearer_token = settings.twitter_bearer_token
        self._available = bool(self.bearer_token)
        if not self._available:
            self.logger.warning("Twitter bearer token not set — collector disabled")

    def _build_query(self) -> str:
        """Build a Twitter search query from cricket keywords.

        Combines keywords with OR, filters out retweets,
        and requires English language.
        """
        # Use a subset of top keywords to stay within query length limits
        top_keywords = settings.cricket_keywords[:15]
        keyword_clause = " OR ".join(f'"{kw}"' for kw in top_keywords)
        return f"({keyword_clause}) lang:en -is:retweet has:media OR has:links"

    async def _fetch(self) -> list[dict[str, Any]]:
        """Fetch recent cricket tweets from Twitter API v2."""
        if not self._available:
            self.logger.info("Twitter collector skipped — no bearer token")
            return []

        query = self._build_query()
        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        params = {
            "query": query,
            "max_results": self.MAX_RESULTS,
            "tweet.fields": "created_at,author_id,public_metrics,entities",
            "expansions": "attachments.media_keys,author_id",
            "media.fields": "url,preview_image_url,type",
            "user.fields": "username,name",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(self.SEARCH_URL, headers=headers, params=params)

            if response.status_code == 403:
                self.logger.warning("Twitter API access forbidden — need higher tier")
                self._available = False
                return []

            if response.status_code == 429:
                self.logger.warning("Twitter API rate limited — backing off")
                return []

            response.raise_for_status()
            data = response.json()

        tweets = data.get("data", [])
        includes = data.get("includes", {})

        # Build lookup maps for media and users
        media_map = {}
        for media in includes.get("media", []):
            media_map[media.get("media_key")] = (
                media.get("url") or media.get("preview_image_url")
            )

        user_map = {}
        for user in includes.get("users", []):
            user_map[user["id"]] = user.get("username", "")

        # Attach media and user info to each tweet
        for tweet in tweets:
            tweet["_media_url"] = None
            media_keys = (
                tweet.get("attachments", {}).get("media_keys", [])
            )
            for mk in media_keys:
                if mk in media_map and media_map[mk]:
                    tweet["_media_url"] = media_map[mk]
                    break

            tweet["_username"] = user_map.get(tweet.get("author_id"), "")

        return tweets

    def _normalize(self, item: dict[str, Any]) -> dict[str, Any]:
        """Normalize a Twitter API v2 tweet into CricketSource fields."""
        metrics = item.get("public_metrics", {})
        engagement = (
            metrics.get("like_count", 0)
            + metrics.get("retweet_count", 0) * 2
            + metrics.get("reply_count", 0)
        )

        published = None
        if item.get("created_at"):
            try:
                published = datetime.fromisoformat(
                    item["created_at"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                published = datetime.now(timezone.utc)

        return {
            "source_type": "twitter",
            "external_id": item.get("id"),
            "title": item.get("text", "")[:500],
            "body": item.get("text", ""),
            "url": f"https://twitter.com/i/status/{item.get('id', '')}",
            "author": item.get("_username", ""),
            "media_url": item.get("_media_url"),
            "published_at": published,
            "engagement_score": float(engagement),
        }
