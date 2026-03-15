"""
app/services/odds_service.py
Fetches live odds from The Odds API, normalises them, and upserts to Supabase.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.database import get_supabase
from app.core.logging import get_logger
from app.models.schemas import BookmakerMarket, BookmakerOdds, GameOdds, OddsOutcome, OddsResponse, Sport

log = get_logger(__name__)
settings = get_settings()

# Sports to track — extend as needed
TRACKED_SPORTS = [
    Sport.NBA.value,
    Sport.NFL.value,
    Sport.NHL.value,
    Sport.MLB.value,
]

CACHE_TTL_ODDS = 300  # 5 min


class OddsService:
    def __init__(self):
        self._base = settings.odds_api_base_url
        self._key = settings.odds_api_key

    # ─── Fetch from API ───────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_odds(self, sport: str, markets: str = "h2h,spreads,totals") -> List[GameOdds]:
        """Fetch current odds for a sport from The Odds API."""
        url = f"{self._base}/sports/{sport}/odds"
        params = {
            "apiKey": self._key,
            "regions": "us",
            "markets": markets,
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            remaining = resp.headers.get("x-requests-remaining", "?")
            log.info("odds.fetched", sport=sport, remaining=remaining)
            return self._parse_odds_response(resp.json(), sport)

    def _parse_odds_response(self, data: list, sport: str) -> List[GameOdds]:
        games = []
        for raw in data:
            bookmakers = []
            for bm in raw.get("bookmakers", []):
                markets = []
                for mkt in bm.get("markets", []):
                    outcomes = [
                        OddsOutcome(
                            name=o["name"],
                            price=float(o["price"]),
                            point=o.get("point"),
                        )
                        for o in mkt.get("outcomes", [])
                    ]
                    markets.append(BookmakerMarket(
                        key=mkt["key"],
                        outcomes=outcomes,
                        last_update=mkt.get("last_update"),
                    ))
                bookmakers.append(BookmakerOdds(
                    key=bm["key"],
                    title=bm["title"],
                    markets=markets,
                ))

            games.append(GameOdds(
                game_id=raw["id"],
                sport=sport,
                home_team=raw["home_team"],
                away_team=raw["away_team"],
                commence_time=datetime.fromisoformat(raw["commence_time"].replace("Z", "+00:00")),
                bookmakers=bookmakers,
            ))
        return games

    # ─── Upsert to Supabase ───────────────────────────────────────────────────

    async def upsert_game(self, game: GameOdds) -> str:
        """Upsert a game record and return the internal UUID."""
        db = get_supabase()
        payload = {
            "external_id": game.game_id,
            "sport": game.sport,
            "home_team": game.home_team,
            "away_team": game.away_team,
            "game_time": game.commence_time.isoformat(),
            "status": "upcoming",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        res = (
            db.table("games")
            .upsert(payload, on_conflict="external_id")
            .execute()
        )
        if res.data:
            return res.data[0]["id"]
        raise ValueError(f"Failed to upsert game {game.game_id}")

    async def store_odds_snapshot(self, game_uuid: str, game: GameOdds) -> None:
        """Write current odds to odds_history for line-movement tracking."""
        db = get_supabase()
        rows = []
        now = datetime.now(timezone.utc).isoformat()

        for bm in game.bookmakers:
            for mkt in bm.markets:
                if mkt.key == "h2h":
                    home_odds = next((o.price for o in mkt.outcomes if o.name == game.home_team), None)
                    away_odds = next((o.price for o in mkt.outcomes if o.name == game.away_team), None)
                    rows.append({
                        "game_id": game_uuid,
                        "bookmaker": bm.key,
                        "market": mkt.key,
                        "home_odds": home_odds,
                        "away_odds": away_odds,
                        "recorded_at": now,
                    })
                elif mkt.key in ("spreads", "totals"):
                    home_val = next((o.point for o in mkt.outcomes if o.name == game.home_team), None)
                    away_val = next((o.point for o in mkt.outcomes if o.name == game.away_team), None)
                    over_val = next((o.point for o in mkt.outcomes if o.name == "Over"), None)
                    rows.append({
                        "game_id": game_uuid,
                        "bookmaker": bm.key,
                        "market": mkt.key,
                        "home_odds": home_val,
                        "away_odds": away_val or over_val,
                        "recorded_at": now,
                    })
        if rows:
            db.table("odds_history").insert(rows).execute()

    # ─── Full ingest cycle ────────────────────────────────────────────────────

    async def run_ingestion_cycle(self) -> Dict[str, int]:
        """Fetch + store odds for all tracked sports. Called by scheduler."""
        results: Dict[str, int] = {}
        for sport in TRACKED_SPORTS:
            try:
                games = await self.fetch_odds(sport)
                count = 0
                for game in games:
                    game_uuid = await self.upsert_game(game)
                    await self.store_odds_snapshot(game_uuid, game)
                    count += 1
                results[sport] = count
                log.info("odds.ingestion.complete", sport=sport, games=count)
            except Exception as e:
                log.error("odds.ingestion.error", sport=sport, error=str(e))
                results[sport] = -1
            await asyncio.sleep(1)  # be polite to API
        return results

    # ─── Query helpers ────────────────────────────────────────────────────────

    async def get_live_odds(self, game_id: str) -> Optional[OddsResponse]:
        cache = await get_cache()
        cache_key = f"odds:{game_id}"
        cached = await cache.get(cache_key)
        if cached:
            return OddsResponse(**cached)

        db = get_supabase()
        # Get latest snapshot per bookmaker for this game
        rows = (
            db.table("odds_history")
            .select("*")
            .eq("game_id", game_id)
            .eq("market", "h2h")
            .order("recorded_at", desc=True)
            .limit(20)
            .execute()
        )
        if not rows.data:
            return None

        game_row = db.table("games").select("*").eq("id", game_id).single().execute()
        if not game_row.data:
            return None

        g = game_row.data
        latest = rows.data[0]

        resp = OddsResponse(
            game_id=game_id,
            home_team=g["home_team"],
            away_team=g["away_team"],
            sport=g["sport"],
            commence_time=g["game_time"],
            best_home_odds=latest.get("home_odds"),
            best_away_odds=latest.get("away_odds"),
            consensus_spread=None,
            consensus_over_under=None,
            bookmakers_count=len({r["bookmaker"] for r in rows.data}),
            last_fetched=datetime.fromisoformat(latest["recorded_at"]),
        )
        await cache.set(cache_key, resp.model_dump(mode="json"), ttl_seconds=CACHE_TTL_ODDS)
        return resp

    async def get_odds_history(self, game_id: str, hours: int = 6) -> List[dict]:
        """Return odds movement timeline for charts."""
        db = get_supabase()
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = (
            db.table("odds_history")
            .select("bookmaker, market, home_odds, away_odds, recorded_at")
            .eq("game_id", game_id)
            .eq("market", "h2h")
            .gte("recorded_at", cutoff)
            .order("recorded_at")
            .execute()
        )
        return rows.data or []


odds_service = OddsService()
