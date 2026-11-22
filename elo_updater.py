from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from supabase import Client, create_client

from goals_model import fit_exponent, league_averages

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

BASE_URL = "https://api.football-data.org/v4"

# European competitions use a season that starts in summer (season=2026 means 2026/27).
EUROPEAN_COMPETITIONS = ["PL", "PD", "BL1", "SA", "FL1", "CL"]
# Brazil's Serie A runs on the calendar year.
CALENDAR_YEAR_COMPETITIONS = ["BSA"]

# Must match the league names stored in the `matches.league` column.
LEAGUE_NAMES = {
    "PL": "Premier League",
    "PD": "Primera Division",
    "BL1": "Bundesliga",
    "SA": "Serie A",
    "FL1": "Ligue 1",
    "CL": "UEFA Champions League",
    "BSA": "Campeonato Brasileiro Série A",
}

# How many seasons to replay besides the current one (1 = current + previous).
PREVIOUS_SEASONS = 1

# Free tier allows ~10 requests per minute; stay safely under it.
REQUEST_PAUSE_SECONDS = 6.5
RATE_LIMIT_WAIT_SECONDS = 65

K_FACTOR = 20.0
HOME_ADVANTAGE = 60.0
DEFAULT_ELO = 1500.0

# A match only helps fit the goal model once both teams have this many results behind them.
MIN_GAMES_FOR_FIT = 10


def season_years(code: str, now: datetime) -> list[int]:
    """Season start years to fetch for a competition, oldest first."""
    if code in CALENDAR_YEAR_COMPETITIONS:
        current = now.year
    else:
        current = now.year if now.month >= 7 else now.year - 1
    return [current - offset for offset in range(PREVIOUS_SEASONS, -1, -1)]


def fetch_competition_season(code: str, season: int) -> list[dict]:
    """Fetch all FINISHED matches for one competition and season. Never raises."""
    url = f"{BASE_URL}/competitions/{code}/matches"
    headers = {"X-Auth-Token": FOOTBALL_DATA_API_KEY}
    params = {"season": season, "status": "FINISHED"}

    for attempt in range(2):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
        except requests.RequestException as exc:
            log.warning("%s %s: request failed (%s). Skipping.", code, season, exc)
            return []

        if response.status_code == 429 and attempt == 0:
            log.warning(
                "%s %s: rate limited, waiting %ds and retrying once.",
                code,
                season,
                RATE_LIMIT_WAIT_SECONDS,
            )
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
            continue

        if response.status_code != 200:
            log.warning(
                "%s %s: HTTP %s body=%s. Skipping.",
                code,
                season,
                response.status_code,
                response.text[:300],
            )
            return []

        matches = response.json().get("matches", [])
        log.info("%s %s: fetched %d finished matches.", code, season, len(matches))
        return matches

    return []


def fetch_all_finished_matches() -> list[dict]:
    now = datetime.now(timezone.utc)
    all_matches: dict[int, dict] = {}
    first_request = True

    for code in EUROPEAN_COMPETITIONS + CALENDAR_YEAR_COMPETITIONS:
        for season in season_years(code, now):
            if not first_request:
                time.sleep(REQUEST_PAUSE_SECONDS)
            first_request = False

            for m in fetch_competition_season(code, season):
                match_id = m.get("id")
                if match_id is not None:
                    m["_code"] = code  # remember which competition this came from
                    all_matches[int(match_id)] = m

    matches = list(all_matches.values())
    matches.sort(key=lambda m: m.get("utcDate") or "")
    return matches


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def goal_diff_multiplier(goal_diff: int) -> float:
    """Bigger wins move ratings more (standard football-Elo scaling)."""
    gd = abs(goal_diff)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11.0 + gd) / 8.0


