"""
app/api/routes/predictions.py  — UPDATED
Wires the SSE streaming endpoint to the LangGraph pipeline.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.pipeline.prediction_graph import run_prediction, stream_prediction
from app.repositories.predictions_repo import predictions_repo

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/stream/{game_id}")
async def stream_prediction_endpoint(game_id: str):
    """
    SSE streaming prediction using the full LangGraph pipeline.
    Frontend: new EventSource('/api/v1/predictions/stream/{game_id}')
    Events: status | narrative | metadata | done | error
    """
    return StreamingResponse(
        stream_prediction(game_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/generate/{game_id}")
async def generate_prediction(game_id: str):
    """Non-streaming: run full pipeline, return complete result."""
    try:
        state = await run_prediction(game_id)
        return {
            "prediction_id": state.get("prediction_id"),
            "predicted_winner": state.get("predicted_winner"),
            "confidence": state.get("confidence"),
            "narrative": state.get("narrative"),
            "key_factors": state.get("key_factors"),
            "upset_watch": state.get("upset_watch"),
            "bet_recommendation": state.get("bet_recommendation"),
            "status": state.get("status"),
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{game_id}")
async def get_prediction(game_id: str):
    """Get cached prediction for a game."""
    prediction = predictions_repo.get_latest_for_game(game_id)
    if not prediction:
        raise HTTPException(
            status_code=404,
            detail="No prediction found. POST to /generate/{game_id} first.",
        )
    return prediction


@router.get("")
async def list_predictions(
    limit: int = Query(20, le=50),
    offset: int = Query(0),
    was_correct: Optional[bool] = Query(None),
):
    return {
        "predictions": predictions_repo.list_all(
            was_correct=was_correct, limit=limit, offset=offset
        )
    }


@router.get("/accuracy/stats")
async def accuracy_stats(sport: Optional[str] = Query(None)):
    return predictions_repo.get_accuracy_stats(sport=sport)


@router.get("/accuracy/leaderboard")
async def accuracy_leaderboard():
    return predictions_repo.get_accuracy_leaderboard()
