from __future__ import annotations

import os
import logging
from datetime import datetime
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sporty.ingest")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")

SUPABASE: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

def fetch_football_data(endpoint: str) -> dict:
    url = f"https://api.football-data.org/v4/{endpoint}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()

def ingest_teams_and_fixtures() -> None:
    if not SUPABASE:
        log.error("Supabase client not initialized. Check your environment variables.")
        return

    log.info("Fetching upcoming fixtures from Football-Data.org...")
    data = fetch_football_data("matches?status=SCHEDULED")
    
    matches = data.get("matches", [])
    log.info("Found %s scheduled matches to process.", len(matches))

    for m in matches:
        home = m["homeTeam"]
        away = m["awayTeam"]
        competition = m["competition"]["name"]
        
        # Upsert Teams
        for team_info in [home, away]:
            team_data = {
                "id": team_info["id"],
                "name": team_info["name"]
            }
            SUPABASE.table("teams").upsert(team_data).execute()

        # Upsert Match
        match_data = {
            "id": m["id"],
            "league": competition,
            "home_team_id": home["id"],
            "away_team_id": away["id"],
            "kickoff": m["utcDate"],
            "status": m["status"]
        }
        SUPABASE.table("matches").upsert(match_data).execute()

    log.info("Phase 1 Ingestion complete: Teams and fixtures successfully stored in Supabase.")

if __name__ == "__main__":
    ingest_teams_and_fixtures()