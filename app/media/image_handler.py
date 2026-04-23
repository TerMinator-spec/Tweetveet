"""Image handler — prefers X media from source, falls back to Unsplash."""

import os
import tempfile
from typing import Optional

import httpx

from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

# Temp directory for downloaded images
IMAGE_CACHE_DIR = os.path.join(tempfile.gettempdir(), "tweetveet_images")
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)


async def _download_image(url: str, filename: str) -> Optional[str]:
    """Download an image from a URL to local temp cache.

    Args:
        url: Image URL to download.
        filename: Local filename to save as.

    Returns:
        Absolute path to downloaded file, or None on failure.
    """
    filepath = os.path.join(IMAGE_CACHE_DIR, filename)

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "image" not in content_type and "octet-stream" not in content_type:
                logger.warning(
                    "URL did not return image content",
                    extra={"url": url, "content_type": content_type},
                )
                return None

            with open(filepath, "wb") as f:
                f.write(response.content)

            size_kb = len(response.content) / 1024
            logger.info(
                "Downloaded image",
                extra={"url": url[:80], "size_kb": round(size_kb, 1)},
            )
            return filepath

    except Exception as e:
        logger.error(f"Image download failed: {e}", extra={"url": url[:80]})
        return None


# async def _fetch_from_unsplash(query: str) -> Optional[str]:
#     """Search Unsplash for a cricket-related image.

#     Args:
#         query: Search keywords (e.g., "Kohli cricket").

#     Returns:
#         Image URL or None.
#     """
#     if not settings.unsplash_access_key:
#         logger.debug("Unsplash key not configured — skipping")
#         return None

#     url = "https://api.unsplash.com/search/photos"
#     params = {
#         "query": f"{query} cricket",
#         "per_page": 5,
#         "orientation": "landscape",
#         "content_filter": "high",
#     }
#     headers = {"Authorization": f"Client-ID {settings.unsplash_access_key}"}

#     try:
#         async with httpx.AsyncClient(timeout=10.0) as client:
#             response = await client.get(url, headers=headers, params=params)
#             response.raise_for_status()
#             data = response.json()

#         results = data.get("results", [])
#         if results:
#             # Pick the first result's regular-size URL
#             image_url = results[0]["urls"].get("regular")
#             logger.info("Found Unsplash image", extra={"query": query})
#             return image_url

#         logger.debug("No Unsplash results", extra={"query": query})
#         return None

#     except Exception as e:
#         logger.error(f"Unsplash search failed: {e}")
#         return None


async def get_image_for_tweet(
    source_media_url: Optional[str] = None,
    keywords: str = "cricket",
) -> Optional[str]:
    """Get an image for a tweet, prioritizing X media over Unsplash.

    Strategy (in order of preference):
    1. Use media URL from the original X/Twitter source (if available)
    2. Use media URL from news source (if available)
    3. Search Unsplash as a fallback

    Args:
        source_media_url: Media URL from the collected source (X or news).
        keywords: Search keywords for Unsplash fallback.

    Returns:
        Local file path to the downloaded image, or None.
    """
    if not settings.enable_image_posting:
        return None

    # Priority 1 & 2: Use source media (X media or news article image)
    if source_media_url:
        logger.info("Using source media (preferred)", extra={"url": source_media_url[:80]})
        local_path = await _download_image(source_media_url, "source_media.jpg")
        if local_path:
            return local_path
    # Unsplash fallback removed as per user request.
    # If source media fails, we post without image.
    pass

    logger.info("No image available for tweet")
    return None


def cleanup_images():
    """Remove all cached images from the temp directory."""
    try:
        for filename in os.listdir(IMAGE_CACHE_DIR):
            filepath = os.path.join(IMAGE_CACHE_DIR, filename)
            if os.path.isfile(filepath):
                os.remove(filepath)
        logger.debug("Cleaned up image cache")
    except Exception as e:
        logger.warning(f"Image cleanup failed: {e}")
