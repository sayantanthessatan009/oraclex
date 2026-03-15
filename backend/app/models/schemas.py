"""
app/models/schemas.py
Pydantic v2 schemas for request/response + internal data structures.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field


# ─── Enums ────────────────────────────────────────────────────────────────────

class Sport(str, Enum):
    NFL = "americanfootball_nfl"
    NBA = "basketball_nba"
    NHL = "icehockey_nhl"
    MLB = "baseball_mlb"
    UFC = "mma_mixed_martial_arts"
    SOCCER_EPL = "soccer_epl"
    NCAAFB = "americanfootball_ncaaf"
    NCAABB = "basketball_ncaab"


class GameStatus(str, Enum):
    UPCOMING = "upcoming"
    LIVE = "live"
    FINAL = "final"
    POSTPONED = "postponed"


class Market(str, Enum):
    H2H = "h2h"
    SPREADS = "spreads"
    TOTALS = "totals"


# ─── Odds ─────────────────────────────────────────────────────────────────────

class OddsOutcome(BaseModel):
    name: str
    price: float  # American odds
    point: Optional[float] = None  # for spreads/totals

    @computed_field
    @property
    def implied_probability(self) -> float:
        """Convert American odds to implied probability."""
        if self.price > 0:
            return 100 / (self.price + 100)
        else:
            return abs(self.price) / (abs(self.price) + 100)

    @computed_field
    @property
    def decimal_odds(self) -> float:
        if self.price > 0:
            return (self.price / 100) + 1
        else:
            return (100 / abs(self.price)) + 1


class BookmakerMarket(BaseModel):
    key: str  # h2h / spreads / totals
    outcomes: List[OddsOutcome]
    last_update: Optional[datetime] = None


class BookmakerOdds(BaseModel):
    key: str  # draftkings, fanduel, etc.
    title: str
    markets: List[BookmakerMarket]


class GameOdds(BaseModel):
    game_id: str
    sport: str
    home_team: str
    away_team: str
    commence_time: datetime
    bookmakers: List[BookmakerOdds]

    @computed_field
    @property
    def best_home_h2h(self) -> Optional[float]:
        best = None
        for bm in self.bookmakers:
            for mkt in bm.markets:
                if mkt.key == "h2h":
                    for o in mkt.outcomes:
                        if o.name == self.home_team:
                            if best is None or o.price > best:
                                best = o.price
        return best

    @computed_field
    @property
    def best_away_h2h(self) -> Optional[float]:
        best = None
        for bm in self.bookmakers:
            for mkt in bm.markets:
                if mkt.key == "h2h":
                    for o in mkt.outcomes:
                        if o.name == self.away_team:
                            if best is None or o.price > best:
                                best = o.price
        return best

    @computed_field
    @property
    def consensus_spread(self) -> Optional[float]:
        """Average home team spread across bookmakers."""
        spreads = []
        for bm in self.bookmakers:
            for mkt in bm.markets:
                if mkt.key == "spreads":
                    for o in mkt.outcomes:
                        if o.name == self.home_team and o.point is not None:
                            spreads.append(o.point)
        return round(sum(spreads) / len(spreads), 1) if spreads else None

    @computed_field
    @property
    def consensus_over_under(self) -> Optional[float]:
        totals = []
        for bm in self.bookmakers:
            for mkt in bm.markets:
                if mkt.key == "totals":
                    for o in mkt.outcomes:
                        if o.name == "Over" and o.point is not None:
                            totals.append(o.point)
        return round(sum(totals) / len(totals), 1) if totals else None


# ─── Games ────────────────────────────────────────────────────────────────────

class GameRecord(BaseModel):
    id: Optional[UUID] = None
    sport: str
    home_team: str
    away_team: str
    game_time: datetime
    status: GameStatus = GameStatus.UPCOMING
    external_id: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ─── Sentiment ────────────────────────────────────────────────────────────────

class TeamSentiment(BaseModel):
    team: str
    sport: str
    score: float = Field(..., ge=-1.0, le=1.0, description="-1 very negative, +1 very positive")
    post_count: int = 0
    key_signals: List[str] = Field(default_factory=list)
    summary: str = ""
    computed_at: Optional[datetime] = None


class SentimentPair(BaseModel):
    home: TeamSentiment
    away: TeamSentiment
    edge: str = ""  # which team has sentiment edge


# ─── Predictions ─────────────────────────────────────────────────────────────

class PredictionFactors(BaseModel):
    factor: str
    weight: str  # "high" | "medium" | "low"
    detail: str


class PredictionResult(BaseModel):
    id: Optional[UUID] = None
    game_id: UUID
    sport: str
    home_team: str
    away_team: str
    predicted_winner: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    narrative: str
    key_factors: List[PredictionFactors] = Field(default_factory=list)
    upset_watch: str = ""
    bet_recommendation: str = ""
    sentiment: Optional[SentimentPair] = None
    model_used: str = "llama-3.3-70b-versatile"
    actual_winner: Optional[str] = None
    was_correct: Optional[bool] = None
    created_at: Optional[datetime] = None


# ─── API responses ────────────────────────────────────────────────────────────

class GamesListResponse(BaseModel):
    sport: str
    count: int
    games: List[GameRecord]


class OddsResponse(BaseModel):
    game_id: str
    home_team: str
    away_team: str
    sport: str
    commence_time: datetime
    best_home_odds: Optional[float]
    best_away_odds: Optional[float]
    consensus_spread: Optional[float]
    consensus_over_under: Optional[float]
    bookmakers_count: int
    last_fetched: datetime


class StreamChunk(BaseModel):
    type: str  # "narrative" | "metadata" | "done" | "error"
    content: str = ""
    metadata: Optional[Dict[str, Any]] = None


class AccuracyStats(BaseModel):
    total_predictions: int
    correct: int
    incorrect: int
    pending: int
    accuracy_pct: float
    by_sport: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    by_confidence_tier: Dict[str, Dict[str, int]] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    env: str
    groq_connected: bool
    supabase_connected: bool
    cache_backend: str
    scheduler_running: bool
