from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sporty.predictor")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")

SUPABASE: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def poisson_probability(lmbda: float, k: int) -> float:
    """Calculates the Poisson probability of k events occurring given expected rate lmbda."""
    return (math.exp(-lmbda) * (lmbda**k)) / math.factorial(k)


def calculate_match_probabilities(home_elo: float, away_elo: float) -> dict:
    """Simple Elo-blended Poisson model to estimate match outcomes and goal expectancies."""
    elo_diff = home_elo - away_elo
    base_home_goals = 1.4 + (elo_diff / 400.0)
    base_away_goals = 1.1 - (elo_diff / 400.0)

    home_lambda = max(0.5, base_home_goals)
    away_lambda = max(0.5, base_away_goals)

    home_win_prob = 0.0
    draw_prob = 0.0
    away_win_prob = 0.0
    over_25_prob = 0.0

    max_goals = 6
    best_score = (1, 1)
    max_score_prob = -1.0

    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_probability(home_lambda, h) * poisson_probability(away_lambda, a)

            if h > a:
                home_win_prob += p
            elif h == a:
                draw_prob += p
            else:
                away_win_prob += p

            if h + a > 2.5:
                over_25_prob += p

            if p > max_score_prob:
                max_score_prob = p
                best_score = (h, a)

    return {
        "home_win": round(home_win_prob, 3),
        "draw": round(draw_prob, 3),
        "away_win": round(away_win_prob, 3),
        "over_25": round(over_25_prob, 3),
        "predicted_score": f"{best_score[0]}-{best_score[1]}",
    }


def generate_predictions() -> None:
    log.info("Fetching upcoming scheduled matches from Supabase for prediction...")
    now_iso = datetime.now(timezone.utc).isoformat()

    res = (
        SUPABASE.table("matches")
        .select("id, home_team_id, away_team_id, status, kickoff")
        .in_("status", ["SCHEDULED", "TIMED"])
        .gte("kickoff", now_iso)
        .execute()
    )
    matches = res.data or []

    if not matches:
        log.info("No upcoming scheduled matches found to predict.")
        return

    # Load all team Elo ratings in one query instead of 2 queries per match
    teams_res = SUPABASE.table("teams").select("id, elo").execute()
    elo_map: dict[int, float] = {
        t["id"]: float(t["elo"]) if t.get("elo") is not None else 1500.0
        for t in (teams_res.data or [])
    }

    predictions_batch: list[dict] = []

    for m in matches:
        home_elo = elo_map.get(m["home_team_id"], 1500.0)
        away_elo = elo_map.get(m["away_team_id"], 1500.0)

        preds = calculate_match_probabilities(home_elo, away_elo)

        predictions_batch.append(
            {
                "match_id": m["id"],
                "home_win_prob": preds["home_win"],
                "draw_prob": preds["draw"],
                "away_win_prob": preds["away_win"],
                "over_25_prob": preds["over_25"],
                "predicted_score": preds["predicted_score"],
            }
        )

    SUPABASE.table("predictions").upsert(
        predictions_batch,
        on_conflict="match_id",
    ).execute()

    log.info(
        "Phase 2 Prediction generation complete! Upserted %d upcoming predictions.",
        len(predictions_batch),
    )


if __name__ == "__main__":
    generate_predictions()