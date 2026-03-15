"""
app/api/routes/sentiment.py
"""
from fastapi import APIRouter, HTTPException, Query
from app.services.sentiment_service import sentiment_service
from app.models.schemas import SentimentPair

router = APIRouter(prefix="/sentiment", tags=["sentiment"])


@router.get("/{game_id}", response_model=SentimentPair)
async def get_game_sentiment(game_id: str):
    """Get sentiment analysis for both teams in a game."""
    from app.core.database import get_supabase
    db = get_supabase()
    game = db.table("games").select("*").eq("id", game_id).single().execute()
    if not game.data:
        raise HTTPException(status_code=404, detail="Game not found")
    g = game.data
    return await sentiment_service.analyze_game_sentiment(
        game_id, g["home_team"], g["away_team"], g["sport"]
    )
