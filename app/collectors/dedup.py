"""Deduplication engine using SHA-256 exact hashing and SimHash fuzzy matching."""

import hashlib
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tweet import CricketSource
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


def _normalize_text(text: str) -> str:
    """Normalize text for hashing: lowercase, strip whitespace/punctuation."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def compute_content_hash(title: str, body: str = "") -> str:
    """Compute SHA-256 hash of normalized title + body for exact dedup.

    Args:
        title: Content title or headline.
        body: Content body text.

    Returns:
        64-char hex digest.
    """
    combined = _normalize_text(title) + "|" + _normalize_text(body or "")
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _simhash_tokens(text: str) -> int:
    """Compute a 64-bit SimHash fingerprint for fuzzy deduplication.

    Uses word-level shingles (3-grams) and MD5 per shingle.
    """
    text = _normalize_text(text)
    words = text.split()
    if len(words) < 3:
        shingles = [text]
    else:
        shingles = [" ".join(words[i: i + 3]) for i in range(len(words) - 2)]

    v = [0] * 64
    for shingle in shingles:
        h = int(hashlib.md5(shingle.encode("utf-8")).hexdigest(), 16)
        for i in range(64):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1

    fingerprint = 0
    for i in range(64):
        if v[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def compute_simhash(title: str, body: str = "") -> str:
    """Compute SimHash as a hex string.

    Args:
        title: Content title.
        body: Content body.

    Returns:
        16-char hex string representing the 64-bit SimHash.
    """
    combined = f"{title} {body or ''}"
    fingerprint = _simhash_tokens(combined)
    return f"{fingerprint:016x}"


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Compute Hamming distance between two hex-encoded SimHash values.

    Args:
        hash_a: First SimHash hex string.
        hash_b: Second SimHash hex string.

    Returns:
        Number of differing bits (0 = identical).
    """
    a = int(hash_a, 16)
    b = int(hash_b, 16)
    xor = a ^ b
    return bin(xor).count("1")


async def is_duplicate(
    db: AsyncSession,
    title: str,
    body: str = "",
    simhash_threshold: int = 3,
) -> bool:
    """Check if content is a duplicate using exact hash and SimHash.

    Args:
        db: Database session.
        title: Content title.
        body: Content body.
        simhash_threshold: Max Hamming distance for fuzzy match (default 3).

    Returns:
        True if duplicate found, False otherwise.
    """
    content_hash = compute_content_hash(title, body)

    # 1. Exact hash check
    result = await db.execute(
        select(CricketSource.id).where(CricketSource.content_hash == content_hash).limit(1)
    )
    if result.scalar_one_or_none() is not None:
        logger.debug("Exact duplicate found", extra={"hash": content_hash[:12]})
        return True

    # 2. Fuzzy SimHash check against recent sources (last 500)
    new_simhash = compute_simhash(title, body)
    result = await db.execute(
        select(CricketSource.simhash)
        .where(CricketSource.simhash.isnot(None))
        .order_by(CricketSource.collected_at.desc())
        .limit(500)
    )
    existing_hashes = result.scalars().all()

    for existing_hash in existing_hashes:
        if hamming_distance(new_simhash, existing_hash) <= simhash_threshold:
            logger.debug(
                "Fuzzy duplicate found",
                extra={"distance": hamming_distance(new_simhash, existing_hash)},
            )
            return True

    return False


async def deduplicate_and_store(
    db: AsyncSession,
    items: list[dict[str, Any]],
) -> list[CricketSource]:
    """Filter duplicates and store new items in the database.

    Args:
        db: Database session.
        items: List of normalized dicts from collectors.

    Returns:
        List of newly created CricketSource objects.
    """
    new_sources = []

    for item in items:
        title = item.get("title", "")
        body = item.get("body", "")

        if not title:
            continue

        if await is_duplicate(db, title, body):
            continue

        source = CricketSource(
            source_type=item["source_type"],
            external_id=item.get("external_id"),
            title=title,
            body=body,
            url=item.get("url"),
            author=item.get("author"),
            media_url=item.get("media_url"),
            content_hash=compute_content_hash(title, body),
            simhash=compute_simhash(title, body),
            engagement_score=item.get("engagement_score", 0.0),
            published_at=item.get("published_at"),
        )
        db.add(source)
        new_sources.append(source)

    if new_sources:
        await db.flush()
        logger.info(
            "Stored new sources",
            extra={"count": len(new_sources), "total_checked": len(items)},
        )

    return new_sources
