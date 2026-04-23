"""Celery tasks and beat schedule for automated cricket bot pipeline."""

import asyncio
from datetime import timedelta

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

# --- Celery App ---
celery_app = Celery(
    "tweetveet",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    # Dead letter queue
    task_default_queue="tweetveet",
    task_default_exchange="tweetveet",
    task_default_routing_key="tweetveet",
)

# --- Beat Schedule ---
celery_app.conf.beat_schedule = {
    "collect-and-post": {
        "task": "app.scheduler.tasks.collect_and_post",
        # 14:30 UTC is exactly 8:00 PM IST (India Standard Time)
        "schedule": crontab(minute=30, hour=14),
    },
    "engagement-cycle": {
        "task": "app.scheduler.tasks.run_engagement",
        # Also run engagement once daily at 8:15 PM IST (14:45 UTC) to follow the main posting
        "schedule": crontab(minute=45, hour=14),
    },
}

# --- Sync DB for Celery (Celery workers are sync) ---
sync_engine = create_engine(settings.database_url_sync, pool_size=5, max_overflow=5)
SyncSessionFactory = sessionmaker(bind=sync_engine)


def _run_async(coro):
    """Helper to run async code from sync Celery tasks."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


async def _pipeline():
    """Full collect → generate → post pipeline (async)."""
    from app.collectors.twitter_collector import TwitterCollector
    from app.collectors.news_collector import NewsCollector
    from app.collectors.dedup import deduplicate_and_store
    from app.generator.tweet_generator import generate_tweet_variants, select_best_tweet
    from app.media.image_handler import get_image_for_tweet, cleanup_images
    from app.poster.twitter_poster import poster
    from app.models.tweet import GeneratedTweet, PostedTweet
    from app.database import async_session_factory

    logger.info("=== Starting collect-and-post pipeline ===")

    # Step 1: Collect from all sources
    twitter_collector = TwitterCollector()
    news_collector = NewsCollector()

    all_items = []
    try:
        twitter_items = await twitter_collector.collect()
        all_items.extend(twitter_items)
    except Exception as e:
        logger.error(f"Twitter collection failed: {e}")

    try:
        news_items = await news_collector.collect()
        all_items.extend(news_items)
    except Exception as e:
        logger.error(f"News collection failed: {e}")

    if not all_items:
        logger.info("No items collected — pipeline ending")
        return {"collected": 0, "posted": 0}

    logger.info(f"Collected {len(all_items)} raw items")

    # Step 2: Deduplicate and store
    async with async_session_factory() as db:
        new_sources = await deduplicate_and_store(db, all_items)
        await db.commit()

        if not new_sources:
            logger.info("All items were duplicates — nothing to post")
            return {"collected": len(all_items), "new": 0, "posted": 0}

        logger.info(f"Stored {len(new_sources)} new sources")

        # Step 3: Generate tweets for top sources (sorted by engagement)
        new_sources.sort(key=lambda s: s.engagement_score, reverse=True)
        
        # Always prioritize all CricAPI live match sources, bypassing generic hourly limits
        sources_to_tweet = [s for s in new_sources if s.author == "CricAPI"]
        
        other_count = 0
        for s in new_sources:
            if s.author != "CricAPI" and other_count < settings.max_tweets_per_hour:
                sources_to_tweet.append(s)
                other_count += 1

        posted_count = 0

        for source in sources_to_tweet:
            try:
                # Generate 3 variants
                variants = await generate_tweet_variants(
                    source.title,
                    source.body or "",
                    item_time=source.published_at,
                )
                if not variants:
                    continue

                # Store variants
                for v in variants:
                    gen = GeneratedTweet(
                        source_id=source.id,
                        style=v["style"],
                        content=v["content"],
                        score=v.get("final_score", v.get("score", 5.0)),
                    )
                    db.add(gen)

                # Select the best
                best = select_best_tweet(variants)
                if not best:
                    continue

                # Mark as selected
                await db.flush()

                # Get image — prefer X media from source
                image_path = await get_image_for_tweet(
                    source_media_url=source.media_url,
                    keywords=source.title[:50],
                )

                # Post to Twitter
                result = await poster.post_tweet(
                    text=best["content"],
                    image_path=image_path,
                )
                
                # Mock result to allow pipeline to finish without hitting Twitter
                # import time
                # result = {
                #     "tweet_id": f"TEST_MODE_{int(time.time())}_{source.id}",
                #     "type": "tweet",
                #     "content": best["content"],
                #     "media_attached": bool(image_path),
                # }
                # logger.info(
                #     f"\n\n======================================\n"
                #     f"📢 FINAL GENERATED TWEET (NOT POSTED):\n\n"
                #     f"{best['content']}\n"
                #     f"======================================\n\n"
                # )

                if result:
                    # Record posted tweet
                    posted = PostedTweet(
                        tweet_id=result["tweet_id"],
                        tweet_type=result.get("type", "tweet"),
                        content=result["content"],
                        media_attached=result.get("media_attached", False),
                    )
                    db.add(posted)
                    source.is_processed = True
                    posted_count += 1

                    logger.info(
                        "Successfully posted tweet",
                        extra={
                            "tweet_id": result["tweet_id"],
                            "style": best["style"],
                            "source_type": source.source_type,
                            "author": source.author,
                        },
                    )
                    
                    # Delay to avoid anti-spam limits when multiple tweets are posted sequentially
                    import asyncio
                    await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"Pipeline error for source {source.id}: {e}")
                continue

        await db.commit()

        # Cleanup downloaded images
        cleanup_images()

        summary = {
            "collected": len(all_items),
            "new": len(new_sources),
            "posted": posted_count,
        }
        logger.info("=== Pipeline complete ===", extra=summary)
        return summary


async def _engagement():
    """Run engagement cycle (async)."""
    from app.engagement.auto_engage import engagement_manager
    return await engagement_manager.run_engagement_cycle()



# --- Celery Tasks ---

@celery_app.task(
    name="app.scheduler.tasks.collect_and_post",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def collect_and_post(self):
    """Main pipeline task: collect → deduplicate → generate → post."""
    try:
        result = _run_async(_pipeline())
        logger.info("collect_and_post completed", extra={"result": result})
        return result
    except Exception as e:
        logger.error(f"collect_and_post failed: {e}")
        raise self.retry(exc=e)


@celery_app.task(
    name="app.scheduler.tasks.run_engagement",
    bind=True,
    max_retries=1,
    default_retry_delay=600,
)
def run_engagement(self):
    """Engagement task: reply to trending, quote viral posts."""
    try:
        result = _run_async(_engagement())
        logger.info("run_engagement completed", extra={"result": result})
        return result
    except Exception as e:
        logger.error(f"run_engagement failed: {e}")
        raise self.retry(exc=e)


@celery_app.task(name="app.scheduler.tasks.manual_trigger")
def manual_trigger():
    """Manually triggered pipeline run (from API)."""
    news_result = _run_async(_pipeline())
    return {"news": news_result}
