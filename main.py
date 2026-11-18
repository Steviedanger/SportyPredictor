from __future__ import annotations

import logging
from scraper import ingest_teams_and_fixtures
from predictor import generate_predictions

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sporty.main")

def run_pipeline() -> None:
    log.info("Starting SportyPredictor daily pipeline...")
    try:
        log.info("--- Step 1: Ingesting fixtures & team data ---")
        ingest_teams_and_fixtures()
        
        log.info("--- Step 2: Running Poisson + Elo prediction engine ---")
        generate_predictions()
        
        log.info("SportyPredictor daily pipeline completed successfully!")
    except Exception as exc:
        log.error("Pipeline failed with error: %s", exc)
        raise exc

if __name__ == "__main__":
    run_pipeline()