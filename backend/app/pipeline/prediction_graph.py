"""
app/pipeline/prediction_graph.py
LangGraph state machine — the full OracleX prediction workflow.
Nodes: data_ingest → embed → retrieve → reason → narrate → store
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.core.logging import get_logger
from app.pipeline.reasoning_chain import reasoning_chain
from app.pipeline.vector_store import vector_store
from app.repositories.games_repo import games_repo
from app.repositories.odds_repo import odds_repo, sentiment_repo
from app.repositories.predictions_repo import predictions_repo
from app.scrapers.espn_scraper import espn_scraper
from app.services.odds_service import odds_service
from app.services.sentiment_service import sentiment_service

log = get_logger(__name__)


# ── State schema ─────────────────────────────────────────────────────────────

class OracleXState(TypedDict):
    # Input
    game_id: str
    sport: str
    home_team: str
    away_team: str
    game_time: str

    # Ingest outputs
    market_odds: Dict[str, Any]         # home_odds, away_odds, spread, total_line
    home_stats: Dict[str, Any]
    away_stats: Dict[str, Any]
    home_injuries: str
    away_injuries: str
    home_form: str
    away_form: str
    sentiment_score: float
    top_comments: List[str]

    # Embed + retrieve outputs
    context_data: Dict[str, Any]        # merged data for embedding
    retrieved_matches: List[Dict]       # top-5 similar from ChromaDB

    # Reason + narrate outputs
    narrative: str
    predicted_winner: str
    confidence: float
    key_factors: List[str]
    upset_watch: str
    bet_recommendation: str

    # Control
    status: str                         # pending/ingesting/embedding/...
    retry_count: int
    error_message: Optional[str]
    prediction_id: Optional[str]


# ── Node implementations ───────────────────────────────────────────────────────

async def data_ingest_node(state: OracleXState) -> OracleXState:
    """Fetch odds, injuries, form, and sentiment in parallel."""
    log.info("graph.node.data_ingest", game_id=state["game_id"])
    state["status"] = "ingesting"

    try:
        # Parallel fetch
        odds_result, injuries, home_form, away_form, sentiment = await asyncio.gather(
            odds_service.get_live_odds(state["game_id"]),
            espn_scraper.get_injuries(state["sport"]),
            espn_scraper.get_team_recent_form(state["sport"], state["home_team"]),
            espn_scraper.get_team_recent_form(state["sport"], state["away_team"]),
            sentiment_service.analyze_game_sentiment(
                state["game_id"],
                state["home_team"],
                state["away_team"],
                state["sport"],
            ),
            return_exceptions=True,
        )

        # Odds
        state["market_odds"] = {}
        if odds_result and not isinstance(odds_result, Exception):
            state["market_odds"] = {
                "home_odds": odds_result.best_home_odds,
                "away_odds": odds_result.best_away_odds,
                "spread": odds_result.consensus_spread,
                "total_line": odds_result.consensus_over_under,
            }

        # Injuries
        inj_list = injuries if isinstance(injuries, list) else []
        state["home_injuries"] = espn_scraper.format_injury_report(
            espn_scraper.get_team_injuries(inj_list, state["home_team"])
        )
        state["away_injuries"] = espn_scraper.format_injury_report(
            espn_scraper.get_team_injuries(inj_list, state["away_team"])
        )

        # Form
        state["home_form"] = espn_scraper.format_form(
            home_form if isinstance(home_form, dict) else {}, state["home_team"]
        )
        state["away_form"] = espn_scraper.format_form(
            away_form if isinstance(away_form, dict) else {}, state["away_team"]
        )

        # Sentiment
        state["sentiment_score"] = 0.0
        state["top_comments"] = []
        if sentiment and not isinstance(sentiment, Exception):
            state["sentiment_score"] = sentiment.home.score - sentiment.away.score
            state["top_comments"] = sentiment.home.key_signals[:3]

    except Exception as e:
        log.error("graph.node.data_ingest.error", error=str(e))
        state["error_message"] = str(e)
        state["status"] = "failed"

    return state


async def embed_node(state: OracleXState) -> OracleXState:
    """Build context data dict for embedding."""
    log.info("graph.node.embed", game_id=state["game_id"])
    state["status"] = "embedding"

    odds = state.get("market_odds", {})
    state["context_data"] = {
        "home_team": state["home_team"],
        "away_team": state["away_team"],
        "home_odds": odds.get("home_odds", 0),
        "away_odds": odds.get("away_odds", 0),
        "spread": odds.get("spread", 0),
        "total_line": odds.get("total_line", 0),
        "home_injuries": state.get("home_injuries", "None"),
        "away_injuries": state.get("away_injuries", "None"),
        "home_last5": state.get("home_form", "N/A"),
        "away_last5": state.get("away_form", "N/A"),
        "sentiment_score": state.get("sentiment_score", 0.0),
    }
    return state


async def retrieve_node(state: OracleXState) -> OracleXState:
    """Query ChromaDB for top-5 similar historical matchups."""
    log.info("graph.node.retrieve", game_id=state["game_id"])
    state["status"] = "retrieving"

    try:
        matches = vector_store.retrieve_similar(
            sport=state["sport"],
            context_data=state["context_data"],
            top_k=5,
        )
        state["retrieved_matches"] = matches
    except Exception as e:
        log.warning("graph.node.retrieve.error", error=str(e))
        state["retrieved_matches"] = []

    return state


async def reason_node(state: OracleXState) -> OracleXState:
    """Groq LLM reasons over retrieved context + current data."""
    log.info("graph.node.reason", game_id=state["game_id"])
    state["status"] = "reasoning"

    try:
        result = await reasoning_chain.arun_prediction(
            sport=state["sport"],
            home_team=state["home_team"],
            away_team=state["away_team"],
            game_time=state["game_time"],
            odds_data=state.get("market_odds", {}),
            injuries={
                "home": state.get("home_injuries", "None"),
                "away": state.get("away_injuries", "None"),
            },
            form={
                "home": state.get("home_form", "N/A"),
                "away": state.get("away_form", "N/A"),
            },
            sentiment_score=state.get("sentiment_score", 0.0),
            retrieved_matches=state.get("retrieved_matches", []),
        )
        state["narrative"] = result.get("narrative", "")
        state["predicted_winner"] = result.get("predicted_winner", state["home_team"])
        state["confidence"] = result.get("confidence", 0.60)
        state["key_factors"] = result.get("key_factors", [])
        state["upset_watch"] = result.get("upset_watch", "")
        state["bet_recommendation"] = result.get("bet_recommendation", "")

    except Exception as e:
        log.error("graph.node.reason.error", error=str(e))
        state["error_message"] = str(e)
        state["status"] = "failed"

    return state


async def narrate_node(state: OracleXState) -> OracleXState:
    """Narrative is already in state from reason_node. This node validates it."""
    log.info("graph.node.narrate", game_id=state["game_id"])
    state["status"] = "narrating"

    if not state.get("narrative"):
        state["narrative"] = (
            f"Based on current data, {state['predicted_winner']} is favored to win "
            f"with {state['confidence']:.0%} confidence."
        )
    return state


async def store_node(state: OracleXState) -> OracleXState:
    """Persist prediction to Supabase + embed completed match in ChromaDB."""
    log.info("graph.node.store", game_id=state["game_id"])
    state["status"] = "storing"

    try:
        from app.models.schemas import PredictionFactors, PredictionResult

        prediction = PredictionResult(
            game_id=state["game_id"],
            sport=state["sport"],
            home_team=state["home_team"],
            away_team=state["away_team"],
            predicted_winner=state["predicted_winner"],
            confidence=state["confidence"],
            narrative=state["narrative"],
            key_factors=[
                PredictionFactors(factor=f[:60], weight="medium", detail=f)
                for f in state.get("key_factors", [])
            ],
            upset_watch=state.get("upset_watch", ""),
            bet_recommendation=state.get("bet_recommendation", ""),
            model_used="llama-3.3-70b-versatile",
        )

        prediction_id = predictions_repo.create(prediction, state["game_id"])
        state["prediction_id"] = prediction_id

        # Also store in ChromaDB for future retrieval
        vector_store.store_match(
            event_id=state["game_id"],
            sport=state["sport"],
            context_data=state.get("context_data", {}),
            outcome=f"predicted: {state['predicted_winner']}",
        )

        state["status"] = "completed"
        log.info("graph.node.store.done", prediction_id=prediction_id)

    except Exception as e:
        log.error("graph.node.store.error", error=str(e))
        state["error_message"] = str(e)
        state["status"] = "failed"

    return state


# ── Retry logic ───────────────────────────────────────────────────────────────

def should_retry(state: OracleXState) -> str:
    if state.get("status") == "failed" and state.get("retry_count", 0) < 2:
        state["retry_count"] = state.get("retry_count", 0) + 1
        log.warning("graph.retry", attempt=state["retry_count"])
        return "data_ingest"
    return END


# ── Build graph ───────────────────────────────────────────────────────────────

def build_prediction_graph() -> StateGraph:
    graph = StateGraph(OracleXState)

    graph.add_node("data_ingest", data_ingest_node)
    graph.add_node("embed", embed_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("reason", reason_node)
    graph.add_node("narrate", narrate_node)
    graph.add_node("store", store_node)

    graph.set_entry_point("data_ingest")
    graph.add_edge("data_ingest", "embed")
    graph.add_edge("embed", "retrieve")
    graph.add_edge("retrieve", "reason")
    graph.add_edge("reason", "narrate")
    graph.add_edge("narrate", "store")
    graph.add_conditional_edges("store", should_retry)

    return graph.compile()


prediction_graph = build_prediction_graph()


# ── Public API ────────────────────────────────────────────────────────────────

async def run_prediction(game_id: str) -> Dict[str, Any]:
    """Run full prediction pipeline for a game. Returns final state."""
    game = games_repo.get_by_id(game_id)
    if not game:
        raise ValueError(f"Game {game_id} not found")

    initial_state: OracleXState = {
        "game_id": game_id,
        "sport": game.sport,
        "home_team": game.home_team,
        "away_team": game.away_team,
        "game_time": game.game_time.isoformat(),
        "market_odds": {},
        "home_stats": {},
        "away_stats": {},
        "home_injuries": "",
        "away_injuries": "",
        "home_form": "",
        "away_form": "",
        "sentiment_score": 0.0,
        "top_comments": [],
        "context_data": {},
        "retrieved_matches": [],
        "narrative": "",
        "predicted_winner": game.home_team,
        "confidence": 0.60,
        "key_factors": [],
        "upset_watch": "",
        "bet_recommendation": "",
        "status": "pending",
        "retry_count": 0,
        "error_message": None,
        "prediction_id": None,
    }

    final_state = await prediction_graph.ainvoke(initial_state)
    return final_state


async def stream_prediction(game_id: str) -> AsyncGenerator[str, None]:
    """
    SSE-compatible generator for the streaming narrative.
    Runs data_ingest + embed + retrieve first, then streams the reason step.
    """
    import json

    game = games_repo.get_by_id(game_id)
    if not game:
        yield f"event: error\ndata: Game not found\n\n"
        return

    yield f"event: status\ndata: Gathering intelligence...\n\n"

    # Run ingest + embed + retrieve synchronously first
    state: OracleXState = {
        "game_id": game_id,
        "sport": game.sport,
        "home_team": game.home_team,
        "away_team": game.away_team,
        "game_time": game.game_time.isoformat(),
        "market_odds": {},
        "home_stats": {}, "away_stats": {},
        "home_injuries": "", "away_injuries": "",
        "home_form": "", "away_form": "",
        "sentiment_score": 0.0, "top_comments": [],
        "context_data": {}, "retrieved_matches": [],
        "narrative": "", "predicted_winner": game.home_team,
        "confidence": 0.60, "key_factors": [],
        "upset_watch": "", "bet_recommendation": "",
        "status": "pending", "retry_count": 0,
        "error_message": None, "prediction_id": None,
    }

    state = await data_ingest_node(state)
    state = await embed_node(state)
    state = await retrieve_node(state)

    yield f"event: status\ndata: Oracle is reading the future...\n\n"

    # Stream the reasoning
    full_narrative = ""
    async for chunk in reasoning_chain.astream_prediction(
        sport=state["sport"],
        home_team=state["home_team"],
        away_team=state["away_team"],
        game_time=state["game_time"],
        odds_data=state.get("market_odds", {}),
        injuries={"home": state.get("home_injuries", ""), "away": state.get("away_injuries", "")},
        form={"home": state.get("home_form", ""), "away": state.get("away_form", "")},
        sentiment_score=state.get("sentiment_score", 0.0),
        retrieved_matches=state.get("retrieved_matches", []),
    ):
        full_narrative += chunk
        yield f"event: narrative\ndata: {chunk}\n\n"

    # Parse + store
    state["narrative"] = full_narrative
    state = await narrate_node(state)
    state = await store_node(state)

    # Send metadata
    meta = {
        "predicted_winner": state["predicted_winner"],
        "confidence": state["confidence"],
        "key_factors": state["key_factors"],
        "upset_watch": state["upset_watch"],
        "bet_recommendation": state["bet_recommendation"],
        "prediction_id": state.get("prediction_id"),
    }
    yield f"event: metadata\ndata: {json.dumps(meta)}\n\n"
    yield f"event: done\ndata: \n\n"
