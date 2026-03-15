"""
tests/test_prediction_service.py
Basic unit tests — no network calls, no real API keys needed.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.models.schemas import (
    GameRecord,
    GameStatus,
    OddsOutcome,
    OddsResponse,
    SentimentPair,
    TeamSentiment,
)
from app.services.prediction_service import PredictionService, build_prediction_prompt


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_game():
    return GameRecord(
        id=uuid4(),
        external_id="test-001",
        sport="basketball_nba",
        home_team="Los Angeles Lakers",
        away_team="Boston Celtics",
        game_time=datetime(2025, 12, 25, 20, 0, tzinfo=timezone.utc),
        status=GameStatus.UPCOMING,
    )


@pytest.fixture
def sample_odds():
    return OddsResponse(
        game_id=str(uuid4()),
        home_team="Los Angeles Lakers",
        away_team="Boston Celtics",
        sport="basketball_nba",
        commence_time=datetime(2025, 12, 25, 20, 0, tzinfo=timezone.utc),
        best_home_odds=-150,
        best_away_odds=130,
        consensus_spread=-3.5,
        consensus_over_under=224.5,
        bookmakers_count=5,
        last_fetched=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_sentiment():
    return SentimentPair(
        home=TeamSentiment(
            team="Los Angeles Lakers",
            sport="basketball_nba",
            score=0.45,
            post_count=32,
            key_signals=["LeBron injury concern resolved", "strong home crowd expected", "7-game win streak"],
            summary="Cautious optimism with strong home advantage",
        ),
        away=TeamSentiment(
            team="Boston Celtics",
            sport="basketball_nba",
            score=0.62,
            post_count=28,
            key_signals=["Tatum in top form", "road warriors this season", "defensive depth"],
            summary="Confident road team with elite defense",
        ),
        edge="Boston Celtics has crowd sentiment edge (+0.17)",
    )


# ─── Odds model tests ────────────────────────────────────────────────────────

def test_odds_outcome_implied_probability_favourite():
    outcome = OddsOutcome(name="Lakers", price=-150)
    prob = outcome.implied_probability
    assert 0.59 < prob < 0.61, f"Expected ~60%, got {prob:.2%}"


def test_odds_outcome_implied_probability_underdog():
    outcome = OddsOutcome(name="Celtics", price=130)
    prob = outcome.implied_probability
    assert 0.43 < prob < 0.45, f"Expected ~43.5%, got {prob:.2%}"


def test_odds_outcome_decimal_conversion_favourite():
    outcome = OddsOutcome(name="Lakers", price=-200)
    assert outcome.decimal_odds == pytest.approx(1.5, rel=0.01)


def test_odds_outcome_decimal_conversion_underdog():
    outcome = OddsOutcome(name="Celtics", price=200)
    assert outcome.decimal_odds == pytest.approx(3.0, rel=0.01)


# ─── Prompt building ─────────────────────────────────────────────────────────

def test_build_prediction_prompt_contains_teams(sample_game, sample_odds, sample_sentiment):
    prompt = build_prediction_prompt(
        sample_game,
        sample_odds,
        sample_sentiment,
        "No significant injuries.",
        "Tatum questionable (knee).",
        "Los Angeles Lakers: 7-3 record, streak: 3W",
        "Boston Celtics: 8-2 record, streak: 2W",
    )
    assert "Los Angeles Lakers" in prompt
    assert "Boston Celtics" in prompt
    assert "THE NARRATIVE" in prompt
    assert "THE VERDICT" in prompt
    assert "KEY FACTORS" in prompt
    assert "UPSET WATCH" in prompt
    assert "THE BET" in prompt


def test_build_prediction_prompt_no_odds(sample_game, sample_sentiment):
    prompt = build_prediction_prompt(
        sample_game, None, sample_sentiment, "", "", "", ""
    )
    assert "Not available" in prompt
    assert "Los Angeles Lakers" in prompt


def test_build_prediction_prompt_no_sentiment(sample_game, sample_odds):
    prompt = build_prediction_prompt(
        sample_game, sample_odds, None, "", "", "", ""
    )
    assert "Not available" in prompt


# ─── Narrative parsing ───────────────────────────────────────────────────────

def test_parse_narrative_extracts_winner(sample_game, sample_sentiment):
    svc = PredictionService.__new__(PredictionService)
    narrative = """## THE NARRATIVE
The Celtics arrive at Crypto.com Arena riding a wave of momentum.

## THE VERDICT
Winner: Boston Celtics
Confidence: 68%

## KEY FACTORS
1. Tatum in career-best form averaging 32 PPG
2. Lakers missing Anthony Davis (questionable)
3. Boston top-5 road offense this season

## UPSET WATCH
If LeBron goes supernova and the Lakers' defense locks Tatum down early, the crowd could swing momentum.

## THE BET
Boston Celtics +3.5, juiced to -110, offers strong value at 52% cover rate on the road."""

    result = svc._parse_narrative(narrative, sample_game, sample_sentiment)

    assert result.predicted_winner == "Boston Celtics"
    assert 0.67 < result.confidence < 0.69
    assert len(result.key_factors) == 3
    assert "Tatum" in result.upset_watch
    assert "Boston Celtics" in result.bet_recommendation


def test_parse_narrative_defaults_home_on_ambiguous(sample_game):
    svc = PredictionService.__new__(PredictionService)
    narrative = "## THE VERDICT\nWinner: The home team\nConfidence: 55%"
    result = svc._parse_narrative(narrative, sample_game, None)
    assert result.predicted_winner == "Los Angeles Lakers"


def test_parse_narrative_confidence_clamp(sample_game):
    svc = PredictionService.__new__(PredictionService)
    narrative = "## THE VERDICT\nWinner: Boston Celtics\nConfidence: 150%"
    result = svc._parse_narrative(narrative, sample_game, None)
    assert result.confidence <= 1.0


# ─── SSE format ──────────────────────────────────────────────────────────────

def test_sse_format_text():
    chunk = PredictionService._sse("narrative", "The Celtics will win.")
    assert chunk.startswith("event: narrative\n")
    assert "The Celtics will win." in chunk
    assert chunk.endswith("\n\n")


def test_sse_format_dict():
    chunk = PredictionService._sse("metadata", {"winner": "Celtics", "confidence": 0.68})
    assert "event: metadata" in chunk
    assert '"winner"' in chunk
    assert '"Celtics"' in chunk


def test_chunk_text_splits_correctly():
    text = "abcdefgh"
    chunks = PredictionService._chunk_text(text, size=3)
    assert chunks == ["abc", "def", "gh"]
