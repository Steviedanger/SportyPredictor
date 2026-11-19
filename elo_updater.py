from __future__ import annotations

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
log = logging.getLogger("sporty.elo")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY or not FOOTBALL_DATA_API_KEY:
    raise RuntimeError("Missing SUPABASE_URL, SUPABASE_KEY, or FOOTBALL_DATA_API_KEY")

SUPABASE: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TARGET_COMPETITIONS = ["PL", "PD", "BL1", "SA", "FL1", "CL", "BSA"]
LOOKBACK_DAYS = 45
K_FACTOR = 24.0
HOME_ADVANTAGE = 60.0
DEFAULT_ELO = 1500.0


def fetch_finished_matches() -> list[dict]:
    now_utc = datetime.now(timezone.utc)
    date_from = (now_utc - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    date_to = now_utc.strftime("%Y-%m-%d")

    url = "https://api.football-data.org/v4/matches"
    headers = {"X-Auth-Token": FOOTBALL_DATA_API_KEY}
    params = {
        "competitions": ",".join(TARGET_COMPETITIONS),
        "dateFrom": date_from,
        "dateTo": date_to,
        "status": "FINISHED",
    }

    log.info(
        "Fetching finished matches from Football-Data.org (%s to %s)...",
        date_from,
        date_to,
    )

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    matches = payload.get("matches", [])
    matches.sort(key=lambda m: m.get("utcDate") or "")
    return matches


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def update_elo_ratings() -> None:
    finished_matches = fetch_finished_matches()
    log.info("Found %d finished matches to process for Elo updates.", len(finished_matches))

    if not finished_matches:
        return

    # Ensure all teams appearing in finished matches exist in `teams` and load current Elo
    teams_res = SUPABASE.table("teams").select("id, name, elo").execute()
    elo_by_id: dict[int, float] = {}
    name_by_id: dict[int, str] = {}

    for row in teams_res.data or []:
        tid = int(row["id"])
        elo_by_id[tid] = float(row["elo"]) if row.get("elo") is not None else DEFAULT_ELO
        name_by_id[tid] = row.get("name") or f"Team {tid}"

    touched_team_ids: set[int] = set()
    applied_count = 0

    for m in finished_matches:
        home = m.get("homeTeam") or {}
        away = m.get("awayTeam") or {}
        home_id = home.get("id")
        away_id = away.get("id")

        score = m.get("score") or {}
        full_time = score.get("fullTime") or {}
        home_goals = full_time.get("home")
        away_goals = full_time.get("away")

        if not home_id or not away_id or home_goals is None or away_goals is None:
            continue

        home_id = int(home_id)
        away_id = int(away_id)
        home_goals = int(home_goals)
        away_goals = int(away_goals)

        if home_id not in elo_by_id:
            elo_by_id[home_id] = DEFAULT_ELO
            name_by_id[home_id] = home.get("name") or home.get("shortName") or f"Team {home_id}"

        if away_id not in elo_by_id:
            elo_by_id[away_id] = DEFAULT_ELO
            name_by_id[away_id] = away.get("name") or away.get("shortName") or f"Team {away_id}"

        if home_goals > away_goals:
            actual_home = 1.0
        elif home_goals == away_goals:
            actual_home = 0.5
        else:
            actual_home = 0.0

        exp_home = expected_score(elo_by_id[home_id] + HOME_ADVANTAGE, elo_by_id[away_id])
        delta = K_FACTOR * (actual_home - exp_home)

        elo_by_id[home_id] = round(elo_by_id[home_id] + delta, 2)
        elo_by_id[away_id] = round(elo_by_id[away_id] - delta, 2)

        touched_team_ids.add(home_id)
        touched_team_ids.add(away_id)
        applied_count += 1

    if not touched_team_ids:
        log.info("No valid full-time match scores found to update Elo.")
        return

    # First upsert any newly seen teams so foreign keys / rows exist, then update Elo
    team_updates = [
        {
            "id": tid,
            "name": name_by_id.get(tid, f"Team {tid}"),
            "elo": elo_by_id[tid],
        }
        for tid in touched_team_ids
    ]

    SUPABASE.table("teams").upsert(
        team_updates,
        on_conflict="id",
    ).execute()

    log.info(
        "Elo update complete: replayed %d finished matches and updated %d teams in Supabase.",
        applied_count,
        len(team_updates),
    )


if __name__ == "__main__":
    update_elo_ratings()