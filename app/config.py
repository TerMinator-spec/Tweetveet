"""Centralized configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://tweetveet:password@localhost:5432/tweetveet",
        description="Async PostgreSQL connection string",
    )
    database_url_sync: str = Field(
        default="postgresql://tweetveet:password@localhost:5432/tweetveet",
        description="Sync PostgreSQL connection string (for Alembic/Celery)",
    )

    # --- Redis ---
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for Celery broker",
    )

    # --- Twitter/X API ---
    twitter_api_key: str = Field(default="", description="Twitter API consumer key")
    twitter_api_secret: str = Field(default="", description="Twitter API consumer secret")
    twitter_access_token: str = Field(default="", description="Twitter OAuth access token")
    twitter_access_token_secret: str = Field(default="", description="Twitter OAuth access token secret")
    twitter_bearer_token: str = Field(default="", description="Twitter API v2 bearer token")

    # --- OpenAI ---
    openai_api_key: str = Field(default="", description="OpenAI API key")
    openai_model: str = Field(default="gpt-5.2", description="OpenAI model to use")

    # --- News APIs ---
    newsapi_key: str = Field(default="", description="NewsAPI.org API key")
    gnews_api_key: str = Field(default="", description="GNews API key")
    cricdata_api_key: str = Field(default="", description="Cricdata (cricapi.com) API key")
    espncricinfo_rss_url: str = Field(
        default="https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
        description="ESPN Cricinfo RSS feed URL"
    )

    # --- Unsplash ---
    unsplash_access_key: str = Field(default="", description="Unsplash API key (fallback images)")

    # --- Bot Behavior ---
    posting_interval_minutes: int = Field(default=180, description="Minutes between bot runs")
    max_tweets_per_hour: int = Field(default=1, description="Max tweets posted per hour")
    max_replies_per_hour: int = Field(default=10, description="Max engagement replies per hour")
    enable_engagement: bool = Field(default=True, description="Enable auto-reply/quote features")
    enable_image_posting: bool = Field(default=True, description="Attach images to tweets")
    prefer_x_media: bool = Field(default=True, description="Prefer X media over Unsplash")

    # --- Logging ---
    log_level: str = Field(default="INFO", description="Logging level")

    # --- Cricket Keywords ---
    cricket_keywords: list[str] = Field(
        default=[
            "IPL", "cricket", "Kohli", "Rohit Sharma", "wicket",
            "T20", "ODI", "Test match", "BCCI", "World Cup cricket",
            "six", "century", "bowled", "LBW", "run out",
            "CSK", "MI", "RCB", "KKR", "SRH", "DC", "PBKS", "RR", "GT", "LSG",
        ],
        description="Keywords for cricket content search",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


# Singleton instance
settings = Settings()
