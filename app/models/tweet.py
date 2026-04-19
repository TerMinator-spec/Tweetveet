"""Database models for cricket sources, generated tweets, and posted tweets."""

import datetime
import enum

from sqlalchemy import (
    Column, DateTime, Enum, Float, ForeignKey, Index, Integer,
    String, Text, Boolean,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class SourceType(str, enum.Enum):
    """Origin of the cricket content."""
    TWITTER = "twitter"
    NEWSAPI = "newsapi"
    GNEWS = "gnews"
    CRICBUZZ = "cricbuzz"


class TweetStyle(str, enum.Enum):
    """Style of generated tweet."""
    HYPE = "hype"
    ANALYTICAL = "analytical"
    CASUAL = "casual"


class CricketSource(Base):
    """Raw cricket content collected from various sources."""

    __tablename__ = "cricket_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(Enum(SourceType), nullable=False, index=True)
    external_id = Column(String(255), nullable=True, comment="Original ID from source platform")
    title = Column(String(500), nullable=False)
    body = Column(Text, nullable=True)
    url = Column(String(2048), nullable=True)
    author = Column(String(255), nullable=True)
    media_url = Column(String(2048), nullable=True, comment="Image/media URL from source")
    content_hash = Column(String(64), nullable=False, unique=True, comment="SHA-256 for exact dedup")
    simhash = Column(String(32), nullable=True, comment="SimHash for fuzzy dedup")
    engagement_score = Column(Float, default=0.0, comment="Likes + RTs from source")
    collected_at = Column(DateTime(timezone=True), server_default=func.now())
    published_at = Column(DateTime(timezone=True), nullable=True)
    is_processed = Column(Boolean, default=False, index=True)

    # Relationships
    generated_tweets = relationship("GeneratedTweet", back_populates="source", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_source_hash_type", "content_hash", "source_type"),
        Index("ix_source_collected", "collected_at"),
    )

    def __repr__(self) -> str:
        return f"<CricketSource(id={self.id}, type={self.source_type}, title={self.title[:40]})>"


class GeneratedTweet(Base):
    """AI-generated tweet variants linked to a source."""

    __tablename__ = "generated_tweets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, ForeignKey("cricket_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    style = Column(Enum(TweetStyle), nullable=False)
    content = Column(String(300), nullable=False, comment="Tweet text ≤ 280 chars")
    hashtags = Column(String(200), nullable=True, comment="Comma-separated hashtags")
    score = Column(Float, default=0.0, comment="AI-assigned engagement score")
    is_selected = Column(Boolean, default=False, index=True, comment="Chosen as best variant")
    generated_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    source = relationship("CricketSource", back_populates="generated_tweets")
    posted_tweet = relationship("PostedTweet", back_populates="generated_tweet", uselist=False)

    __table_args__ = (
        Index("ix_gen_source_selected", "source_id", "is_selected"),
    )

    def __repr__(self) -> str:
        return f"<GeneratedTweet(id={self.id}, style={self.style}, score={self.score})>"


class PostedTweet(Base):
    """Record of tweets posted to Twitter/X."""

    __tablename__ = "posted_tweets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    generated_tweet_id = Column(
        Integer,
        ForeignKey("generated_tweets.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    tweet_id = Column(String(64), nullable=False, unique=True, comment="Twitter tweet ID")
    tweet_type = Column(
        String(20), default="tweet",
        comment="tweet, reply, or quote",
    )
    content = Column(String(300), nullable=False)
    media_attached = Column(Boolean, default=False)
    posted_at = Column(DateTime(timezone=True), server_default=func.now())

    # Engagement metrics (updated via webhook or polling)
    likes = Column(Integer, default=0)
    retweets = Column(Integer, default=0)
    replies = Column(Integer, default=0)
    impressions = Column(Integer, default=0)

    # Relationships
    generated_tweet = relationship("GeneratedTweet", back_populates="posted_tweet")

    __table_args__ = (
        Index("ix_posted_at", "posted_at"),
        Index("ix_posted_type", "tweet_type"),
    )

    def __repr__(self) -> str:
        return f"<PostedTweet(id={self.id}, tweet_id={self.tweet_id})>"
