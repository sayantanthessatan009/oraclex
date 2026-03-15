"""
app/services/sentiment_service.py
Pulls Reddit posts for each team, scores them with Groq 8b-instant,
aggregates into a -1.0 to +1.0 sentiment score per team.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import List, Optional

from groq import AsyncGroq
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.database import get_supabase
from app.core.logging import get_logger
from app.models.schemas import SentimentPair, TeamSentiment

log = get_logger(__name__)
settings = get_settings()

SUBREDDITS_BY_SPORT = {
    "basketball_nba": ["nba", "sportsbook", "sportsbetting"],
    "americanfootball_nfl": ["nfl", "sportsbook", "sportsbetting"],
    "icehockey_nhl": ["hockey", "nhl", "sportsbook"],
    "baseball_mlb": ["baseball", "mlb", "sportsbook"],
    "mma_mixed_martial_arts": ["mma", "ufc", "sportsbook"],
    "soccer_epl": ["soccer", "eplsoccer", "sportsbook"],
}

CACHE_TTL_SENTIMENT = 1800  # 30 min


class SentimentService:
    def __init__(self):
        self._groq = AsyncGroq(api_key=settings.groq_api_key)

    # ─── Reddit fetch ─────────────────────────────────────────────────────────

    async def fetch_reddit_posts(self, team: str, sport: str, limit: int = 40) -> List[str]:
        """Fetch recent Reddit posts mentioning a team. Returns list of text snippets."""
        try:
            import asyncpraw
            reddit = asyncpraw.Reddit(
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                user_agent=settings.reddit_user_agent,
            )
            subreddits = SUBREDDITS_BY_SPORT.get(sport, ["sportsbook"])
            texts = []

            for sub_name in subreddits[:2]:  # limit to 2 subreddits per call
                try:
                    sub = await reddit.subreddit(sub_name)
                    async for post in sub.search(team, limit=limit // 2, sort="new", time_filter="day"):
                        text = f"{post.title}. {post.selftext[:200]}"
                        texts.append(text.strip())
                except Exception as e:
                    log.warning("reddit.subreddit.error", sub=sub_name, error=str(e))

            await reddit.close()
            log.info("reddit.fetched", team=team, count=len(texts))
            return texts[:limit]

        except Exception as e:
            log.error("reddit.fetch.error", team=team, error=str(e))
            return []

    # ─── Groq scoring ─────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def score_sentiment(self, team: str, texts: List[str]) -> TeamSentiment:
        """Use Groq 8b-instant to score sentiment for a team from Reddit text."""
        if not texts:
            return TeamSentiment(
                team=team, sport="unknown", score=0.0,
                post_count=0, key_signals=[], summary="No data available",
                computed_at=datetime.now(timezone.utc),
            )

        combined = "\n---\n".join(texts[:30])  # cap context size
        prompt = f"""You are a sports betting sentiment analyst. Analyze the following Reddit posts about {team}.

POSTS:
{combined}

Return ONLY valid JSON (no markdown, no backticks) with this exact structure:
{{
  "score": <float between -1.0 and 1.0>,
  "key_signals": [<3 short string bullet points, max 10 words each>],
  "summary": "<one sentence summary of the sentiment, max 20 words>"
}}

Where:
- score: -1.0 = very bearish/negative about team's chances, +1.0 = very bullish/positive
- key_signals: top 3 factors from the posts (injuries, form, lineup, momentum, etc.)
- summary: concise sentiment summary

Respond with JSON only."""

        resp = await self._groq.chat.completions.create(
            model=settings.groq_sentiment_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.1,
        )

        raw = resp.choices[0].message.content.strip()

        # Strip any markdown fences if model added them
        raw = re.sub(r"```json|```", "", raw).strip()

        try:
            parsed = json.loads(raw)
            return TeamSentiment(
                team=team,
                sport="unknown",
                score=float(parsed.get("score", 0.0)),
                post_count=len(texts),
                key_signals=parsed.get("key_signals", []),
                summary=parsed.get("summary", ""),
                computed_at=datetime.now(timezone.utc),
            )
        except json.JSONDecodeError:
            log.error("sentiment.parse.error", team=team, raw=raw[:200])
            return TeamSentiment(
                team=team, sport="unknown", score=0.0,
                post_count=len(texts), key_signals=[],
                summary="Parse error", computed_at=datetime.now(timezone.utc),
            )

    # ─── Full pair analysis ───────────────────────────────────────────────────

    async def analyze_game_sentiment(
        self, game_id: str, home_team: str, away_team: str, sport: str
    ) -> SentimentPair:
        cache = await get_cache()
        cache_key = f"sentiment:{game_id}"
        cached = await cache.get(cache_key)
        if cached:
            return SentimentPair(**cached)

        home_texts, away_texts = await asyncio.gather(
            self.fetch_reddit_posts(home_team, sport),
            self.fetch_reddit_posts(away_team, sport),
        )

        home_sentiment, away_sentiment = await asyncio.gather(
            self.score_sentiment(home_team, home_texts),
            self.score_sentiment(away_team, away_texts),
        )

        home_sentiment.sport = sport
        away_sentiment.sport = sport

        diff = home_sentiment.score - away_sentiment.score
        if abs(diff) < 0.1:
            edge = "neutral"
        elif diff > 0:
            edge = f"{home_team} has crowd sentiment edge (+{diff:.2f})"
        else:
            edge = f"{away_team} has crowd sentiment edge (+{abs(diff):.2f})"

        pair = SentimentPair(home=home_sentiment, away=away_sentiment, edge=edge)

        # Persist to Supabase
        await self._store_sentiment(game_id, pair)
        await cache.set(cache_key, pair.model_dump(mode="json"), ttl_seconds=CACHE_TTL_SENTIMENT)

        return pair

    async def _store_sentiment(self, game_id: str, pair: SentimentPair) -> None:
        db = get_supabase()
        try:
            db.table("sentiment_scores").upsert({
                "game_id": game_id,
                "home_team": pair.home.team,
                "away_team": pair.away.team,
                "home_score": pair.home.score,
                "away_score": pair.away.score,
                "home_signals": pair.home.key_signals,
                "away_signals": pair.away.key_signals,
                "home_summary": pair.home.summary,
                "away_summary": pair.away.summary,
                "edge": pair.edge,
                "post_count_home": pair.home.post_count,
                "post_count_away": pair.away.post_count,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="game_id").execute()
        except Exception as e:
            log.error("sentiment.store.error", game_id=game_id, error=str(e))


import asyncio  # noqa: E402 (needed for gather)

sentiment_service = SentimentService()
