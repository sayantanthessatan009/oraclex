"""
app/api/routes/odds.py
Live odds and odds history endpoints.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import OddsResponse
from app.services.odds_service import TRACKED_SPORTS, odds_service

router = APIRouter(prefix="/odds", tags=["odds"])


@router.get("/live/{game_id}", response_model=OddsResponse)
async def get_live_odds(game_id: str):
    """Get current best odds for a game."""
    result = await odds_service.get_live_odds(game_id)
    if not result:
        raise HTTPException(status_code=404, detail="Odds not found for this game")
    return result


@router.get("/history/{game_id}")
async def get_odds_history(
    game_id: str,
    hours: int = Query(6, ge=1, le=48, description="How many hours of history"),
):
    """Get odds movement history for line-movement chart."""
    history = await odds_service.get_odds_history(game_id, hours=hours)
    return {"game_id": game_id, "hours": hours, "count": len(history), "data": history}


@router.post("/ingest")
async def trigger_ingestion(sport: Optional[str] = None):
    """Manually trigger odds ingestion. Useful for dev/testing."""
    if sport:
        from app.core.config import get_settings
        games = await odds_service.fetch_odds(sport)
        for game in games:
            game_uuid = await odds_service.upsert_game(game)
            await odds_service.store_odds_snapshot(game_uuid, game)
        return {"sport": sport, "games_ingested": len(games)}
    else:
        result = await odds_service.run_ingestion_cycle()
        return {"result": result}


@router.get("/sports")
async def list_tracked_sports():
    """Return list of sports currently being tracked."""
    return {"sports": TRACKED_SPORTS}
