"""Abstract base collector with retry logic."""

from abc import ABC, abstractmethod
from typing import Any

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class BaseCollector(ABC):
    """Abstract base for all data collectors.

    Subclasses must implement `_fetch()` which returns raw items,
    and `_normalize()` which converts them to CricketSource-compatible dicts.
    """

    def __init__(self, name: str):
        self.name = name
        self.logger = setup_logger(f"collector.{name}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        reraise=True,
    )
    async def collect(self) -> list[dict[str, Any]]:
        """Collect and normalize cricket content from the source.

        Returns:
            List of dicts compatible with CricketSource model fields.
        """
        self.logger.info("Starting collection", extra={"collector": self.name})
        try:
            raw_items = await self._fetch()
            self.logger.info(
                "Fetched raw items",
                extra={"collector": self.name, "count": len(raw_items)},
            )
            normalized = []
            for item in raw_items:
                try:
                    normalized.append(self._normalize(item))
                except Exception as e:
                    self.logger.warning(
                        "Failed to normalize item",
                        extra={"collector": self.name, "error": str(e)},
                    )
            self.logger.info(
                "Collection complete",
                extra={"collector": self.name, "normalized_count": len(normalized)},
            )
            return normalized
        except Exception as e:
            self.logger.error(
                "Collection failed",
                extra={"collector": self.name, "error": str(e)},
            )
            raise

    @abstractmethod
    async def _fetch(self) -> list[dict[str, Any]]:
        """Fetch raw items from the data source."""
        ...

    @abstractmethod
    def _normalize(self, item: dict[str, Any]) -> dict[str, Any]:
        """Normalize a raw item into CricketSource-compatible dict.

        Must return dict with keys:
            source_type, external_id, title, body, url, author,
            media_url, published_at, engagement_score
        """
        ...
