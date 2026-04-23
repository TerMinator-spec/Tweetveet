"""News API collector (NewsAPI.org + GNews) for cricket content."""

import time
from datetime import datetime, timezone
from typing import Any

import httpx
import feedparser

from app.collectors.base import BaseCollector
from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class NewsCollector(BaseCollector):
    """Collects cricket news from NewsAPI.org and GNews APIs.

    Aggregates results from both sources and normalizes them.
    Either or both APIs can be disabled via empty keys.
    """

    NEWSAPI_URL = "https://newsapi.org/v2/everything"
    GNEWS_URL = "https://gnews.io/api/v4/search"

    def __init__(self):
        super().__init__("news")
        self.newsapi_key = settings.newsapi_key
        self.gnews_key = settings.gnews_api_key
        self.cricdata_key = getattr(settings, 'cricdata_api_key', "")
        self.espncricinfo_url = settings.espncricinfo_rss_url

    async def _fetch_newsapi(self) -> list[dict[str, Any]]:
        """Fetch cricket articles from NewsAPI.org."""
        if not self.newsapi_key:
            self.logger.info("NewsAPI key not set — skipping")
            return []

        params = {
            "q": "cricket OR IPL OR \"T20 World Cup\" OR BCCI",
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 30,
            "apiKey": self.newsapi_key,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(self.NEWSAPI_URL, params=params)

            if response.status_code == 429:
                self.logger.warning("NewsAPI rate limited")
                return []

            response.raise_for_status()
            data = response.json()

        articles = data.get("articles", [])
        # Tag source for normalization
        for article in articles:
            article["_source"] = "newsapi"
        return articles

    async def _fetch_gnews(self) -> list[dict[str, Any]]:
        """Fetch cricket articles from GNews API."""
        if not self.gnews_key:
            self.logger.info("GNews key not set — skipping")
            return []

        params = {
            "q": "cricket",
            "lang": "en",
            "max": 30,
            "sortby": "publishedAt",
            "token": self.gnews_key,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(self.GNEWS_URL, params=params)

            if response.status_code == 429:
                self.logger.warning("GNews rate limited")
                return []

            response.raise_for_status()
            data = response.json()

        articles = data.get("articles", [])
        for article in articles:
            article["_source"] = "gnews"
        return articles

    async def _fetch_cricdata(self) -> list[dict[str, Any]]:
        """Fetch live match updates from Cricdata (cricapi.com) as news."""
        if not self.cricdata_key:
            self.logger.info("Cricdata key not set — skipping")
            return []

        url = "https://api.cricapi.com/v1/currentMatches"
        params = {
            "apikey": self.cricdata_key,
            "offset": 0
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                self.logger.warning(f"Cricdata fetch failed: {e}")
                return []

        matches = data.get("data", [])
        articles = []
        for match in matches:
            if not match.get("matchStarted"):
                continue

            name = match.get("name", "").upper()
            teams = [t.upper() for t in match.get("teams", [])]
            
            # Filter for IPL and International matches of top 10 countries
            ipl_teams = ["CSK", "MI", "RCB", "KKR", "SRH", "DC", "PBKS", "RR", "GT", "LSG", 
                         "CHENNAI SUPER KINGS", "MUMBAI INDIANS", "ROYAL CHALLENGERS BENGALURU", 
                         "ROYAL CHALLENGERS BANGALORE", "KOLKATA KNIGHT RIDERS", "SUNRISERS HYDERABAD", 
                         "DELHI CAPITALS", "PUNJAB KINGS", "RAJASTHAN ROYALS", "GUJARAT TITANS", "LUCKNOW SUPER GIANTS"]
            is_ipl = "IPL" in name or "INDIAN PREMIER LEAGUE" in name or any(t.strip() in ipl_teams for t in teams)
            
            is_intl = any(k in name for k in ("T20I", "ODI", "TEST", "WORLD CUP", "ICC", "TOUR"))
            match_type = match.get("matchType", "").lower()
            is_intl_type = is_intl or match_type in ("odi", "t20i", "test")
            
            top_10 = ("INDIA", "AUSTRALIA", "ENGLAND", "SOUTH AFRICA", "NEW ZEALAND", 
                      "PAKISTAN", "SRI LANKA", "WEST INDIES", "BANGLADESH", "AFGHANISTAN")
            has_top_10 = any(c in name for c in top_10) or any(c in t for t in teams for c in top_10)
            
            if not (is_ipl or (is_intl_type and has_top_10)):
                continue

            # 1. Prepare Match Summary
            title_summary = f"{match.get('name')} - {match.get('status')}"
            
            score_texts = []
            for score in match.get("score", []):
                score_texts.append(f"{score.get('inning')}: {score.get('r')}/{score.get('w')} ({score.get('o')} ov)")
            score_str = " | ".join(score_texts)
            body_summary = f"Match: {match.get('name')}. Venue: {match.get('venue')}. Status: {match.get('status')}. Scores: {score_str}"
            
            published_at = match.get("dateTimeGMT")
            if published_at and not published_at.endswith("Z"):
                published_at += "Z"
                
            summary_article = {
                "title": title_summary[:500],
                "description": body_summary,
                "url": f"https://cricapi.com/match/{match.get('id')}", 
                "publishedAt": published_at,
                "_source": "cricdata",
                "author": "CricAPI",
                "is_live": match.get("matchStarted") and not match.get("matchEnded")
            }
            articles.append(summary_article)

            # 2. Fetch Scorecard and Prepare Player Spotlight
            best_player_name = None
            best_player_stats = ""
            try:
                sc_url = "https://api.cricapi.com/v1/match_scorecard"
                sc_params = {"apikey": self.cricdata_key, "id": match.get("id")}
                async with httpx.AsyncClient(timeout=20.0) as sc_client:
                    sc_resp = await sc_client.get(sc_url, params=sc_params)
                if sc_resp.status_code == 200:
                    sc_data = sc_resp.json()
                    scorecards = sc_data.get("data", {}).get("scorecard", [])
                    
                    best_batsman = None
                    max_runs = -1
                    best_bowler = None
                    max_wickets = -1
                    
                    for inning in scorecards:
                        for bat in inning.get("batting", []):
                            runs = bat.get("r", 0)
                            if runs > max_runs:
                                max_runs = runs
                                best_batsman = bat
                                
                        for bowl in inning.get("bowling", []):
                            wickets = bowl.get("w", 0)
                            if wickets > max_wickets:
                                max_wickets = wickets
                                best_bowler = bowl
                    
                    # Decide if bowler outshines batsman
                    if max_wickets >= 4 or (max_wickets == 3 and max_runs < 50):
                        if best_bowler:
                            best_player_name = best_bowler.get("bowler", {}).get("name")
                            best_player_stats = f"Sensational bowling: {best_bowler.get('w')} wickets for {best_bowler.get('r')} runs in {best_bowler.get('o')} overs!"
                    elif best_batsman:
                        best_player_name = best_batsman.get("batsman", {}).get("name")
                        best_player_stats = f"Brilliant batting: {best_batsman.get('r')} runs off {best_batsman.get('b')} balls (Fours: {best_batsman.get('4s', 0)}, Sixes: {best_batsman.get('6s', 0)})!"
            except Exception as e:
                self.logger.warning(f"Failed to fetch scorecard for {match.get('id')}: {e}")

            if best_player_name:
                player_title = f"{best_player_name} shines in {match.get('name')}"
                player_body = f"Player Spotlight: {best_player_name}. {best_player_stats} Match: {match.get('name')}. Status: {match.get('status')}."
                player_article = {
                    "title": player_title[:500],
                    "description": player_body,
                    "url": f"https://cricapi.com/match/{match.get('id')}/player", 
                    "publishedAt": published_at,
                    "_source": "cricdata",
                    "author": "CricAPI",
                    "is_live": match.get("matchStarted") and not match.get("matchEnded")
                }
                articles.append(player_article)
            
        return articles

    async def _fetch_espncricinfo(self) -> list[dict[str, Any]]:
        """Fetch latest news from ESPN Cricinfo RSS feed."""
        if not self.espncricinfo_url:
            self.logger.info("ESPN Cricinfo RSS URL not set — skipping")
            return []

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                response = await client.get(self.espncricinfo_url)
                response.raise_for_status()
                feed_data = response.text
            except Exception as e:
                self.logger.warning(f"ESPN Cricinfo RSS fetch failed: {e}")
                return []

        feed = feedparser.parse(feed_data)
        articles = []
        for entry in feed.entries:
            # Extract image from media:content or coverImages if available
            media_url = None
            if 'media_content' in entry and entry.media_content:
                media_url = entry.media_content[0].get('url')
            elif 'coverimages' in entry:
                media_url = entry.coverimages

            # Normalize published date
            pub_date = None
            if 'published_parsed' in entry:
                pub_date = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
            elif 'published' in entry:
                # Basic string fallback if parser missed it
                pub_date = entry.published

            article = {
                "title": entry.get("title", ""),
                "description": entry.get("summary") or entry.get("description", ""),
                "url": entry.get("link", ""),
                "publishedAt": pub_date.isoformat() if isinstance(pub_date, datetime) else pub_date,
                "_source": "espncricinfo",
                "author": "ESPN Cricinfo",
                "media_url": media_url
            }
            articles.append(article)

        return articles

    async def _fetch(self) -> list[dict[str, Any]]:
        """Fetch from both NewsAPI and GNews concurrently."""
        import asyncio

        results = await asyncio.gather(
            self._fetch_newsapi(),
            self._fetch_gnews(),
            self._fetch_cricdata(),
            self._fetch_espncricinfo(),
            return_exceptions=True,
        )

        all_articles = []
        for result in results:
            if isinstance(result, Exception):
                self.logger.error(f"Sub-fetch failed: {result}")
                continue
            all_articles.extend(result)

        # Filter for today's news (last 24 hours)
        now = datetime.now(timezone.utc)
        recent_articles = []
        for item in all_articles:
            if item.get("_source") == "cricdata" and item.get("is_live"):
                recent_articles.append(item)
                continue

            pub_raw = item.get("publishedAt")
            if not pub_raw:
                continue
            try:
                pub_date = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                
                # For Cricdata, publishedAt is the match START time. A match could easily end 5-10 hours 
                # after it starts, so we need a larger age window (24 hrs) for Cricdata items to catch recently ended matches.
                max_age = 86400 if item.get("_source") == "cricdata" else 43200
                
                if (now - pub_date).total_seconds() <= max_age:
                    recent_articles.append(item)
            except Exception:
                pass

        return recent_articles

    def _normalize(self, item: dict[str, Any]) -> dict[str, Any]:
        """Normalize a news article into CricketSource fields."""
        source_tag = item.get("_source", "newsapi")
        # Map cricdata/espncricinfo to newsapi to bypass PostgreSQL ENUM error
        source_type = source_tag if source_tag in ("newsapi", "gnews") else "newsapi"

        # Parse published date
        published = None
        published_raw = item.get("publishedAt")
        if published_raw:
            try:
                published = datetime.fromisoformat(
                    published_raw.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                published = datetime.now(timezone.utc)

        # Extract image — NewsAPI uses 'urlToImage', GNews uses 'image'
        image_url = item.get("urlToImage") or item.get("image")

        # Build title from headline
        title = item.get("title", "")
        if not title:
            title = (item.get("description") or "")[:500]

        return {
            "source_type": source_type,
            "external_id": None,
            "title": title[:500],
            "body": item.get("description") or item.get("content") or "",
            "url": item.get("url", ""),
            "author": item.get("author") or item.get("source", {}).get("name", ""),
            "media_url": image_url,
            "published_at": published,
            "engagement_score": 0.0,
        }
