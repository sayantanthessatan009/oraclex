"""
app/repositories/predictions_repo.py
All database operations for the predictions table.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from app.core.database import get_supabase
from app.core.logging import get_logger
from app.models.schemas import PredictionResult

log = get_logger(__name__)


class PredictionsRepository:

    def __init__(self):
        self._db = get_supabase()

    # ── Create ────────────────────────────────────────────────────────────────

    def create(self, prediction: PredictionResult, game_id: str) -> Optional[str]:
        """Persist a new prediction. Returns the new UUID string."""
        payload = {
            "game_id": game_id,
            "predicted_winner": prediction.predicted_winner,
            "confidence": prediction.confidence,
            "narrative": prediction.narrative,
            "key_factors": [f.model_dump() for f in prediction.key_factors],
            "upset_watch": prediction.upset_watch,
            "bet_recommendation": prediction.bet_recommendation,
            "model_used": prediction.model_used,
            "sentiment_data": (
                prediction.sentiment.model_dump(mode="json")
                if prediction.sentiment else None
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            res = self._db.table("predictions").insert(payload).execute()
            if res.data:
                return res.data[0]["id"]
        except Exception as e:
            log.error("predictions_repo.create.error", error=str(e))
        return None

    # ── Read ─────────────────────────────────────────────────────────────────

    def get_by_id(self, prediction_id: str) -> Optional[dict]:
        res = (
            self._db.table("predictions")
            .select("*")
            .eq("id", prediction_id)
            .single()
            .execute()
        )
        return res.data if res.data else None

    def get_latest_for_game(self, game_id: str) -> Optional[dict]:
        res = (
            self._db.table("predictions")
            .select("*")
            .eq("game_id", game_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def list_all(
        self,
        sport: Optional[str] = None,
        was_correct: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[dict]:
        q = (
            self._db.table("predictions")
            .select("*, games(home_team, away_team, sport, game_time, status)")
            .order("created_at", desc=True)
            .limit(limit)
            .range(offset, offset + limit - 1)
        )
        if was_correct is not None:
            q = q.eq("was_correct", was_correct)
        res = q.execute()
        return res.data or []

    def list_pending_accuracy(self) -> List[dict]:
        """Predictions where was_correct is null but game is final."""
        res = (
            self._db.table("predictions")
            .select("id, game_id, predicted_winner, games(status, home_team, away_team, home_score, away_score)")
            .is_("was_correct", "null")
            .execute()
        )
        return [
            r for r in (res.data or [])
            if r.get("games", {}).get("status") == "final"
        ]

    # ── Update ────────────────────────────────────────────────────────────────

    def mark_outcome(
        self, prediction_id: str, actual_winner: str
    ) -> bool:
        """Set actual_winner — the trigger in Supabase auto-sets was_correct."""
        res = (
            self._db.table("predictions")
            .update({"actual_winner": actual_winner})
            .eq("id", prediction_id)
            .execute()
        )
        return bool(res.data)

    # ── Accuracy stats ────────────────────────────────────────────────────────

    def get_accuracy_stats(self, sport: Optional[str] = None) -> dict:
        q = self._db.table("predictions").select("was_correct, confidence")
        if sport:
            q = q.eq("sport", sport)
        rows = q.execute().data or []

        total = len(rows)
        correct = sum(1 for r in rows if r.get("was_correct") is True)
        incorrect = sum(1 for r in rows if r.get("was_correct") is False)
        pending = total - correct - incorrect

        return {
            "total": total,
            "correct": correct,
            "incorrect": incorrect,
            "pending": pending,
            "accuracy_pct": round(correct / max(correct + incorrect, 1) * 100, 1),
        }

    def get_accuracy_leaderboard(self) -> List[dict]:
        """Query the accuracy_leaderboard view."""
        res = self._db.table("accuracy_leaderboard").select("*").execute()
        return res.data or []


predictions_repo = PredictionsRepository()
