"""
app/services/prediction_service.py
The core OracleX engine. Chains:
  1. Gather facts (odds + sentiment + injuries + form)
  2. Groq llama-3.3-70b-versatile — streaming narrative + prediction
  3. Persist to Supabase predictions table
  4. Return SSE stream to frontend
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional
from uuid import UUID

from groq import AsyncGroq
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.database import get_supabase
from app.core.logging import get_logger
from app.models.schemas import (
    GameRecord,
    OddsResponse,
    PredictionFactors,
    PredictionResult,
    SentimentPair,
    StreamChunk,
)
from app.scrapers.espn_scraper import espn_scraper
from app.services.odds_service import odds_service
from app.services.sentiment_service import sentiment_service

log = get_logger(__name__)
settings = get_settings()

CACHE_TTL_PREDICTION = 3600  # 1 hour

NARRATIVE_SYSTEM_PROMPT = """You are OracleX — an AI oracle that reads sports narratives before they unfold.
You combine the razor insight of a Las Vegas sharp, the voice of a sports novelist, and the precision of a data scientist.
You tell the story of what WILL happen — present tense, as if you are watching the future unfold.

Your predictions carry authority. You do not hedge. You do not say "maybe" or "might."
You speak with the quiet confidence of someone who has already seen the outcome.

You love drama. You find the narrative thread — the revenge game, the struggling star, the underdog's last stand.
Every game has a story. Your job is to find it and tell it before it happens."""


def build_prediction_prompt(
    game: GameRecord,
    odds: Optional[OddsResponse],
    sentiment: Optional[SentimentPair],
    home_injuries: str,
    away_injuries: str,
    home_form: str,
    away_form: str,
) -> str:
    # Odds block
    if odds:
        home_ml = odds.best_home_odds or "N/A"
        away_ml = odds.best_away_odds or "N/A"

        def implied(american_odds) -> str:
            if isinstance(american_odds, (int, float)):
                if american_odds > 0:
                    p = 100 / (american_odds + 100)
                else:
                    p = abs(american_odds) / (abs(american_odds) + 100)
                return f"{p:.1%}"
            return "N/A"

        home_prob = implied(home_ml)
        away_prob = implied(away_ml)
        spread_str = f"{game.home_team} {odds.consensus_spread:+.1f}" if odds.consensus_spread else "N/A"
        ou_str = str(odds.consensus_over_under) if odds.consensus_over_under else "N/A"
        odds_block = f"""
MARKET ODDS (as of latest fetch):
- {game.home_team} moneyline: {home_ml} → {home_prob} implied win probability
- {game.away_team} moneyline: {away_ml} → {away_prob} implied win probability
- Spread: {spread_str}
- Over/Under: {ou_str}
- Bookmakers tracked: {odds.bookmakers_count}"""
    else:
        odds_block = "\nMARKET ODDS: Not available"

    # Sentiment block
    if sentiment:
        sent_block = f"""
CROWD SENTIMENT (Reddit + news, last 24 hours):
- {game.home_team}: {sentiment.home.score:+.2f}/1.0
  Signals: {'; '.join(sentiment.home.key_signals)}
  Summary: {sentiment.home.summary}
- {game.away_team}: {sentiment.away.score:+.2f}/1.0
  Signals: {'; '.join(sentiment.away.key_signals)}
  Summary: {sentiment.away.summary}
- Edge: {sentiment.edge}"""
    else:
        sent_block = "\nCROWD SENTIMENT: Not available"

    return f"""Game: {game.away_team} @ {game.home_team}
Sport: {game.sport}
Kickoff/Tipoff: {game.game_time.strftime('%A %B %d, %Y at %I:%M %p UTC')}

{odds_block}

{sent_block}

INJURY REPORT:
- {game.home_team}: {home_injuries}
- {game.away_team}: {away_injuries}

RECENT FORM:
- {home_form}
- {away_form}

