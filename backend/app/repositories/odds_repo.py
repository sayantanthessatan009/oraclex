"""
app/repositories/odds_repo.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.core.database import get_supabase
from app.core.logging import get_logger

log = get_logger(__name__)


class OddsRepository:

    def __init__(self):
        self._db = get_supabase()

    def insert_snapshot(
        self,
        game_id: str,
        bookmaker: str,
        market: str,
        home_odds: Optional[float],
        away_odds: Optional[float],
    ) -> bool:
        try:
            self._db.table("odds_history").insert({
                "game_id": game_id,
                "bookmaker": bookmaker,
                "market": market,
                "home_odds": home_odds,
                "away_odds": away_odds,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            return True
        except Exception as e:
            log.error("odds_repo.insert.error", error=str(e))
            return False

    def insert_batch(self, rows: List[dict]) -> bool:
        """Bulk insert odds snapshots."""
        if not rows:
            return True
        try:
            self._db.table("odds_history").insert(rows).execute()
            return True
        except Exception as e:
            log.error("odds_repo.batch.error", error=str(e))
            return False

    def get_latest(self, game_id: str, market: str = "h2h") -> Optional[dict]:
        res = (
            self._db.table("odds_history")
            .select("*")
            .eq("game_id", game_id)
            .eq("market", market)
            .order("recorded_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def get_history(self, game_id: str, hours: int = 6) -> List[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        res = (
            self._db.table("odds_history")
            .select("bookmaker, market, home_odds, away_odds, recorded_at")
            .eq("game_id", game_id)
            .eq("market", "h2h")
            .gte("recorded_at", cutoff)
            .order("recorded_at")
            .execute()
        )
        return res.data or []

    def get_bookmaker_count(self, game_id: str) -> int:
        res = (
            self._db.table("odds_history")
            .select("bookmaker")
            .eq("game_id", game_id)
            .execute()
        )
        return len({r["bookmaker"] for r in (res.data or [])})


odds_repo = OddsRepository()


# ─────────────────────────────────────────────────────────────────────────────

"""
app/repositories/sentiment_repo.py
"""


class SentimentRepository:

    def __init__(self):
        self._db = get_supabase()

    def upsert(self, game_id: str, data: dict) -> bool:
        try:
            self._db.table("sentiment_scores").upsert(
                {"game_id": game_id, **data, "computed_at": datetime.now(timezone.utc).isoformat()},
                on_conflict="game_id",
            ).execute()
            return True
        except Exception as e:
            log.error("sentiment_repo.upsert.error", error=str(e))
            return False

    def get_for_game(self, game_id: str) -> Optional[dict]:
        res = (
            self._db.table("sentiment_scores")
            .select("*")
            .eq("game_id", game_id)
            .single()
            .execute()
        )
        return res.data if res.data else None


sentiment_repo = SentimentRepository()


# ─────────────────────────────────────────────────────────────────────────────

"""
app/repositories/favorites_repo.py
"""


class FavoritesRepository:

    def __init__(self):
        self._db = get_supabase()

    def add(self, user_id: str, game_id: str) -> bool:
        try:
            self._db.table("user_favorites").upsert(
                {"user_id": user_id, "game_id": game_id},
                on_conflict="user_id,game_id",
            ).execute()
            return True
        except Exception as e:
            log.error("favorites_repo.add.error", error=str(e))
            return False

    def remove(self, user_id: str, game_id: str) -> bool:
        self._db.table("user_favorites").delete().eq("user_id", user_id).eq("game_id", game_id).execute()
        return True

    def list_for_user(self, user_id: str) -> List[dict]:
        res = (
            self._db.table("user_favorites")
            .select("*, games(home_team, away_team, sport, game_time, status)")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return res.data or []


favorites_repo = FavoritesRepository()
