"""FastAPI REST API routes for bot status, tweet history, and manual triggers."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.tweet import CricketSource, GeneratedTweet, PostedTweet
from app.utils.logger import setup_logger

logger = setup_logger(__name__)
router = APIRouter(prefix="/api", tags=["TweetVeet API"])


@router.get("/status")
async def get_status(db: AsyncSession = Depends(get_db)):
    """Bot health status, last run info, and counts."""
    # Total sources
    total_sources = await db.scalar(select(func.count(CricketSource.id)))

    # Total posted
    total_posted = await db.scalar(select(func.count(PostedTweet.id)))

    # Last posted tweet
    last_posted = await db.scalar(
        select(PostedTweet.posted_at)
        .order_by(PostedTweet.posted_at.desc())
        .limit(1)
    )

    # Sources pending processing
    pending = await db.scalar(
        select(func.count(CricketSource.id))
        .where(CricketSource.is_processed == False)  # noqa: E712
    )

    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "total_sources_collected": total_sources or 0,
            "total_tweets_posted": total_posted or 0,
            "pending_sources": pending or 0,
            "last_posted_at": last_posted.isoformat() if last_posted else None,
        },
    }


@router.get("/tweets")
async def get_tweets(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of posted tweets."""
    offset = (page - 1) * per_page

    result = await db.execute(
        select(PostedTweet)
        .order_by(desc(PostedTweet.posted_at))
        .offset(offset)
        .limit(per_page)
    )
    tweets = result.scalars().all()

    total = await db.scalar(select(func.count(PostedTweet.id)))

    return {
        "page": page,
        "per_page": per_page,
        "total": total or 0,
        "tweets": [
            {
                "id": t.id,
                "tweet_id": t.tweet_id,
                "content": t.content,
                "type": t.tweet_type,
                "media_attached": t.media_attached,
                "posted_at": t.posted_at.isoformat() if t.posted_at else None,
                "engagement": {
                    "likes": t.likes,
                    "retweets": t.retweets,
                    "replies": t.replies,
                    "impressions": t.impressions,
                },
            }
            for t in tweets
        ],
    }


@router.get("/sources")
async def get_sources(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of collected cricket sources with dedup stats."""
    offset = (page - 1) * per_page

    result = await db.execute(
        select(CricketSource)
        .order_by(desc(CricketSource.collected_at))
        .offset(offset)
        .limit(per_page)
    )
    sources = result.scalars().all()

    total = await db.scalar(select(func.count(CricketSource.id)))

    return {
        "page": page,
        "per_page": per_page,
        "total": total or 0,
        "sources": [
            {
                "id": s.id,
                "type": s.source_type,
                "title": s.title,
                "url": s.url,
                "author": s.author,
                "has_media": bool(s.media_url),
                "engagement_score": s.engagement_score,
                "is_processed": s.is_processed,
                "collected_at": s.collected_at.isoformat() if s.collected_at else None,
            }
            for s in sources
        ],
    }


@router.post("/trigger")
async def trigger_pipeline():
    """Manually trigger the collect-and-post pipeline."""
    from app.scheduler.tasks import manual_trigger

    task = manual_trigger.delay()
    logger.info("Manual pipeline triggered", extra={"task_id": task.id})

    return {
        "status": "triggered",
        "task_id": task.id,
        "message": "Pipeline is running in background (news + stats). Check /api/status for results.",
    }


@router.post("/trigger-stats")
async def trigger_stats():
    """Manually trigger ONLY the stats comparison tweet."""
    from app.scheduler.tasks import post_stats_tweet

    task = post_stats_tweet.delay()
    logger.info("Stats comparison triggered", extra={"task_id": task.id})

    return {
        "status": "triggered",
        "task_id": task.id,
        "message": "Stats comparison tweet is being generated. Check worker logs.",
    }


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Aggregated posting statistics."""
    # Total by type
    result = await db.execute(
        select(PostedTweet.tweet_type, func.count(PostedTweet.id))
        .group_by(PostedTweet.tweet_type)
    )
    type_counts = {row[0]: row[1] for row in result.all()}

    # Total by source type
    result = await db.execute(
        select(CricketSource.source_type, func.count(CricketSource.id))
        .group_by(CricketSource.source_type)
    )
    source_counts = {row[0]: row[1] for row in result.all()}

    # Average engagement
    avg_likes = await db.scalar(select(func.avg(PostedTweet.likes))) or 0
    avg_retweets = await db.scalar(select(func.avg(PostedTweet.retweets))) or 0

    # Total generated variants
    total_generated = await db.scalar(select(func.count(GeneratedTweet.id))) or 0

    return {
        "posting_summary": type_counts,
        "source_summary": source_counts,
        "total_variants_generated": total_generated,
        "average_engagement": {
            "likes": round(float(avg_likes), 1),
            "retweets": round(float(avg_retweets), 1),
        },
    }
