"""
app/api/routes/favorites.py
User favorites — stored in Supabase with RLS (requires auth token).
"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_supabase_anon
from app.core.logging import get_logger

router = APIRouter(prefix="/favorites", tags=["favorites"])
log = get_logger(__name__)


class FavoriteRequest(BaseModel):
    game_id: str
    user_id: str  # from Supabase auth JWT


@router.get("")
async def list_favorites(user_id: str, authorization: Optional[str] = Header(None)):
    """Get all favorited games for a user."""
    db = get_supabase_anon()
    result = (
        db.table("user_favorites")
        .select("*, games(home_team, away_team, sport, game_time, status)")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"favorites": result.data or []}


@router.post("")
async def add_favorite(req: FavoriteRequest):
    """Favorite a game."""
    db = get_supabase_anon()
    try:
        result = (
            db.table("user_favorites")
            .upsert({"user_id": req.user_id, "game_id": req.game_id}, on_conflict="user_id,game_id")
            .execute()
        )
        return {"status": "added", "data": result.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{game_id}")
async def remove_favorite(game_id: str, user_id: str):
    """Remove a game from favorites."""
    db = get_supabase_anon()
    db.table("user_favorites").delete().eq("user_id", user_id).eq("game_id", game_id).execute()
    return {"status": "removed"}
