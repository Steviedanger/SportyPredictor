import logging
import os
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)
logger = logging.getLogger("sporty.ingest")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY or not FOOTBALL_DATA_API_KEY:
    raise RuntimeError("Missing SUPABASE_URL, SUPABASE_KEY, or FOOTBALL_DATA_API_KEY")

SUPABASE: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Free-tier supported major competitions on Football-Data.org
TARGET_COMPETITIONS = ["PL", "PD", "BL1", "SA", "FL1", "CL", "BSA"]
LOOKAHEAD_DAYS = 10


def fetch_upcoming_matches() -> list[dict]:
    now_utc = datetime.now(timezone.utc)
    date_from = now_utc.strftime("%Y-%m-%d")
    date_to = (now_utc + timedelta(days=LOOKAHEAD_DAYS)).strftime("%Y-%m-%d")

    url = "https://api.football-data.org/v4/matches"
    headers = {"X-Auth-Token": FOOTBALL_DATA_API_KEY}
    params = {
        "competitions": ",".join(TARGET_COMPETITIONS),
        "dateFrom": date_from,
        "dateTo": date_to,
        "status": "SCHEDULED,TIMED",
    }

    logger.info(
        "Fetching upcoming fixtures from Football-Data.org (%s to %s) across %s...",
        date_from,
        date_to,
        ",".join(TARGET_COMPETITIONS),
    )

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload.get("matches", [])


def ingest_fixtures() -> None:
    matches = fetch_upcoming_matches()
    logger.info("Found %d scheduled matches to process.", len(matches))

    if not matches:
        return

    teams_by_id: dict[int, dict] = {}
    match_rows: list[dict] = []

    for m in matches:
        home = m.get("homeTeam") or {}
        away = m.get("awayTeam") or {}
        home_id = home.get("id")
        away_id = away.get("id")

        # Skip placeholder/TBD knockout fixtures that lack concrete team IDs
        if not home_id or not away_id:
            continue

        if home_id not in teams_by_id:
            teams_by_id[home_id] = {
                "id": home_id,
                "name": home.get("name") or home.get("shortName") or f"Team {home_id}",
            }

        if away_id not in teams_by_id:
            teams_by_id[away_id] = {
                "id": away_id,
                "name": away.get("name") or away.get("shortName") or f"Team {away_id}",
            }

        competition = m.get("competition") or {}
        match_rows.append(
            {
                "id": m["id"],
                "league": competition.get("name") or competition.get("code") or "Unknown League",
                "kickoff": m["utcDate"],
                "status": m.get("status", "SCHEDULED"),
                "home_team_id": home_id,
                "away_team_id": away_id,
            }
        )

    if teams_by_id:
        SUPABASE.table("teams").upsert(
            list(teams_by_id.values()),
            on_conflict="id",
            ignore_duplicates=False,
        ).execute()

    if match_rows:
        SUPABASE.table("matches").upsert(
            match_rows,
            on_conflict="id",
        ).execute()

    logger.info(
        "Phase 1 Ingestion complete: %d teams and %d fixtures stored in Supabase.",
        len(teams_by_id),
        len(match_rows),
    )


if __name__ == "__main__":
    ingest_fixtures()