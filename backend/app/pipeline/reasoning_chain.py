"""
app/pipeline/reasoning_chain.py
LangChain RAG chain using Groq llama-3.3-70b-versatile.
Retrieves similar historical matches from ChromaDB,
reasons over them + live odds, outputs natural language prediction.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
settings = get_settings()

# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are OracleX — an AI oracle that predicts sports outcomes before they happen.
You combine the insight of a Las Vegas sharp, the reasoning of a data scientist,
and the voice of a sports analyst. You speak with authority and clarity.
You never hedge. You predict with confidence based on the evidence in front of you."""

PREDICTION_PROMPT = """You are analyzing: {away_team} @ {home_team} ({sport})
Game time: {game_time}

CURRENT ODDS:
- {home_team} moneyline: {home_odds} (implied {home_prob:.1%})
- {away_team} moneyline: {away_odds} (implied {away_prob:.1%})
- Spread: {spread}
- Over/Under: {total_line}

INJURIES:
- {home_team}: {home_injuries}
- {away_team}: {away_injuries}

RECENT FORM:
- {home_form}
- {away_form}

CROWD SENTIMENT: {sentiment_score:+.2f}/1.0 ({sentiment_label})

SIMILAR HISTORICAL MATCHUPS (retrieved from vector store):
{retrieved_context}

PATTERNS FROM SIMILAR GAMES:
- Home team won in {home_win_pct:.0%} of similar matchups
- Average margin: {avg_margin:.1f} points
- Sentiment correlation: {sentiment_correlation}

Based on all of the above, provide your prediction in this EXACT format:

## THE NARRATIVE
[3 sentences. Present tense. Cinematic. Find the storyline.]

## THE VERDICT
Winner: [exact team name]
Confidence: [XX%]

## KEY FACTORS
1. [Factor — max 15 words]
2. [Factor — max 15 words]
3. [Factor — max 15 words]

## UPSET WATCH
[1-2 sentences on how the underdog could win]

## THE BET
[Specific market + line + brief rationale]"""


# ── Output parser ─────────────────────────────────────────────────────────────

def parse_prediction_output(text: str, home_team: str, away_team: str) -> Dict[str, Any]:
    """Extract structured fields from the narrative text."""
    result = {
        "narrative": text,
        "predicted_winner": home_team,
        "confidence": 0.60,
        "key_factors": [],
        "upset_watch": "",
        "bet_recommendation": "",
    }

    # Winner
    match = re.search(r"Winner:\s*(.+)", text, re.IGNORECASE)
    if match:
        raw = match.group(1).strip()
        if away_team.lower() in raw.lower():
            result["predicted_winner"] = away_team
        else:
            result["predicted_winner"] = home_team

    # Confidence
    match = re.search(r"Confidence:\s*(\d+)%", text, re.IGNORECASE)
    if match:
        result["confidence"] = min(int(match.group(1)) / 100.0, 1.0)

    # Key factors
    kf_match = re.search(r"## KEY FACTORS\n(.*?)(?=##|\Z)", text, re.DOTALL | re.IGNORECASE)
    if kf_match:
        for line in kf_match.group(1).strip().split("\n"):
            line = re.sub(r"^\d+\.\s*", "", line).strip()
            if line:
                result["key_factors"].append(line)

    # Upset watch
    uw_match = re.search(r"## UPSET WATCH\n(.*?)(?=##|\Z)", text, re.DOTALL | re.IGNORECASE)
    if uw_match:
        result["upset_watch"] = uw_match.group(1).strip()[:500]

    # Bet recommendation
    bet_match = re.search(r"## THE BET\n(.*?)(?=##|\Z)", text, re.DOTALL | re.IGNORECASE)
    if bet_match:
        result["bet_recommendation"] = bet_match.group(1).strip()[:300]

    return result


# ── Context helpers ───────────────────────────────────────────────────────────

def _implied_prob(american_odds: Optional[float]) -> float:
    if not american_odds:
        return 0.5
    if american_odds > 0:
        return 100 / (american_odds + 100)
    return abs(american_odds) / (abs(american_odds) + 100)


def _format_retrieved_context(matches: List[Dict]) -> str:
    if not matches:
        return "No similar historical matchups found in vector store."
    parts = []
    for i, m in enumerate(matches[:5], 1):
        parts.append(
            f"Match {i} (similarity {m['similarity']:.2f}):\n"
            f"{m['document']}\n"
            f"Outcome: {m['outcome']}"
        )
    return "\n\n".join(parts)


def _home_win_pct(matches: List[Dict], home_team: str) -> float:
    if not matches:
        return 0.5
    wins = sum(
        1 for m in matches
        if home_team.lower() in m.get("outcome", "").lower()
    )
    return wins / len(matches)


def _sentiment_label(score: float) -> str:
    if score > 0.3:
        return "bullish on home team"
    if score < -0.3:
        return "bearish on home team"
    return "neutral"


# ── Main reasoning chain ──────────────────────────────────────────────────────

class ReasoningChain:
    def __init__(self):
        self._llm = ChatGroq(
            api_key=settings.groq_api_key,
            model=settings.groq_narrative_model,
            temperature=0.7,
            max_tokens=900,
            streaming=True,
        )
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", PREDICTION_PROMPT),
        ])
        self._chain = self._prompt | self._llm | StrOutputParser()

    async def astream_prediction(
        self,
        sport: str,
        home_team: str,
        away_team: str,
        game_time: str,
        odds_data: Dict[str, Any],
        injuries: Dict[str, str],
        form: Dict[str, str],
        sentiment_score: float,
        retrieved_matches: List[Dict],
    ):
        """Async generator — streams narrative tokens."""
        home_odds = odds_data.get("home_odds")
        away_odds = odds_data.get("away_odds")

        variables = {
            "sport": sport,
            "home_team": home_team,
            "away_team": away_team,
            "game_time": game_time,
            "home_odds": home_odds or "N/A",
            "away_odds": away_odds or "N/A",
            "home_prob": _implied_prob(home_odds),
            "away_prob": _implied_prob(away_odds),
            "spread": odds_data.get("spread", "N/A"),
            "total_line": odds_data.get("total_line", "N/A"),
            "home_injuries": injuries.get("home", "None reported"),
            "away_injuries": injuries.get("away", "None reported"),
            "home_form": form.get("home", "N/A"),
            "away_form": form.get("away", "N/A"),
            "sentiment_score": sentiment_score,
            "sentiment_label": _sentiment_label(sentiment_score),
            "retrieved_context": _format_retrieved_context(retrieved_matches),
            "home_win_pct": _home_win_pct(retrieved_matches, home_team),
            "avg_margin": odds_data.get("avg_margin", 4.5),
            "sentiment_correlation": "moderate positive" if abs(sentiment_score) > 0.3 else "weak",
        }

        async for chunk in self._chain.astream(variables):
            yield chunk

    async def arun_prediction(
        self,
        sport: str,
        home_team: str,
        away_team: str,
        game_time: str,
        odds_data: Dict[str, Any],
        injuries: Dict[str, str],
        form: Dict[str, str],
        sentiment_score: float,
        retrieved_matches: List[Dict],
    ) -> Dict[str, Any]:
        """Non-streaming version — returns full parsed prediction dict."""
        full_text = ""
        async for chunk in self.astream_prediction(
            sport, home_team, away_team, game_time,
            odds_data, injuries, form, sentiment_score, retrieved_matches,
        ):
            full_text += chunk

        return parse_prediction_output(full_text, home_team, away_team)


reasoning_chain = ReasoningChain()
