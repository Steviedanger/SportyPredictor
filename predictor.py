from __future__ import annotations

import os
import math
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sporty.predictor")

SUPABASE: Client = create_client(
    os.getenv("SUPABASE_URL", ""),
    os.getenv("SUPABASE_KEY", "")
)

def poisson_probability(lmbda: float, k: int) -> float:
    """Calculates the Poisson probability of k events occurring given expected rate lmbda."""
    return (math.exp(-lmbda) * (lmbda ** k)) / math.factorial(k)

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
        "predicted_score": f"{best_score[0]}-{best_score[1]}"
    }

def generate_predictions() -> None:
    log.info("Fetching scheduled matches from Supabase for prediction...")
    res = SUPABASE.table("matches").select("id, home_team_id, away_team_id").execute()
    matches = res.data
    
    if not matches:
        log.info("No scheduled matches found to predict.")
        return

    for m in matches:
        home_res = SUPABASE.table("teams").select("elo").eq("id", m["home_team_id"]).single().execute()
        away_res = SUPABASE.table("teams").select("elo").eq("id", m["away_team_id"]).single().execute()
        
        home_elo = home_res.data["elo"] if home_res.data and "elo" in home_res.data else 1500.0
        away_elo = away_res.data["elo"] if away_res.data and "elo" in away_res.data else 1500.0
        
        preds = calculate_match_probabilities(home_elo, away_elo)
        
        prediction_payload = {
            "match_id": m["id"],
            "home_win_prob": preds["home_win"],
            "draw_prob": preds["draw"],
            "away_win_prob": preds["away_win"],
            "over_25_prob": preds["over_25"],
            "predicted_score": preds["predicted_score"]
        }
        
        SUPABASE.table("predictions").upsert(prediction_payload, on_conflict="match_id").execute()
        log.info("Generated prediction for Match ID %s: %s", m["id"], preds["predicted_score"])

    log.info("Phase 2 Prediction generation complete!")

if __name__ == "__main__":
    generate_predictions()