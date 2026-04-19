"""Twitter API v2 posting engine with OAuth 1.0a, media upload, and rate limiting."""

import asyncio
import os
import random
import time
from typing import Optional

import httpx
import tweepy

from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class RateLimiter:
    """Token-bucket rate limiter for Twitter API.

    Enforces Twitter's posting limits:
    - 300 tweets per 3-hour window (for v2 API)
    - Configurable per-hour limit via settings
    """

    def __init__(self, max_per_hour: int = 5):
        self.max_per_hour = max_per_hour
        self.timestamps: list[float] = []

    def can_post(self) -> bool:
        """Check if posting is allowed under the rate limit."""
        now = time.time()
        one_hour_ago = now - 3600

        # Prune old timestamps
        self.timestamps = [t for t in self.timestamps if t > one_hour_ago]

        return len(self.timestamps) < self.max_per_hour

    def record_post(self):
        """Record a successful post timestamp."""
        self.timestamps.append(time.time())

    def remaining(self) -> int:
        """Number of posts remaining in this hour window."""
        now = time.time()
        one_hour_ago = now - 3600
        self.timestamps = [t for t in self.timestamps if t > one_hour_ago]
        return max(0, self.max_per_hour - len(self.timestamps))


class TwitterPoster:
    """Posts tweets to Twitter/X using API v2 with media support.

    Handles:
    - OAuth 1.0a authentication
    - Media upload via v1.1 endpoint
    - Tweet creation via v2 endpoint
    - Rate limiting and human-like delays
    - Retry on transient failures
    """

    def __init__(self):
        self.rate_limiter = RateLimiter(max_per_hour=settings.max_tweets_per_hour)
        self._init_clients()

    def _init_clients(self):
        """Initialize tweepy clients for API v1.1 (media) and v2 (tweets)."""
        # OAuth 1.0a for v1.1 media upload
        self.auth = tweepy.OAuth1UserHandler(
            consumer_key=settings.twitter_api_key,
            consumer_secret=settings.twitter_api_secret,
            access_token=settings.twitter_access_token,
            access_token_secret=settings.twitter_access_token_secret,
        )
        self.api_v1 = tweepy.API(self.auth, wait_on_rate_limit=True)

        # OAuth 1.0a for v2 client
        self.client_v2 = tweepy.Client(
            consumer_key=settings.twitter_api_key,
            consumer_secret=settings.twitter_api_secret,
            access_token=settings.twitter_access_token,
            access_token_secret=settings.twitter_access_token_secret,
            wait_on_rate_limit=True,
        )

    async def _upload_media(self, image_path: str) -> Optional[str]:
        """Upload media to Twitter via v1.1 API.

        Args:
            image_path: Local path to the image file.

        Returns:
            Media ID string, or None on failure.
        """
        if not os.path.exists(image_path):
            logger.error("Image file not found", extra={"path": image_path})
            return None

        try:
            # tweepy media_upload is synchronous — run in executor
            loop = asyncio.get_event_loop()
            media = await loop.run_in_executor(
                None,
                lambda: self.api_v1.media_upload(filename=image_path),
            )
            media_id = str(media.media_id)
            logger.info("Media uploaded", extra={"media_id": media_id})
            return media_id

        except tweepy.TweepyException as e:
            logger.error(f"Media upload failed: {e}")
            return None

    async def _human_delay(self):
        """Add a random delay to mimic human posting behavior."""
        delay = random.uniform(30, 120)
        logger.debug(f"Adding human-like delay: {delay:.1f}s")
        await asyncio.sleep(delay)

    async def post_tweet(
        self,
        text: str,
        image_path: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        quote_tweet_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Post a tweet with optional media, reply, or quote.

        Args:
            text: Tweet text (must be ≤ 280 chars).
            image_path: Optional local path to image to attach.
            in_reply_to: Tweet ID to reply to (for thread/reply).
            quote_tweet_id: Tweet ID to quote-tweet.

        Returns:
            Dict with tweet_id and content on success, None on failure.
        """
        # Rate limit check
        if not self.rate_limiter.can_post():
            logger.warning(
                "Rate limit reached — skipping post",
                extra={"remaining": self.rate_limiter.remaining()},
            )
            return None

        # Validate text length
        if len(text) > 280:
            logger.error("Tweet exceeds 280 characters", extra={"length": len(text)})
            text = text[:277] + "..."

        # Upload media if provided
        media_ids = None
        if image_path:
            media_id = await self._upload_media(image_path)
            if media_id:
                media_ids = [media_id]

        # Add human-like delay
        await self._human_delay()

        # Post via v2 API
        try:
            loop = asyncio.get_event_loop()
            kwargs = {"text": text}
            if media_ids:
                kwargs["media_ids"] = media_ids
            if in_reply_to:
                kwargs["in_reply_to_tweet_id"] = in_reply_to
            if quote_tweet_id:
                kwargs["quote_tweet_id"] = quote_tweet_id

            response = await loop.run_in_executor(
                None,
                lambda: self.client_v2.create_tweet(**kwargs),
            )

            tweet_id = response.data.get("id") if response.data else None

            if tweet_id:
                self.rate_limiter.record_post()
                tweet_type = "tweet"
                if in_reply_to:
                    tweet_type = "reply"
                elif quote_tweet_id:
                    tweet_type = "quote"

                logger.info(
                    "Tweet posted successfully",
                    extra={
                        "tweet_id": tweet_id,
                        "type": tweet_type,
                        "has_media": bool(media_ids),
                        "remaining_quota": self.rate_limiter.remaining(),
                    },
                )
                return {
                    "tweet_id": tweet_id,
                    "content": text,
                    "type": tweet_type,
                    "media_attached": bool(media_ids),
                }

            logger.error("Tweet creation returned no ID")
            return None

        except tweepy.TooManyRequests:
            logger.warning("Twitter API rate limited (429) — will retry next cycle")
            return None
        except tweepy.TweepyException as e:
            logger.error(f"Tweet posting failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected posting error: {e}")
            return None

    async def post_reply(self, text: str, reply_to_id: str) -> Optional[dict]:
        """Post a reply to a specific tweet.

        Args:
            text: Reply text.
            reply_to_id: ID of tweet to reply to.

        Returns:
            Post result dict or None.
        """
        return await self.post_tweet(text=text, in_reply_to=reply_to_id)

    async def post_quote(
        self, text: str, quote_tweet_id: str, image_path: Optional[str] = None
    ) -> Optional[dict]:
        """Post a quote tweet.

        Args:
            text: Quote commentary text.
            quote_tweet_id: ID of tweet to quote.
            image_path: Optional image to attach.

        Returns:
            Post result dict or None.
        """
        return await self.post_tweet(
            text=text,
            image_path=image_path,
            quote_tweet_id=quote_tweet_id,
        )


# Singleton poster instance
poster = TwitterPoster()
