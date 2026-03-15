"""
app/scrapers/espn_scraper.py
Public ESPN data — injury reports, recent form, game results.
No auth required. Uses public ESPN API endpoints + HTML scraping fallback.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from app.core.cache import get_cache
from app.core.logging import get_logger

log = get_logger(__name__)

ESPN_SPORT_MAP = {
    "basketball_nba": ("nba", "basketball"),
    "americanfootball_nfl": ("nfl", "football"),
    "icehockey_nhl": ("nhl", "hockey"),
    "baseball_mlb": ("mlb", "baseball"),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


class ESPNScraper:

    # ─── Injury report ────────────────────────────────────────────────────────

    async def get_injuries(self, sport: str) -> List[Dict]:
        """Fetch injury report from ESPN's unofficial API."""
        cache = await get_cache()
        cache_key = f"injuries:{sport}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        league, sport_path = ESPN_SPORT_MAP.get(sport, ("nfl", "football"))
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league}/injuries"

        try:
            async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                injuries = self._parse_injuries(data)
                await cache.set(cache_key, injuries, ttl_seconds=3600)
                log.info("espn.injuries.fetched", sport=sport, count=len(injuries))
                return injuries
        except Exception as e:
            log.warning("espn.injuries.error", sport=sport, error=str(e))
            return []

    def _parse_injuries(self, data: dict) -> List[Dict]:
        results = []
        try:
            for item in data.get("injuries", []):
                team = item.get("team", {}).get("displayName", "")
                for inj in item.get("injuries", []):
                    athlete = inj.get("athlete", {})
                    results.append({
                        "team": team,
                        "player": athlete.get("displayName", "Unknown"),
                        "position": athlete.get("position", {}).get("abbreviation", ""),
                        "status": inj.get("status", ""),
                        "details": inj.get("details", {}).get("detail", ""),
                    })
        except Exception:
            pass
        return results

    def get_team_injuries(self, injuries: List[Dict], team_name: str) -> List[Dict]:
        """Filter injuries to a specific team."""
        team_lower = team_name.lower()
        return [
            i for i in injuries
            if team_lower in i.get("team", "").lower()
        ]

    def format_injury_report(self, injuries: List[Dict]) -> str:
        """Format injuries as a readable string for the prompt."""
        if not injuries:
            return "No significant injuries reported."
        parts = []
        for i in injuries[:5]:  # top 5
            parts.append(f"{i['player']} ({i['position']}) — {i['status']}: {i['details']}")
        return "; ".join(parts)

    # ─── Recent form ──────────────────────────────────────────────────────────

    async def get_team_recent_form(self, sport: str, team_name: str, games: int = 5) -> Dict:
        """Fetch recent game results for a team from ESPN."""
        cache = await get_cache()
        cache_key = f"form:{sport}:{team_name.replace(' ', '_')}"
        cached = await cache.get(cache_key)
        if cached:
            return cached

        league, sport_path = ESPN_SPORT_MAP.get(sport, ("nfl", "football"))
        # ESPN team search
        search_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league}/teams"

        try:
            async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
                resp = await client.get(search_url)
                resp.raise_for_status()
                teams_data = resp.json()
                team_id = self._find_team_id(teams_data, team_name)

                if not team_id:
                    return {"record": "N/A", "last_5": [], "streak": "Unknown"}

                # Fetch team schedule
                sched_url = (
                    f"https://site.api.espn.com/apis/site/v2/sports/"
                    f"{sport_path}/{league}/teams/{team_id}/schedule"
                )
                sched_resp = await client.get(sched_url)
                sched_resp.raise_for_status()
                form = self._parse_form(sched_resp.json(), team_name, games)
                await cache.set(cache_key, form, ttl_seconds=3600)
                return form

        except Exception as e:
            log.warning("espn.form.error", team=team_name, error=str(e))
            return {"record": "N/A", "last_5": [], "streak": "Unknown"}

    def _find_team_id(self, data: dict, team_name: str) -> Optional[str]:
        try:
            sports = data.get("sports", [])
            for sport in sports:
                for league in sport.get("leagues", []):
                    for team in league.get("teams", []):
                        t = team.get("team", {})
                        if team_name.lower() in t.get("displayName", "").lower():
                            return t.get("id")
        except Exception:
            pass
        return None

    def _parse_form(self, data: dict, team_name: str, games: int) -> Dict:
        try:
            events = data.get("events", [])
            completed = [e for e in events if e.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed", False)]
            recent = completed[-games:] if len(completed) >= games else completed

            results = []
            wins = losses = 0
            for event in recent:
                comp = event.get("competitions", [{}])[0]
                for competitor in comp.get("competitors", []):
                    if team_name.lower() in competitor.get("team", {}).get("displayName", "").lower():
                        outcome = competitor.get("winner", False)
                        score = competitor.get("score", "?")
                        opp_comp = [c for c in comp.get("competitors", []) if c != competitor]
                        opp = opp_comp[0].get("team", {}).get("abbreviation", "OPP") if opp_comp else "OPP"
                        opp_score = opp_comp[0].get("score", "?") if opp_comp else "?"
                        results.append({
                            "result": "W" if outcome else "L",
                            "score": f"{score}-{opp_score}",
                            "opponent": opp,
                        })
                        if outcome:
                            wins += 1
                        else:
                            losses += 1

            # Streak
            streak = "Unknown"
            if results:
                last = results[-1]["result"]
                count = sum(1 for r in reversed(results) if r["result"] == last)
                streak = f"{count}{last}"

            return {
                "record": f"{wins}-{losses}",
                "last_5": results,
                "streak": streak,
            }
        except Exception as e:
            log.warning("espn.form.parse.error", error=str(e))
            return {"record": "N/A", "last_5": [], "streak": "Unknown"}

    def format_form(self, form: Dict, team: str) -> str:
        record = form.get("record", "N/A")
        streak = form.get("streak", "N/A")
        last_5 = form.get("last_5", [])
        results_str = " ".join([f"{r['result']}({r['opponent']})" for r in last_5])
        return f"{team}: {record} record, streak: {streak}, last games: {results_str}"


espn_scraper = ESPNScraper()
