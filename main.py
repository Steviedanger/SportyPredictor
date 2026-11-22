import logging
from scraper import ingest_teams_and_fixtures
from elo_updater import update_elo_ratings
from predictor import generate_predictions

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)
logger = logging.getLogger("sporty.main")


def run_pipeline() -> None:
    logger.info("Starting SportyPredictor daily pipeline...")
    try:
        logger.info("--- Step 1: Ingesting fixtures & team data ---")
        ingest_teams_and_fixtures()

        logger.info("--- Step 2: Updating team Elo ratings from finished matches ---")
        try:
            update_elo_ratings()
        except Exception as exc:
            # Don't let a rating failure block predictions; use the ratings we already have.
            logger.error("Elo update failed, continuing with existing ratings: %s", exc)

        logger.info("--- Step 3: Running Poisson + Elo prediction engine ---")
        generate_predictions()

        logger.info("SportyPredictor pipeline completed successfully!")
    except Exception as exc:
        logger.error("Pipeline failed with error: %s", exc)
        raise exc


if __name__ == "__main__":
    run_pipeline()