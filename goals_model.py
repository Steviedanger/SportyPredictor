"""
Goal-model maths shared by elo_updater.py (fits the parameters) and predictor.py
(uses them). Pure functions only: no network, no database.

Model: for a match with pre-match Elo difference d = home_elo - away_elo,
    strength = 10 ** (d / 400)
    home expected goals = league_home_avg * strength ** k
    away expected goals = league_away_avg / strength ** k
where `k` (the "exponent") says how strongly a rating gap turns into goals. It is
fitted from real results instead of being guessed.
"""
from __future__ import annotations

import math

# Fallbacks, used only when no fitted values are available yet.
DEFAULT_HOME_GOALS = 1.45
DEFAULT_AWAY_GOALS = 1.15
DEFAULT_EXPONENT = 0.5

MIN_LEAGUE_MATCHES = 30   # need at least this many results to trust a league average
MIN_FIT_SAMPLES = 300     # need at least this many matches to fit the exponent
EXPONENT_GRID = [round(0.05 * i, 2) for i in range(1, 17)]  # 0.05 ... 0.80

MAX_GOALS = 8             # scoreline grid goes 0..8 goals per team

LAMBDA_HOME_BOUNDS = (0.2, 4.5)
LAMBDA_AWAY_BOUNDS = (0.15, 4.0)


def _lambdas(elo_diff: float, home_avg: float, away_avg: float, exponent: float) -> tuple[float, float]:
    scale = 10.0 ** ((elo_diff / 400.0) * exponent)  # == strength ** exponent
    lam_h = min(LAMBDA_HOME_BOUNDS[1], max(LAMBDA_HOME_BOUNDS[0], home_avg * scale))
    lam_a = min(LAMBDA_AWAY_BOUNDS[1], max(LAMBDA_AWAY_BOUNDS[0], away_avg / scale))
    return lam_h, lam_a


def expected_goals(
    home_elo: float,
    away_elo: float,
    home_avg: float,
    away_avg: float,
    exponent: float,
) -> tuple[float, float]:
    return _lambdas(home_elo - away_elo, home_avg, away_avg, exponent)


def _poisson_pmf(lam: float, k: int) -> float:
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def _outcome(h: int, a: int) -> str:
    return "H" if h > a else "D" if h == a else "A"


def score_summary(lam_h: float, lam_a: float, max_goals: int = MAX_GOALS) -> dict:
    """
    1X2, over 2.5 and a suggested score, all from one scoreline grid.

    The suggested score is the most likely scoreline that agrees with the picked 1X2
    result AND the picked Over/Under 2.5 side, so the card never contradicts itself
    (the single likeliest scoreline is almost always 1-0 or 1-1, even when Over 2.5
    is favoured). `likeliest_score` keeps that raw single most likely cell.
    """
    home_pmf = [_poisson_pmf(lam_h, k) for k in range(max_goals + 1)]
    away_pmf = [_poisson_pmf(lam_a, k) for k in range(max_goals + 1)]
    mass = sum(home_pmf) * sum(away_pmf)

    cells: list[tuple[float, int, int]] = []
    home_win = draw = away_win = over_25 = 0.0

    for h, ph in enumerate(home_pmf):
        for a, pa in enumerate(away_pmf):
            p = ph * pa / mass
            cells.append((p, h, a))
            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p
            if h + a > 2.5:
                over_25 += p

    # Decide the picks from the same rounded numbers the page will display, with the
    # same tie rules as the page (first listed wins: home, draw, away; Over at 50%).
    rounded = [("H", round(home_win, 3)), ("D", round(draw, 3)), ("A", round(away_win, 3))]
    pick = max(rounded, key=lambda t: t[1])[0]
    over_side = round(over_25, 3) >= 0.5

    consistent = [
        c for c in cells
        if _outcome(c[1], c[2]) == pick and ((c[1] + c[2]) > 2.5) == over_side
    ]
    _, best_h, best_a = max(consistent)
    _, raw_h, raw_a = max(cells)

    return {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "over_25": over_25,
        "predicted_score": f"{best_h}-{best_a}",
        "likeliest_score": f"{raw_h}-{raw_a}",
        "home_xg": lam_h,
        "away_xg": lam_a,
    }


def league_averages(rows: list[tuple[str, int, int]]) -> dict[str, tuple[float, float, int]]:
    """rows of (league, home_goals, away_goals) -> {league: (avg_home, avg_away, n)}."""
    totals: dict[str, list[float]] = {}
    for league, hg, ag in rows:
        t = totals.setdefault(league, [0.0, 0.0, 0])
        t[0] += hg
        t[1] += ag
        t[2] += 1
    return {
        league: (t[0] / t[2], t[1] / t[2], int(t[2]))
        for league, t in totals.items()
        if t[2] >= MIN_LEAGUE_MATCHES
    }


def _loglik(samples: list[tuple[float, float, float, int, int]], exponent: float) -> float:
    """Poisson log-likelihood (constant terms dropped) of the observed scores."""
    total = 0.0
    for diff, home_avg, away_avg, hg, ag in samples:
        lam_h, lam_a = _lambdas(diff, home_avg, away_avg, exponent)
        total += hg * math.log(lam_h) - lam_h + ag * math.log(lam_a) - lam_a
    return total


def fit_exponent(
    samples: list[tuple[float, float, float, int, int]],
    grid: list[float] | None = None,
) -> tuple[float, dict]:
    """
    samples: (pre-match elo diff, league home avg, league away avg, home goals, away goals).
    Picks the exponent that makes the real scores most likely. Returns (exponent, info).
    """
    grid = grid or EXPONENT_GRID
    if len(samples) < MIN_FIT_SAMPLES:
        return DEFAULT_EXPONENT, {"fitted": False, "samples": len(samples)}

    scored = [(k, _loglik(samples, k)) for k in grid]
    best_k, best_ll = max(scored, key=lambda kv: kv[1])
    flat_ll = _loglik(samples, 0.0)  # ratings ignored entirely

    return best_k, {
        "fitted": True,
        "samples": len(samples),
        "gain_per_match_vs_no_elo": (best_ll - flat_ll) / len(samples),
        "at_grid_edge": best_k in (grid[0], grid[-1]),
    }