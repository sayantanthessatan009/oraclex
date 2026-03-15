"""
app/pipeline/vector_store.py
ChromaDB vector store using sentence-transformers/all-MiniLM-L6-v2.
Separate collections per sport. Free, local, no API needed.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from app.core.logging import get_logger

log = get_logger(__name__)

CHROMA_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
TOP_K = 5
SIMILARITY_THRESHOLD = 0.7  # lower distance = more similar in ChromaDB

SPORT_COLLECTIONS = [
    "nba", "nhl", "nfl", "epl",
    "ipl", "big_bash", "casino",
]


class VectorStore:
    def __init__(self):
        self._client = chromadb.PersistentClient(
            path=CHROMA_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
        self._embedder = SentenceTransformer(EMBEDDING_MODEL)
        self._collections: Dict[str, chromadb.Collection] = {}
        self._init_collections()

    def _init_collections(self):
        for sport in SPORT_COLLECTIONS:
            self._collections[sport] = self._client.get_or_create_collection(
                name=f"{sport}_matches",
                metadata={"hnsw:space": "cosine"},
            )
        log.info("vector_store.collections.ready", sports=SPORT_COLLECTIONS)

    def _collection(self, sport: str) -> chromadb.Collection:
        key = sport.lower().replace(" ", "_")
        if key not in self._collections:
            self._collections[key] = self._client.get_or_create_collection(
                name=f"{key}_matches",
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[key]

    # ── Context string builders per sport ─────────────────────────────────────

    @staticmethod
    def build_context_string(sport: str, data: Dict[str, Any]) -> str:
        """Build the text that gets embedded. Sport-specific fields."""
        sport = sport.lower()

        base = (
            f"Sport: {sport}\n"
            f"Home Team: {data.get('home_team', 'Unknown')} | "
            f"Away Team: {data.get('away_team', 'Unknown')}\n"
        )

        if sport == "nba":
            return base + (
                f"Home Stats: {data.get('home_win_pct', 0):.0%}W, "
                f"PPG: {data.get('home_ppg', 0):.1f}, PAPG: {data.get('home_papg', 0):.1f}\n"
                f"Away Stats: {data.get('away_win_pct', 0):.0%}W, "
                f"PPG: {data.get('away_ppg', 0):.1f}, PAPG: {data.get('away_papg', 0):.1f}\n"
                f"Injuries: {data.get('home_injuries', 'None')} vs {data.get('away_injuries', 'None')}\n"
                f"Odds: Home {data.get('home_odds', 0):.2f} | Away {data.get('away_odds', 0):.2f} | "
                f"Total: {data.get('total_line', 0)}\n"
                f"Sentiment: {data.get('sentiment_score', 0):.2f}\n"
                f"Form: Home {data.get('home_last5', 'N/A')} | Away {data.get('away_last5', 'N/A')}"
            )
        elif sport == "nhl":
            return base + (
                f"Home: {data.get('home_win_pct', 0):.0%}W, "
                f"GF: {data.get('home_goals_for', 0):.1f}, GA: {data.get('home_goals_against', 0):.1f}, "
                f"PP%: {data.get('home_pp_pct', 0):.1f}\n"
                f"Away: {data.get('away_win_pct', 0):.0%}W, "
                f"GF: {data.get('away_goals_for', 0):.1f}, GA: {data.get('away_goals_against', 0):.1f}\n"
                f"Injuries: {data.get('home_injuries', 'None')} vs {data.get('away_injuries', 'None')}\n"
                f"Odds: Home {data.get('home_odds', 0):.2f} | Away {data.get('away_odds', 0):.2f}\n"
                f"Sentiment: {data.get('sentiment_score', 0):.2f}"
            )
        elif sport == "nfl":
            return base + (
                f"Home: {data.get('home_win_pct', 0):.0%}W, "
                f"YPG: {data.get('home_ypg', 0):.1f}, TO margin: {data.get('home_to_margin', 0)}\n"
                f"Away: {data.get('away_win_pct', 0):.0%}W, "
                f"YPG: {data.get('away_ypg', 0):.1f}, TO margin: {data.get('away_to_margin', 0)}\n"
                f"Injuries: {data.get('home_injuries', 'None')} vs {data.get('away_injuries', 'None')}\n"
                f"Spread: {data.get('spread', 0):+.1f} | Total: {data.get('total_line', 0)}\n"
                f"Sentiment: {data.get('sentiment_score', 0):.2f}"
            )
        elif sport == "epl":
            return base + (
                f"Home: Pos {data.get('home_position', 0)}, "
                f"GD: {data.get('home_goal_diff', 0):+d}, Form: {data.get('home_form', 'N/A')}\n"
                f"Away: Pos {data.get('away_position', 0)}, "
                f"GD: {data.get('away_goal_diff', 0):+d}, Form: {data.get('away_form', 'N/A')}\n"
                f"H2H: {data.get('h2h', 'N/A')}\n"
                f"Odds: Home {data.get('home_odds', 0):.2f} | Draw {data.get('draw_odds', 0):.2f} | "
                f"Away {data.get('away_odds', 0):.2f}\n"
                f"Sentiment: {data.get('sentiment_score', 0):.2f}"
            )
        elif sport in ("ipl", "big_bash"):
            return base + (
                f"Venue: {data.get('venue', 'Unknown')}\n"
                f"Home Form: {data.get('home_form', 'N/A')} | Away Form: {data.get('away_form', 'N/A')}\n"
                f"Top Scorers: {data.get('home_top_scorer', 'N/A')} vs {data.get('away_top_scorer', 'N/A')}\n"
                f"Pitch: {data.get('pitch_report', 'Standard')}\n"
                f"H2H: {data.get('h2h', 'N/A')}\n"
                f"Odds: Home {data.get('home_odds', 0):.2f} | Away {data.get('away_odds', 0):.2f}\n"
                f"Sentiment: {data.get('sentiment_score', 0):.2f}"
            )
        else:
            # Generic fallback
            return base + (
                f"Odds: Home {data.get('home_odds', 0):.2f} | Away {data.get('away_odds', 0):.2f}\n"
                f"Sentiment: {data.get('sentiment_score', 0):.2f}"
            )

    # ── Embed + store ─────────────────────────────────────────────────────────

    def store_match(
        self,
        event_id: str,
        sport: str,
        context_data: Dict[str, Any],
        outcome: Optional[str] = None,
    ) -> None:
        """Embed and store a match in ChromaDB."""
        context_string = self.build_context_string(sport, context_data)
        embedding = self._embedder.encode(context_string).tolist()

        metadata = {
            "sport": sport,
            "home_team": context_data.get("home_team", ""),
            "away_team": context_data.get("away_team", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "outcome": outcome or "unknown",
        }

        collection = self._collection(sport)
        collection.upsert(
            ids=[event_id],
            embeddings=[embedding],
            documents=[context_string],
            metadatas=[metadata],
        )
        log.info("vector_store.stored", event_id=event_id, sport=sport)

    # ── Retrieve ─────────────────────────────────────────────────────────────

    def retrieve_similar(
        self,
        sport: str,
        context_data: Dict[str, Any],
        top_k: int = TOP_K,
        max_age_days: int = 730,  # 2 years
    ) -> List[Dict[str, Any]]:
        """Find top-k most similar historical matchups."""
        context_string = self.build_context_string(sport, context_data)
        query_embedding = self._embedder.encode(context_string).tolist()

        collection = self._collection(sport)
        count = collection.count()
        if count == 0:
            log.warning("vector_store.empty", sport=sport)
            return []

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, count),
            include=["documents", "metadatas", "distances"],
        )

        matches = []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            if meta.get("timestamp", "") < cutoff:
                continue
            if dist > SIMILARITY_THRESHOLD:
                continue
            matches.append({
                "document": doc,
                "metadata": meta,
                "similarity": round(1 - dist, 4),
                "outcome": meta.get("outcome", "unknown"),
            })

        log.info("vector_store.retrieved", sport=sport, count=len(matches))
        return matches

    def get_collection_stats(self) -> Dict[str, int]:
        return {
            sport: self._collection(sport).count()
            for sport in SPORT_COLLECTIONS
        }


vector_store = VectorStore()
