"""Engagement features: auto-reply to trending tweets, quote-tweet viral posts."""

import asyncio
import time
from typing import Optional

import httpx

from app.config import settings
from app.generator.tweet_generator import generate_reply, generate_quote
from app.poster.twitter_poster import poster
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class EngagementManager:
    """Manages auto-engagement with trending cricket tweets.

    Features:
    - Auto-reply to high-engagement tweets
    - Quote-tweet viral posts with AI commentary
    - Anti-spam safeguards (max interactions per hour, cooldown)
    """

    # Anti-spam thresholds
    MAX_INTERACTIONS_PER_HOUR = 10
    MIN_GAP_SECONDS = 300  # 5 minutes between engagements
    MIN_ENGAGEMENT_FOR_REPLY = 100  # Min likes+RTs to trigger reply
    MIN_ENGAGEMENT_FOR_QUOTE = 500  # Min for quote tweet

    # Accounts to never interact with
    BLOCKLIST = set()

    def __init__(self):
        self.interaction_timestamps: list[float] = []
        self.engaged_tweet_ids: set[str] = set()
        self.max_per_hour = min(
            settings.max_replies_per_hour,
            self.MAX_INTERACTIONS_PER_HOUR,
        )

    def _can_engage(self) -> bool:
        """Check anti-spam limits before engaging."""
        now = time.time()
        one_hour_ago = now - 3600

        # Prune old timestamps
        self.interaction_timestamps = [
            t for t in self.interaction_timestamps if t > one_hour_ago
        ]

        # Check hourly limit
        if len(self.interaction_timestamps) >= self.max_per_hour:
            logger.debug("Engagement hourly limit reached")
            return False

        # Check minimum gap
        if self.interaction_timestamps:
            last = max(self.interaction_timestamps)
            if now - last < self.MIN_GAP_SECONDS:
                logger.debug("Engagement cooldown active")
                return False

        return True

    def _record_engagement(self, tweet_id: str):
        """Record an engagement action."""
        self.interaction_timestamps.append(time.time())
        self.engaged_tweet_ids.add(tweet_id)

    async def find_trending_tweets(self) -> list[dict]:
        """Search for trending cricket tweets with high engagement.

        Returns:
            List of tweet dicts with id, text, and engagement metrics.
        """
        if not settings.twitter_bearer_token:
            logger.info("No bearer token — trending search disabled")
            return []

        # Build search for popular cricket tweets
        keywords = settings.cricket_keywords[:10]
        query_parts = " OR ".join(f'"{kw}"' for kw in keywords)
        query = f"({query_parts}) lang:en -is:retweet"

        headers = {"Authorization": f"Bearer {settings.twitter_bearer_token}"}
        params = {
            "query": query,
            "max_results": 20,
            "tweet.fields": "public_metrics,created_at,author_id",
            "user.fields": "username",
            "expansions": "author_id",
            "sort_order": "relevancy",
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    "https://api.twitter.com/2/tweets/search/recent",
                    headers=headers,
                    params=params,
                )

                if response.status_code != 200:
                    logger.warning(f"Trending search failed: {response.status_code}")
                    return []

                data = response.json()

            tweets = data.get("data", [])
            user_map = {}
            for user in data.get("includes", {}).get("users", []):
                user_map[user["id"]] = user.get("username", "")

            # Filter by engagement
            trending = []
            for tweet in tweets:
                metrics = tweet.get("public_metrics", {})
                total_engagement = (
                    metrics.get("like_count", 0)
                    + metrics.get("retweet_count", 0) * 2
                    + metrics.get("reply_count", 0)
                )
                author = user_map.get(tweet.get("author_id"), "")

                if tweet["id"] in self.engaged_tweet_ids:
                    continue
                if author in self.BLOCKLIST:
                    continue

                if total_engagement >= self.MIN_ENGAGEMENT_FOR_REPLY:
                    trending.append({
                        "id": tweet["id"],
                        "text": tweet["text"],
                        "author": author,
                        "engagement": total_engagement,
                    })

            # Sort by engagement descending
            trending.sort(key=lambda x: x["engagement"], reverse=True)
            return trending[:5]

        except Exception as e:
            logger.error(f"Trending search error: {e}")
            return []

    async def auto_reply(self, tweet: dict) -> Optional[dict]:
        """Generate and post an AI reply to a trending tweet.

        Args:
            tweet: Dict with id, text, engagement.

        Returns:
            Posted reply result, or None.
        """
        if not self._can_engage():
            return None

        reply_text = await generate_reply(tweet["text"])
        if not reply_text:
            logger.warning("Failed to generate reply")
            return None

        result = await poster.post_reply(
            text=reply_text,
            reply_to_id=tweet["id"],
        )

        if result:
            self._record_engagement(tweet["id"])
            logger.info(
                "Auto-replied to tweet",
                extra={"tweet_id": tweet["id"], "engagement": tweet["engagement"]},
            )
        return result

    async def auto_quote(self, tweet: dict) -> Optional[dict]:
        """Generate and post a quote tweet for a viral post.

        Args:
            tweet: Dict with id, text, engagement.

        Returns:
            Posted quote result, or None.
        """
        if not self._can_engage():
            return None

        if tweet["engagement"] < self.MIN_ENGAGEMENT_FOR_QUOTE:
            return None

        quote_text = await generate_quote(tweet["text"], tweet["engagement"])
        if not quote_text:
            logger.warning("Failed to generate quote")
            return None

        result = await poster.post_quote(
            text=quote_text,
            quote_tweet_id=tweet["id"],
        )

        if result:
            self._record_engagement(tweet["id"])
            logger.info(
                "Quote-tweeted viral post",
                extra={"tweet_id": tweet["id"], "engagement": tweet["engagement"]},
            )
        return result

    async def run_engagement_cycle(self) -> dict:
        """Run a complete engagement cycle: find trending → reply/quote.

        Returns:
            Summary dict with counts of replies and quotes.
        """
        if not settings.enable_engagement:
            logger.info("Engagement features disabled")
            return {"replies": 0, "quotes": 0}

        trending = await self.find_trending_tweets()
        logger.info(f"Found {len(trending)} trending tweets for engagement")

        replies_count = 0
        quotes_count = 0

        for tweet in trending:
            if not self._can_engage():
                break

            # Quote the most viral ones, reply to the rest
            if tweet["engagement"] >= self.MIN_ENGAGEMENT_FOR_QUOTE:
                result = await self.auto_quote(tweet)
                if result:
                    quotes_count += 1
                    continue

            result = await self.auto_reply(tweet)
            if result:
                replies_count += 1

            # Respect rate limits between engagements
            await asyncio.sleep(10)

        logger.info(
            "Engagement cycle complete",
            extra={"replies": replies_count, "quotes": quotes_count},
        )
        return {"replies": replies_count, "quotes": quotes_count}


# Singleton
engagement_manager = EngagementManager()
