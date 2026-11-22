from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from supabase import Client, create_client

from elo_updater import (
    CALENDAR_YEAR_COMPETITIONS,
    EUROPEAN_COMPETITIONS,
    REQUEST_PAUSE_SECONDS,
    fetch_competition_season,
    season_years,
)

load_dotenv()
log = logging.getLogger("sporty.grader")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")

SUPABASE: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Only grade matches that kicked off at least this long ago.
GRADE_AFTER_HOURS = 3


def grade_row(row: dict, home_goals: int, away_goals: int) -> dict:
    """Score one logged prediction against the real 90-minute result."""
    probs = {
        "H": float(row["home_win_prob"]),
        "D": float(row["draw_prob"]),
        "A": float(row["away_win_prob"]),
    }

    if home_goals > away_goals:
        outcome = "H"
    elif home_goals == away_goals:
        outcome = "D"
    else:
        outcome = "A"

    pick = max(probs, key=probs.get)
    brier = sum((p - (1.0 if k == outcome else 0.0)) ** 2 for k, p in probs.items())
    log_loss = -math.log(max(probs[outcome], 1e-6))

    over_actual = (home_goals + away_goals) > 2.5
    p_over = float(row["over_25_prob"])
    brier_over = (p_over - (1.0 if over_actual else 0.0)) ** 2

    return {
        "home_goals": home_goals,
        "away_goals": away_goals,
        "outcome": outcome,
        "pick": pick,
        "pick_correct": pick == outcome,
        "brier_1x2": round(brier, 5),
        "log_loss_1x2": round(log_loss, 5),
        "over25_actual": over_actual,
        "brier_over25": round(brier_over, 5),
        "exact_score_hit": f"{home_goals}-{away_goals}" == row.get("predicted_score"),
        "graded_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_current_season_results() -> dict[int, tuple[int, int]]:
    """match id -> (home goals, away goals) at 90 minutes, current season only."""
    now = datetime.now(timezone.utc)
    results: dict[int, tuple[int, int]] = {}

    for code in EUROPEAN_COMPETITIONS + CALENDAR_YEAR_COMPETITIONS:
        # Pause first: the Elo step just made requests and the free tier is rate limited.
        time.sleep(REQUEST_PAUSE_SECONDS)
        season = season_years(code, now)[-1]

        for m in fetch_competition_season(code, season):
            score = m.get("score") or {}
            # regularTime is the 90-minute score when a match went to extra time.
            ft = score.get("regularTime") or score.get("fullTime") or {}
            home, away = ft.get("home"), ft.get("away")
            if m.get("id") is None or home is None or away is None:
                continue
            results[int(m["id"])] = (int(home), int(away))

    return results


def grade_finished_predictions() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=GRADE_AFTER_HOURS)).isoformat()

    res = (
        SUPABASE.table("prediction_log")
        .select("match_id, home_win_prob, draw_prob, away_win_prob, over_25_prob, predicted_score")
        .is_("graded_at", "null")
        .lt("kickoff", cutoff)
        .execute()
    )
    pending = res.data or []

    if not pending:
        log.info("Grader: no logged predictions waiting to be graded.")
        return

    log.info("Grader: %d logged predictions are past kickoff; fetching results...", len(pending))
    results = fetch_current_season_results()

    graded = 0
    brier_total = 0.0

    for row in pending:
        result = results.get(int(row["match_id"]))
        if result is None:
            continue  # not finished yet, postponed, or outside the fetched competitions

        scored = grade_row(row, result[0], result[1])
        SUPABASE.table("prediction_log").update(scored).eq("match_id", row["match_id"]).execute()
        graded += 1
        brier_total += scored["brier_1x2"]

    if graded:
        log.info(
            "Grader: graded %d matches (avg Brier for this batch %.3f; guessing 1/3 each scores 0.667).",
            graded,
            brier_total / graded,
        )
    else:
        log.info("Grader: none of the %d pending matches have a final result yet.", len(pending))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    grade_finished_predictions()