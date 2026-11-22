from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import Client, create_client

from goals_model import (
    DEFAULT_AWAY_GOALS,
    DEFAULT_EXPONENT,
    DEFAULT_HOME_GOALS,
    expected_goals,
    score_summary,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sporty.predictor")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")

SUPABASE: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Bump this whenever the model changes, so results of different models are never mixed.
MODEL_VERSION = "elo-poisson-v2"


def calculate_match_probabilities(
    home_elo: float,
    away_elo: float,
    home_avg: float,
    away_avg: float,
    exponent: float,
) -> dict:
    """
    Elo-scaled Poisson model. Expected goals come from the league's average goals
    scaled by the rating gap; 1X2, Over 2.5 and the likeliest score all derive
    from the same scoreline grid.
    """
    lam_h, lam_a = expected_goals(home_elo, away_elo, home_avg, away_avg, exponent)
    s = score_summary(lam_h, lam_a)

    return {
        "home_win": round(s["home_win"], 3),
        "draw": round(s["draw"], 3),
        "away_win": round(s["away_win"], 3),
        "over_25": round(s["over_25"], 3),
        "predicted_score": s["predicted_score"],
        "home_xg": round(s["home_xg"], 2),
        "away_xg": round(s["away_xg"], 2),
    }


def load_goal_params() -> tuple[dict[str, tuple[float, float]], float, bool]:
    """League goal averages and the fitted Elo exponent, saved by elo_updater.py."""
    stats: dict[str, tuple[float, float]] = {}
    exponent = DEFAULT_EXPONENT
    exponent_found = False

    try:
        rows = (
            SUPABASE.table("league_stats")
            .select("league, avg_home_goals, avg_away_goals")
            .execute()
            .data
            or []
        )
        for r in rows:
            stats[r["league"]] = (float(r["avg_home_goals"]), float(r["avg_away_goals"]))

        param_rows = (
            SUPABASE.table("model_params")
            .select("value")
            .eq("name", "elo_goal_exponent")
            .execute()
            .data
            or []
        )
        if param_rows:
            exponent = float(param_rows[0]["value"])
            exponent_found = True
    except Exception as exc:
        log.warning("Could not load fitted goal parameters, using fallbacks: %s", exc)

    return stats, exponent, bool(stats) and exponent_found


def generate_predictions() -> None:
    log.info("Fetching upcoming scheduled matches from Supabase for prediction...")
    now_iso = datetime.now(timezone.utc).isoformat()

    res = (
        SUPABASE.table("matches")
        .select("id, league, home_team_id, away_team_id, status, kickoff")
        .in_("status", ["SCHEDULED", "TIMED"])
        .gte("kickoff", now_iso)
        .execute()
    )
    matches = res.data or []

    if not matches:
        log.info("No upcoming scheduled matches found to predict.")
        return

    teams_res = SUPABASE.table("teams").select("id, elo").execute()
    elo_map: dict[int, float] = {
        t["id"]: float(t["elo"]) if t.get("elo") is not None else 1500.0
        for t in (teams_res.data or [])
    }

    league_stats, exponent, fitted = load_goal_params()
    if league_stats:
        fallback_home = sum(h for h, _ in league_stats.values()) / len(league_stats)
        fallback_away = sum(a for _, a in league_stats.values()) / len(league_stats)
    else:
        fallback_home, fallback_away = DEFAULT_HOME_GOALS, DEFAULT_AWAY_GOALS

    # If fitted values are missing, say so in the record instead of passing it off as v2.
    model_version = MODEL_VERSION if fitted else f"{MODEL_VERSION}-fallback"
    log.info(
        "Goal model: version=%s exponent=%.2f leagues with stats=%d",
        model_version,
        exponent,
        len(league_stats),
    )

    predictions_batch: list[dict] = []
    log_batch: list[dict] = []
    unknown_league = 0

    for m in matches:
        home_elo = elo_map.get(m["home_team_id"], 1500.0)
        away_elo = elo_map.get(m["away_team_id"], 1500.0)

        league = m.get("league")
        if league in league_stats:
            home_avg, away_avg = league_stats[league]
        else:
            home_avg, away_avg = fallback_home, fallback_away
            unknown_league += 1

        preds = calculate_match_probabilities(home_elo, away_elo, home_avg, away_avg, exponent)

        predictions_batch.append(
            {
                "match_id": m["id"],
                "home_win_prob": preds["home_win"],
                "draw_prob": preds["draw"],
                "away_win_prob": preds["away_win"],
                "over_25_prob": preds["over_25"],
                "predicted_score": preds["predicted_score"],
                "home_xg": preds["home_xg"],
                "away_xg": preds["away_xg"],
            }
        )

        # Permanent record of what the model said before kickoff. `first_predicted_at`
        # is left out on purpose so the database keeps the original timestamp.
        log_batch.append(
            {
                "match_id": m["id"],
                "model_version": model_version,
                "kickoff": m["kickoff"],
                "last_updated_at": now_iso,
                "home_elo": round(home_elo, 2),
                "away_elo": round(away_elo, 2),
                "home_win_prob": preds["home_win"],
                "draw_prob": preds["draw"],
                "away_win_prob": preds["away_win"],
                "over_25_prob": preds["over_25"],
                "predicted_score": preds["predicted_score"],
                "home_xg": preds["home_xg"],
                "away_xg": preds["away_xg"],
            }
        )

    if unknown_league:
        log.info("%d matches used the average of all leagues (no stats for their league name).", unknown_league)

    SUPABASE.table("predictions").upsert(
        predictions_batch,
        on_conflict="match_id",
    ).execute()

    log.info(
        "Phase 2 Prediction generation complete! Upserted %d upcoming predictions.",
        len(predictions_batch),
    )

    # A logging problem must never block the live predictions, but it must be loud.
    try:
        SUPABASE.table("prediction_log").upsert(
            log_batch,
            on_conflict="match_id",
        ).execute()
        log.info("Prediction log updated for %d upcoming matches.", len(log_batch))
    except Exception as exc:
        log.error("Could not write prediction_log (live predictions were saved): %s", exc)


if __name__ == "__main__":
    generate_predictions()