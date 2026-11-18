"""Cross-source consensus for one matchday.

Tips are clustered by fixture, then by market and pick. Two or more sources
naming the same pick is a consensus. A market that was tipped but never
agreed falls back to the highest-confidence source instead of being dropped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from markets import (
    MARKET_1X2,
    MARKET_CORNERS,
    MARKET_CORRECT_SCORE,
    MARKET_HANDICAP,
    MARKET_OVER_UNDER,
    Selection,
    display_pick,
    estimate_odds,
    flip_pick,
    normalize_team,
    teams_match,
)
from scraper import SourceResult

_MARKET_ORDER = {
    MARKET_1X2: 0,
    MARKET_OVER_UNDER: 1,
    MARKET_HANDICAP: 2,
    MARKET_CORNERS: 3,
    MARKET_CORRECT_SCORE: 4,
}


@dataclass
class ConsensusPick:
    fixture_key: str
    home: str
    away: str
    kickoff: Optional[datetime]
    league: Optional[str]
    market: str
    pick: str
    sources: list[str]
    support: int
    confidence: float
    odds: float
    is_consensus: bool
    rationale: str

    @property
    def fixture(self) -> str:
        return f"{self.home} vs {self.away}"

    @property
    def label(self) -> str:
        return display_pick(self.market, self.pick, self.home, self.away)


@dataclass
class AggregateReport:
    picks: list[ConsensusPick] = field(default_factory=list)
    source_counts: dict[str, int] = field(default_factory=dict)
    source_errors: dict[str, str] = field(default_factory=dict)

    @property
    def consensus(self) -> list[ConsensusPick]:
        return [pick for pick in self.picks if pick.is_consensus]

    @property
    def fallbacks(self) -> list[ConsensusPick]:
        return [pick for pick in self.picks if not pick.is_consensus]


@dataclass
class _Cluster:
    home_n: str
    away_n: str
    home: str
    away: str
    rows: list[dict] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.home_n}|{self.away_n}"


def aggregate(results: list[SourceResult]) -> AggregateReport:
    report = AggregateReport(
        source_counts={item.source: len(item.selections) for item in results},
        source_errors={
            item.source: item.error for item in results if item.error
        },
    )
    selections = [tip for item in results for tip in item.selections]
    clusters = _cluster(selections)
    rows: list[dict] = []
    for cluster in clusters:
        rows.extend(cluster.rows)
    if not rows:
        return report

    frame = pd.DataFrame(rows)
    grouped = (
        frame.groupby(["fixture_key", "market", "pick"], dropna=False)
        .apply(_reduce_group, include_groups=False)
        .reset_index()
    )
    coverage = (
        frame.groupby(["fixture_key", "market"])["source"]
        .nunique()
        .rename("coverage")
    )
    grouped = grouped.merge(coverage, on=["fixture_key", "market"], how="left")

    chosen: list[ConsensusPick] = []
    for _, market_rows in grouped.groupby(["fixture_key", "market"], dropna=False):
        market_rows = market_rows.sort_values(
            by=["support", "confidence"],
            ascending=[False, False],
        )
        best = market_rows.iloc[0]
        support = int(best["support"])
        sources = list(best["sources"])
        mean_confidence = float(best["confidence"]) if pd.notna(best["confidence"]) else 0.55
        agreed = support >= 2
        if agreed:
            share = support / max(int(best["coverage"]), support)
            confidence = min(0.93, 0.55 * mean_confidence + 0.45 * share)
            rationale = f"Agreed by {support} sources: {', '.join(sources)}."
        else:
            confidence = min(0.7, mean_confidence)
            rationale = (
                f"No overlapping tip. Fallback uses {sources[0]} "
                f"({confidence:.0%})."
            )
        odds_value = best["odds"]
        if odds_value is None or (isinstance(odds_value, float) and math.isnan(odds_value)):
            odds = estimate_odds(str(best["market"]), confidence, str(best["pick"]))
        else:
            odds = round(float(odds_value), 2)
        kickoff = best["kickoff"]
        if kickoff is not None and pd.isna(kickoff):
            kickoff = None
        chosen.append(
            ConsensusPick(
                fixture_key=str(best["fixture_key"]),
                home=str(best["home"]),
                away=str(best["away"]),
                kickoff=kickoff if isinstance(kickoff, datetime) else None,
                league=best["league"] if isinstance(best["league"], str) else None,
                market=str(best["market"]),
                pick=str(best["pick"]),
                sources=sources,
                support=support,
                confidence=round(confidence, 3),
                odds=odds,
                is_consensus=agreed,
                rationale=rationale,
            )
        )

    chosen.sort(
        key=lambda pick: (
            pick.kickoff or datetime.max,
            pick.home,
            _MARKET_ORDER.get(pick.market, 9),
        )
    )
    report.picks = chosen
    return report


def _cluster(selections: list[Selection]) -> list[_Cluster]:
    clusters: list[_Cluster] = []
    for tip in selections:
        home_n = normalize_team(tip.home)
        away_n = normalize_team(tip.away)
        if len(home_n) < 2 or len(away_n) < 2 or not tip.pick:
            continue
        placed = False
        for cluster in clusters:
            flipped = False
            if teams_match(home_n, cluster.home_n) and teams_match(away_n, cluster.away_n):
                flipped = False
            elif teams_match(home_n, cluster.away_n) and teams_match(away_n, cluster.home_n):
                flipped = True
            else:
                continue
            _absorb(cluster, tip, flipped)
            placed = True
            break
        if not placed:
            cluster = _Cluster(home_n, away_n, tip.home, tip.away)
            _absorb(cluster, tip, flipped=False)
            clusters.append(cluster)
    return clusters


def _absorb(cluster: _Cluster, tip: Selection, flipped: bool) -> None:
    label_home = tip.away if flipped else tip.home
    label_away = tip.home if flipped else tip.away
    if len(label_home) > len(cluster.home):
        cluster.home = label_home
    if len(label_away) > len(cluster.away):
        cluster.away = label_away
    pick = flip_pick(tip.market, tip.pick) if flipped else tip.pick
    cluster.rows.append(
        {
            "fixture_key": cluster.key,
            "market": tip.market,
            "pick": pick,
            "source": tip.source,
            "confidence": tip.confidence if tip.confidence is not None else 0.55,
            "odds": tip.odds if tip.odds is not None else math.nan,
            "home": cluster.home,
            "away": cluster.away,
            "kickoff": tip.kickoff,
            "league": tip.league,
        }
    )


def _reduce_group(group: pd.DataFrame) -> pd.Series:
    odds = group["odds"].dropna()
    kickoffs = [value for value in group["kickoff"] if isinstance(value, datetime)]
    leagues = [value for value in group["league"] if isinstance(value, str) and value.strip()]
    return pd.Series(
        {
            "support": int(group["source"].nunique()),
            "sources": tuple(sorted(set(group["source"].tolist()))),
            "confidence": float(group["confidence"].mean()),
            "odds": float(odds.median()) if not odds.empty else math.nan,
            "home": str(group["home"].iloc[-1]),
            "away": str(group["away"].iloc[-1]),
            "kickoff": min(kickoffs) if kickoffs else None,
            "league": leagues[0] if leagues else None,
        }
    )