─────────────────────────────────────────────────────────────

Tell me the story of this game before it happens.

Structure your response EXACTLY as follows — use these exact headers:

## THE NARRATIVE
[3-4 sentences. Present tense. Cinematic. Find the storyline — the revenge arc, the momentum, the pivotal matchup. Make the reader feel the game before it starts.]

## THE VERDICT
Winner: [team name]
Confidence: [XX%]

## KEY FACTORS
1. [Factor 1 — max 15 words]
2. [Factor 2 — max 15 words]
3. [Factor 3 — max 15 words]

## UPSET WATCH
[1-2 sentences. How could the underdog win? What scenario breaks the prediction?]

## THE BET
[Specific recommendation: market + line + brief rationale. Example: "Home team -3.5, juiced to -115, offers value given the 67% implied probability."]

Speak with certainty. Be the oracle."""


class PredictionService:
    def __init__(self):
        self._groq = AsyncGroq(api_key=settings.groq_api_key)

    # ─── Full streaming prediction ────────────────────────────────────────────

    async def stream_prediction(
        self, game_id: str
    ) -> AsyncGenerator[str, None]:
        """
        Main SSE generator. Yields Server-Sent Events strings.
        Usage in FastAPI: StreamingResponse(service.stream_prediction(id), media_type="text/event-stream")
        """
        try:
            # 1. Check cache for complete prediction
            cache = await get_cache()
            cache_key = f"prediction:full:{game_id}"
            cached = await cache.get(cache_key)
            if cached:
                # Stream cached narrative character by character for the effect
                yield self._sse("metadata", cached)
                for chunk in self._chunk_text(cached.get("narrative", ""), size=4):
                    yield self._sse("narrative", chunk)
                    await asyncio.sleep(0.02)
                yield self._sse("done", "")
                return

            # 2. Load game record
            db = get_supabase()
            row = db.table("games").select("*").eq("id", game_id).single().execute()
            if not row.data:
                yield self._sse("error", "Game not found")
                return

            game = GameRecord(**row.data)

            # 3. Gather context in parallel
            yield self._sse("status", "Gathering intelligence...")
            odds, sentiment, injuries = await asyncio.gather(
                odds_service.get_live_odds(game_id),
                sentiment_service.analyze_game_sentiment(
                    game_id, game.home_team, game.away_team, game.sport
                ),
                espn_scraper.get_injuries(game.sport),
                return_exceptions=True,
            )

            # Handle exceptions from gather
            if isinstance(odds, Exception):
                log.warning("stream.odds.error", error=str(odds))
                odds = None
            if isinstance(sentiment, Exception):
                log.warning("stream.sentiment.error", error=str(sentiment))
                sentiment = None
            if isinstance(injuries, Exception):
                injuries = []

            # Recent form
            home_form_data, away_form_data = await asyncio.gather(
                espn_scraper.get_team_recent_form(game.sport, game.home_team),
                espn_scraper.get_team_recent_form(game.sport, game.away_team),
                return_exceptions=True,
            )
            if isinstance(home_form_data, Exception):
                home_form_data = {}
            if isinstance(away_form_data, Exception):
                away_form_data = {}

            home_inj = espn_scraper.format_injury_report(
                espn_scraper.get_team_injuries(injuries if isinstance(injuries, list) else [], game.home_team)
            )
            away_inj = espn_scraper.format_injury_report(
                espn_scraper.get_team_injuries(injuries if isinstance(injuries, list) else [], game.away_team)
            )
            home_form = espn_scraper.format_form(home_form_data if isinstance(home_form_data, dict) else {}, game.home_team)
            away_form = espn_scraper.format_form(away_form_data if isinstance(away_form_data, dict) else {}, game.away_team)

            # 4. Build prompt
            prompt = build_prediction_prompt(
                game, odds, sentiment if isinstance(sentiment, SentimentPair) else None,
                home_inj, away_inj, home_form, away_form,
            )

            # 5. Stream Groq narrative
            yield self._sse("status", "Oracle is reading the future...")
            full_narrative = ""

            stream = await self._groq.chat.completions.create(
                model=settings.groq_narrative_model,
                messages=[
                    {"role": "system", "content": NARRATIVE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=800,
                temperature=0.7,
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    full_narrative += delta
                    yield self._sse("narrative", delta)

            # 6. Parse structured fields from narrative
            prediction = self._parse_narrative(full_narrative, game, sentiment if isinstance(sentiment, SentimentPair) else None)

            # 7. Persist to DB
            saved_id = await self._save_prediction(game_id, prediction)
            prediction.id = saved_id

            # 8. Send final metadata
            meta = {
                "predicted_winner": prediction.predicted_winner,
                "confidence": prediction.confidence,
                "key_factors": [f.model_dump() for f in prediction.key_factors],
                "upset_watch": prediction.upset_watch,
                "bet_recommendation": prediction.bet_recommendation,
                "sentiment": prediction.sentiment.model_dump(mode="json") if prediction.sentiment else None,
                "prediction_id": str(saved_id) if saved_id else None,
            }
            yield self._sse("metadata", meta)

            # Cache full result
            meta["narrative"] = full_narrative
            await cache.set(cache_key, meta, ttl_seconds=CACHE_TTL_PREDICTION)

            yield self._sse("done", "")

        except Exception as e:
            log.error("stream.prediction.error", game_id=game_id, error=str(e))
            yield self._sse("error", f"Prediction failed: {str(e)}")

    # ─── Non-streaming (cached) prediction ───────────────────────────────────

    async def get_prediction(self, game_id: str) -> Optional[dict]:
        """Return cached prediction without streaming. For REST endpoint."""
        db = get_supabase()
        rows = (
            db.table("predictions")
            .select("*")
            .eq("game_id", game_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if rows.data:
            return rows.data[0]
        return None

    # ─── Parse structured fields from narrative text ──────────────────────────

    def _parse_narrative(
        self,
        text: str,
        game: GameRecord,
        sentiment: Optional[SentimentPair],
    ) -> PredictionResult:
        """Extract structured fields from the narrative text."""
        # Winner
        winner = game.home_team  # default
        verdict_match = re.search(r"Winner:\s*(.+)", text, re.IGNORECASE)
        if verdict_match:
            winner_raw = verdict_match.group(1).strip()
            # Match to actual team name
            if game.away_team.lower() in winner_raw.lower():
                winner = game.away_team
            else:
                winner = game.home_team

        # Confidence
        confidence = 0.60  # default
        conf_match = re.search(r"Confidence:\s*(\d+)%", text, re.IGNORECASE)
        if conf_match:
            confidence = int(conf_match.group(1)) / 100.0

        # Key factors
        factors = []
        kf_section = re.search(r"## KEY FACTORS\n(.*?)(?=##|\Z)", text, re.DOTALL | re.IGNORECASE)
        if kf_section:
            for line in kf_section.group(1).strip().split("\n"):
                line = re.sub(r"^\d+\.\s*", "", line).strip()
                if line:
                    factors.append(PredictionFactors(
                        factor=line[:60],
                        weight="high" if "critical" in line.lower() or "key" in line.lower() else "medium",
                        detail=line,
                    ))

        # Upset watch
        upset = ""
        upset_match = re.search(r"## UPSET WATCH\n(.*?)(?=##|\Z)", text, re.DOTALL | re.IGNORECASE)
        if upset_match:
            upset = upset_match.group(1).strip()

        # Bet recommendation
        bet = ""
        bet_match = re.search(r"## THE BET\n(.*?)(?=##|\Z)", text, re.DOTALL | re.IGNORECASE)
        if bet_match:
            bet = bet_match.group(1).strip()

        return PredictionResult(
            game_id=game.id,
            sport=game.sport,
            home_team=game.home_team,
            away_team=game.away_team,
            predicted_winner=winner,
            confidence=min(max(confidence, 0.0), 1.0),
            narrative=text,
            key_factors=factors[:3],
            upset_watch=upset[:500],
            bet_recommendation=bet[:300],
            sentiment=sentiment,
            model_used=settings.groq_narrative_model,
        )

    # ─── DB persistence ───────────────────────────────────────────────────────

    async def _save_prediction(self, game_id: str, prediction: PredictionResult) -> Optional[UUID]:
        db = get_supabase()
        try:
            res = db.table("predictions").insert({
                "game_id": game_id,
                "predicted_winner": prediction.predicted_winner,
                "confidence": prediction.confidence,
                "narrative": prediction.narrative,
                "key_factors": [f.model_dump() for f in prediction.key_factors],
                "upset_watch": prediction.upset_watch,
                "bet_recommendation": prediction.bet_recommendation,
                "model_used": prediction.model_used,
                "sentiment_data": prediction.sentiment.model_dump(mode="json") if prediction.sentiment else None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            if res.data:
                return UUID(res.data[0]["id"])
        except Exception as e:
            log.error("prediction.save.error", game_id=game_id, error=str(e))
        return None

    # ─── Batch generate predictions ───────────────────────────────────────────

    async def generate_batch_predictions(self) -> Dict[str, str]:
        """Called by scheduler — generate predictions for all upcoming games."""
        db = get_supabase()
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        games = (
            db.table("games")
            .select("id")
            .eq("status", "upcoming")
            .lte("game_time", cutoff)
            .execute()
        )
        results = {}
        for game in (games.data or []):
            game_id = game["id"]
            try:
                # Check if prediction already exists and is fresh
                existing = await self.get_prediction(game_id)
                if existing:
                    results[game_id] = "cached"
                    continue

                # Consume the stream to generate + persist
                async for _ in self.stream_prediction(game_id):
                    pass
                results[game_id] = "generated"
                await asyncio.sleep(2)  # rate limit friendliness
            except Exception as e:
                log.error("batch.prediction.error", game_id=game_id, error=str(e))
                results[game_id] = f"error: {str(e)}"
        return results

    # ─── Accuracy tracking ────────────────────────────────────────────────────

    async def update_accuracy(self) -> int:
        """
        After games go final, check if predictions were correct.
        Matches predicted_winner to actual result from ESPN.
        """
        db = get_supabase()
        # Get finalized games with pending predictions
        rows = (
            db.table("predictions")
            .select("id, game_id, predicted_winner")
            .is_("was_correct", "null")
            .execute()
        )

        updated = 0
        for row in (rows.data or []):
            game = (
                db.table("games")
                .select("status, home_team, away_team, home_score, away_score")
                .eq("id", row["game_id"])
                .single()
                .execute()
            )
            if not game.data or game.data.get("status") != "final":
                continue

            g = game.data
            if g.get("home_score") is None or g.get("away_score") is None:
                continue

            actual_winner = (
                g["home_team"] if g["home_score"] > g["away_score"] else g["away_team"]
            )
            was_correct = row["predicted_winner"].lower() == actual_winner.lower()

            db.table("predictions").update({
                "actual_winner": actual_winner,
                "was_correct": was_correct,
            }).eq("id", row["id"]).execute()
            updated += 1

        log.info("accuracy.updated", count=updated)
        return updated

    # ─── SSE helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _sse(event: str, data) -> str:
        if isinstance(data, (dict, list)):
            payload = json.dumps(data)
        else:
            payload = str(data)
        return f"event: {event}\ndata: {payload}\n\n"

    @staticmethod
    def _chunk_text(text: str, size: int = 4) -> list:
        return [text[i:i+size] for i in range(0, len(text), size)]


prediction_service = PredictionService()

from typing import Dict  # noqa: E402
