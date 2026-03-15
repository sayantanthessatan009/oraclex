"""
app/repositories/games_repo.py
All database operations for the games table.
Uses the service-role Supabase client for backend writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from app.core.database import get_supabase
from app.core.logging import get_logger
from app.models.schemas import GameRecord, GameStatus

log = get_logger(__name__)


class GamesRepository:

    def __init__(self):
        self._db = get_supabase()

    # ── Create / Upsert ──────────────────────────────────────────────────────

    def upsert(self, game: GameRecord) -> GameRecord:
        """Insert or update a game by external_id. Returns the saved record."""
        payload = {
            "external_id": game.external_id,
            "sport": game.sport,
            "home_team": game.home_team,
            "away_team": game.away_team,
            "game_time": game.game_time.isoformat(),
            "status": game.status.value if hasattr(game.status, "value") else game.status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if game.home_score is not None:
            payload["home_score"] = game.home_score
        if game.away_score is not None:
            payload["away_score"] = game.away_score

        res = (
            self._db.table("games")
            .upsert(payload, on_conflict="external_id")
            .execute()
        )
        return GameRecord(**res.data[0])

    # ── Read ─────────────────────────────────────────────────────────────────

    def get_by_id(self, game_id: str) -> Optional[GameRecord]:
        res = self._db.table("games").select("*").eq("id", game_id).single().execute()
        return GameRecord(**res.data) if res.data else None

    def get_by_external_id(self, external_id: str) -> Optional[GameRecord]:
        res = (
            self._db.table("games")
            .select("*")
            .eq("external_id", external_id)
            .single()
            .execute()
        )
        return GameRecord(**res.data) if res.data else None

    def list_upcoming(
        self,
        sport: Optional[str] = None,
        hours_ahead: int = 48,
        limit: int = 20,
    ) -> List[GameRecord]:
        cutoff = (datetime.now(timezone.utc) + timedelta(hours=hours_ahead)).isoformat()
        q = (
            self._db.table("games")
            .select("*")
            .eq("status", "upcoming")
            .lte("game_time", cutoff)
            .order("game_time")
            .limit(limit)
        )
        if sport:
            q = q.eq("sport", sport)
        res = q.execute()
        return [GameRecord(**r) for r in (res.data or [])]

    def list_by_status(
        self,
        status: str,
        sport: Optional[str] = None,
        limit: int = 20,
    ) -> List[GameRecord]:
        q = (
            self._db.table("games")
            .select("*")
            .eq("status", status)
            .order("game_time", desc=True)
            .limit(limit)
        )
        if sport:
            q = q.eq("sport", sport)
        res = q.execute()
        return [GameRecord(**r) for r in (res.data or [])]

    # ── Update ────────────────────────────────────────────────────────────────

    def update_status(self, game_id: str, status: str) -> bool:
        res = (
            self._db.table("games")
            .update({"status": status, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", game_id)
            .execute()
        )
        return bool(res.data)

    def update_score(
        self, game_id: str, home_score: int, away_score: int
    ) -> bool:
        res = (
            self._db.table("games")
            .update({
                "home_score": home_score,
                "away_score": away_score,
                "status": "final",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", game_id)
            .execute()
        )
        return bool(res.data)

    # ── Games with predictions (view) ─────────────────────────────────────────

    def list_with_predictions(
        self,
        sport: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[dict]:
        """Query the games_with_predictions view."""
        q = (
            self._db.table("games_with_predictions")
            .select("*")
            .order("game_time")
            .limit(limit)
        )
        if sport:
            q = q.eq("sport", sport)
        if status:
            q = q.eq("status", status)
        res = q.execute()
        return res.data or []


games_repo = GamesRepository()
