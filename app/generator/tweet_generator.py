import datetime
import json
import re
from typing import Any, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.generator.prompts import SYSTEM_PROMPT, GENERATION_PROMPT, REPLY_PROMPT, QUOTE_PROMPT
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

# Initialize OpenAI client
client = AsyncOpenAI(api_key=settings.openai_api_key)


def _extract_context(title: str, body: str) -> str:
    """Extract player/team names from content for better prompt context."""
    known_entities = [
        "Kohli", "Rohit", "Bumrah", "Dhoni", "Jadeja", "Gill", "Pant",
        "Hardik Pandya", "Surya Kumar", "Jasprit", "Ashwin", "Rahul",
        "CSK", "MI", "RCB", "KKR", "SRH", "DC", "PBKS", "RR", "GT", "LSG",
        "India", "Australia", "England", "Pakistan", "South Africa",
        "IPL", "World Cup", "T20", "ODI", "Test", "BCCI", "ICC",
    ]
    combined = f"{title} {body}"
    found = [e for e in known_entities if e.lower() in combined.lower()]
    return ", ".join(found) if found else "cricket"


def _validate_tweet(content: str) -> bool:
    """Validate that a tweet meets Twitter requirements."""
    if len(content) > 280:
        return False
    if len(content.strip()) < 20:
        return False
    # Check for at least 1 hashtag
    hashtags = re.findall(r"#\w+", content)
    if len(hashtags) < 1:
        return False
    return True


def _truncate_tweet(content: str, max_len: int = 280) -> str:
    """Truncate a tweet to fit within the character limit while preserving hashtags."""
    if len(content) <= max_len:
        return content

    # Extract hashtags from the end
    hashtags = re.findall(r"#\w+", content)
    hashtag_text = " " + " ".join(hashtags[-3:]) if hashtags else ""

    # Truncate the main text
    available = max_len - len(hashtag_text) - 3  # 3 for "..."
    main_text = re.sub(r"#\w+", "", content).strip()
    truncated = main_text[:available].rsplit(" ", 1)[0] + "..."

    return truncated + hashtag_text


async def generate_tweet_variants(
    title: str,
    body: str = "",
    item_time: Optional[datetime.datetime] = None,
) -> list[dict[str, Any]]:
    """Generate 4 tweet style variations using OpenAI.

    Args:
        title: Cricket news headline.
        body: Article body or tweet text.
        item_time: Time when the news/match was published.

    Returns:
        List of dicts with keys: style, content, score.
        Sorted by score descending (best first).
    """
    context = _extract_context(title, body)

    # Format times for prompt
    now = datetime.datetime.now(datetime.timezone.utc)
    current_time_str = now.strftime("%Y-%m-%d %H:%M UTC")
    
    if item_time:
        if item_time.tzinfo is None:
            item_time = item_time.replace(tzinfo=datetime.timezone.utc)
        item_time_str = item_time.strftime("%Y-%m-%d %H:%M UTC")
    else:
        item_time_str = "Unknown"

    prompt = GENERATION_PROMPT.format(
        title=title[:300],
        body=(body or "")[:500],
        context=context,
        current_time=current_time_str,
        item_time=item_time_str,
    )

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.9,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        data = json.loads(raw)
        tweets = data.get("tweets", [])

        # Validate and fix each tweet
        validated = []
        for tweet in tweets:
            content = tweet.get("content", "").strip()
            if not content:
                continue

            # Truncate if needed
            content = _truncate_tweet(content)

            if _validate_tweet(content):
                validated.append({
                    "style": tweet.get("style", "casual"),
                    "content": content,
                    "score": float(tweet.get("score", 5.0)),
                })

        # Sort by score descending
        validated.sort(key=lambda x: x["score"], reverse=True)

        logger.info(
            "Generated tweet variants",
            extra={"count": len(validated), "context": context},
        )
        return validated

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse OpenAI response: {e}")
        return []
    except Exception as e:
        logger.error(f"Tweet generation failed: {e}")
        return []


async def generate_reply(original_text: str) -> str | None:
    """Generate an AI reply to a tweet.

    Args:
        original_text: The tweet being replied to.

    Returns:
        Reply text, or None if generation fails.
    """
    prompt = REPLY_PROMPT.format(original_text=original_text[:500])

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.85,
            max_tokens=200,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content)
        reply = data.get("reply", "").strip()

        if reply and len(reply) <= 280:
            return reply
        elif reply:
            return _truncate_tweet(reply)
        return None

    except Exception as e:
        logger.error(f"Reply generation failed: {e}")
        return None


async def generate_quote(original_text: str, engagement: int = 0) -> str | None:
    """Generate a quote tweet commentary.

    Args:
        original_text: The tweet being quote-tweeted.
        engagement: Total engagement count of original.

    Returns:
        Quote text, or None if generation fails.
    """
    prompt = QUOTE_PROMPT.format(
        original_text=original_text[:500],
        engagement=engagement,
    )

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.85,
            max_tokens=200,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content)
        quote = data.get("quote", "").strip()

        if quote and len(quote) <= 280:
            return quote
        elif quote:
            return _truncate_tweet(quote)
        return None

    except Exception as e:
        logger.error(f"Quote generation failed: {e}")
        return None


def select_best_tweet(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the best tweet from generated variants.

    Uses a weighted scoring approach:
    - AI self-score (60% weight)
    - Length bonus: tweets 100-200 chars score higher (20% weight)
    - Hashtag bonus: having 2-3 hashtags (20% weight)

    Args:
        variants: List of tweet dicts with content and score.

    Returns:
        Best scoring tweet dict, or None if empty.
    """
    if not variants:
        return None

    scored = []
    for tweet in variants:
        content = tweet["content"]
        ai_score = tweet.get("score", 5.0)

        # Length bonus: sweet spot is 100-200 chars
        length = len(content)
        if 150 <= length <= 240:
            length_bonus = 10.0
        elif 60 <= length < 150 or 240 < length <= 280:
            length_bonus = 7.0
        else:
            length_bonus = 4.0

        # Hashtag bonus
        hashtag_count = len(re.findall(r"#\w+", content))
        if 2 <= hashtag_count <= 3:
            hashtag_bonus = 10.0
        elif hashtag_count == 1:
            hashtag_bonus = 6.0
        else:
            hashtag_bonus = 3.0

        final_score = (ai_score * 0.6) + (length_bonus * 0.2) + (hashtag_bonus * 0.2)
        scored.append({**tweet, "final_score": final_score})

    scored.sort(key=lambda x: x["final_score"], reverse=True)
    best = scored[0]

    logger.info(
        "Selected best tweet",
        extra={
            "style": best["style"],
            "score": best["final_score"],
            "length": len(best["content"]),
        },
    )
    return best
