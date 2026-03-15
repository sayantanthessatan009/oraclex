"""
app/api/routes/games.py
Endpoints for listing and querying games.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_supabase
from app.models.schemas import GameRecord, GamesListResponse

router = APIRouter(prefix="/games", tags=["games"])


@router.get("", response_model=GamesListResponse)
async def list_games(
    sport: Optional[str] = Query(None, description="Filter by sport key, e.g. basketball_nba"),
    status: Optional[str] = Query("upcoming", description="upcoming | live | final"),
    hours_ahead: int = Query(48, description="Window ahead in hours for upcoming games"),
    limit: int = Query(20, le=50),
):
    """List games with optional sport and status filters."""
    db = get_supabase()
    q = db.table("games").select("*").order("game_time")

    if status:
        q = q.eq("status", status)
    if sport:
        q = q.eq("sport", sport)
    if status == "upcoming":
        cutoff = (datetime.now(timezone.utc) + timedelta(hours=hours_ahead)).isoformat()
        q = q.lte("game_time", cutoff)

    q = q.limit(limit)
    result = q.execute()
    games = [GameRecord(**r) for r in (result.data or [])]

    return GamesListResponse(
        sport=sport or "all",
        count=len(games),
        games=games,
    )


@router.get("/{game_id}", response_model=GameRecord)
async def get_game(game_id: str):
    """Get a single game by ID."""
    db = get_supabase()
    result = db.table("games").select("*").eq("id", game_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Game not found")
    return GameRecord(**result.data)


@router.get("/by-external/{external_id}", response_model=GameRecord)
async def get_game_by_external_id(external_id: str):
    """Lookup a game by The Odds API external ID."""
    db = get_supabase()
    result = db.table("games").select("*").eq("external_id", external_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Game not found")
    return GameRecord(**result.data)