def save_goal_model_params(samples: list[tuple[str, float, int, int, bool]]) -> None:
    """
    samples: (competition code, pre-match elo diff, home goals, away goals, usable_for_fit).
    Saves per-league average goals and the fitted Elo-to-goals exponent.
    """
    named = [(LEAGUE_NAMES.get(code, code), hg, ag) for code, _d, hg, ag, _ok in samples]
    league_avgs = league_averages(named)

    if not league_avgs:
        log.warning("Goal model: not enough results to compute league averages.")
        return

    fit_samples = []
    for code, diff, hg, ag, usable in samples:
        avgs = league_avgs.get(LEAGUE_NAMES.get(code, code))
        if usable and avgs:
            fit_samples.append((diff, avgs[0], avgs[1], hg, ag))

    exponent, info = fit_exponent(fit_samples)
    now_iso = datetime.now(timezone.utc).isoformat()

    SUPABASE.table("league_stats").upsert(
        [
            {
                "league": league,
                "avg_home_goals": round(h, 4),
                "avg_away_goals": round(a, 4),
                "matches": n,
                "updated_at": now_iso,
            }
            for league, (h, a, n) in league_avgs.items()
        ],
        on_conflict="league",
    ).execute()

    for league, (h, a, n) in sorted(league_avgs.items()):
        log.info("Goal model: %s avg goals %.2f home / %.2f away over %d matches.", league, h, a, n)

    if info["fitted"]:
        SUPABASE.table("model_params").upsert(
            [
                {
                    "name": "elo_goal_exponent",
                    "value": exponent,
                    "samples": info["samples"],
                    "updated_at": now_iso,
                }
            ],
            on_conflict="name",
        ).execute()
        log.info(
            "Goal model: fitted Elo exponent %.2f from %d matches (%.4f log-likelihood gain per match vs ignoring Elo).",
            exponent,
            info["samples"],
            info["gain_per_match_vs_no_elo"],
        )
        if info["at_grid_edge"]:
            log.warning("Goal model: exponent sits at the edge of the search range; check the data.")
    else:
        log.warning(
            "Goal model: only %d usable matches, need more to fit the exponent; keeping the previous value.",
            info["samples"],
        )


def update_elo_ratings() -> None:
    """
    Recompute every rating from scratch: start all teams at DEFAULT_ELO, replay the
    full history in date order, then overwrite `teams.elo`. Because nothing is read
    back from the database, running this twice gives identical results.
    Also records each match's pre-match rating gap so the goal model can be fitted.
    """
    finished_matches = fetch_all_finished_matches()
    log.info("Replaying %d finished matches for Elo.", len(finished_matches))

    if not finished_matches:
        log.warning("No finished matches fetched; leaving existing ratings untouched.")
        return

    elo: dict[int, float] = {}
    names: dict[int, str] = {}
    games_played: dict[int, int] = defaultdict(int)
    samples: list[tuple[str, float, int, int, bool]] = []
    applied = 0

    for m in finished_matches:
        home = m.get("homeTeam") or {}
        away = m.get("awayTeam") or {}
        home_id = home.get("id")
        away_id = away.get("id")

        full_time = (m.get("score") or {}).get("fullTime") or {}
        home_goals = full_time.get("home")
        away_goals = full_time.get("away")

        if not home_id or not away_id or home_goals is None or away_goals is None:
            continue

        home_id, away_id = int(home_id), int(away_id)
        home_goals, away_goals = int(home_goals), int(away_goals)

        for tid, team in ((home_id, home), (away_id, away)):
            if tid not in elo:
                elo[tid] = DEFAULT_ELO
                names[tid] = team.get("name") or team.get("shortName") or f"Team {tid}"

        # Record the rating gap BEFORE this result is applied (no hindsight).
        code = m.get("_code")
        if code:
            usable = (
                games_played[home_id] >= MIN_GAMES_FOR_FIT
                and games_played[away_id] >= MIN_GAMES_FOR_FIT
            )
            samples.append((code, elo[home_id] - elo[away_id], home_goals, away_goals, usable))

        if home_goals > away_goals:
            actual_home = 1.0
        elif home_goals == away_goals:
            actual_home = 0.5
        else:
            actual_home = 0.0

        exp_home = expected_score(elo[home_id] + HOME_ADVANTAGE, elo[away_id])
        delta = (
            K_FACTOR
            * goal_diff_multiplier(home_goals - away_goals)
            * (actual_home - exp_home)
        )

        elo[home_id] += delta
        elo[away_id] -= delta
        games_played[home_id] += 1
        games_played[away_id] += 1
        applied += 1

    if not elo:
        log.warning("No valid full-time scores found; leaving existing ratings untouched.")
        return

    rows = [
        {"id": tid, "name": names[tid], "elo": round(rating, 2)}
        for tid, rating in elo.items()
    ]

    batch_size = 200
    for start in range(0, len(rows), batch_size):
        SUPABASE.table("teams").upsert(
            rows[start : start + batch_size],
            on_conflict="id",
        ).execute()

    ratings = sorted(elo.values())
    log.info(
        "Elo update complete: %d matches replayed, %d teams written (min %.0f, max %.0f).",
        applied,
        len(rows),
        ratings[0],
        ratings[-1],
    )

    # Ratings are already saved; a problem here must not undo that.
    try:
        save_goal_model_params(samples)
    except Exception as exc:
        log.error("Could not save goal model parameters (ratings were saved): %s", exc)


if __name__ == "__main__":
    update_elo_ratings()