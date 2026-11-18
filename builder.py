"""Build one accumulator for each combined-odds tier.

Short prices from agreed 1X2 and modest totals fill the 2, 5, and 10 slips.
Correct scores, corner lines, and high goal lines are reserved for the
25+, 50, and 100+ slips. Each ticket uses a fixture at most once.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aggregator import ConsensusPick
from markets import (
    MARKET_1X2,
    MARKET_CORNERS,
    MARKET_CORRECT_SCORE,
    MARKET_HANDICAP,
    MARKET_OVER_UNDER,
    goal_line,
)


@dataclass(frozen=True)
class Tier:
    name: str
    target: float
    low: float
    high: float
    min_legs: int
    max_legs: int
    style: str


@dataclass
class Ticket:
    name: str
    target: float
    legs: list[ConsensusPick]
    note: str = ""

    @property
    def combined_odds(self) -> float:
        product = 1.0
        for leg in self.legs:
            product *= leg.odds
        return round(product, 2)

    @property
    def confidence(self) -> float:
        if not self.legs:
            return 0.0
        # Independent combination: the product of the leg confidences.
        value = 1.0
        for leg in self.legs:
            value *= max(leg.confidence, 0.05)
        return round(value, 3)


TIERS: tuple[Tier, ...] = (
    Tier("2 Odds", 2.0, 1.65, 2.55, 2, 3, "banker"),
    Tier("5 Odds", 5.0, 3.8, 6.8, 2, 4, "core"),
    Tier("10 Odds", 10.0, 7.5, 14.0, 3, 5, "core"),
    Tier("25+ Odds", 28.0, 20.0, 42.0, 3, 6, "value"),
    Tier("50 Odds", 50.0, 38.0, 72.0, 3, 6, "value"),
    Tier("100+ Odds", 120.0, 90.0, 220.0, 4, 7, "longshot"),
)


def build_tickets(picks: list[ConsensusPick]) -> list[Ticket]:
    """Return one ticket per tier, in ladder order."""
    tickets: list[Ticket] = []
    for tier in TIERS:
        strict_pool = _pool(picks, tier.style, relaxed=False)
        relaxed_pool = _pool(picks, tier.style, relaxed=True)
        legs, relaxed = _search(tier, strict_pool)
        note = ""
        if legs is None:
            legs, _ = _search(tier, relaxed_pool)
            relaxed = legs is not None
            if relaxed:
                note = "Filled out with extra picks because the strict pool was short."
        if legs is None:
            tickets.append(
                Ticket(tier.name, tier.target, [], "Not enough selections for this tier.")
            )
            continue
        if _needs_value(tier) and not any(_is_value(leg) for leg in legs):
            note = (note + " " if note else "") + "No value market was available to add."
        tickets.append(Ticket(tier.name, tier.target, legs, note.strip()))
    return tickets


def _pool(picks: list[ConsensusPick], style: str, relaxed: bool) -> list[ConsensusPick]:
    chosen: list[ConsensusPick] = []
    for pick in picks:
        if pick.odds < 1.05 or pick.odds > 30:
            continue
        if style == "banker":
            if not _is_safe(pick):
                continue
            if pick.odds > (2.15 if relaxed else 1.8):
                continue
            if not relaxed and (not pick.is_consensus or pick.confidence < 0.6):
                continue
            if relaxed and not pick.is_consensus and pick.confidence < 0.55:
                continue
        elif style == "core":
            if pick.market == MARKET_CORRECT_SCORE or pick.market == MARKET_CORNERS:
                continue
            if _is_value(pick) and pick.market == MARKET_OVER_UNDER:
                continue
            if pick.odds > (3.2 if relaxed else 2.6):
                continue
            if not relaxed and not pick.is_consensus:
                continue
        elif style == "value":
            if not (_is_value(pick) or _is_safe(pick) or pick.market == MARKET_1X2):
                continue
            if not relaxed and pick.market == MARKET_CORRECT_SCORE and pick.confidence < 0.35:
                continue
        else:
            if not (_is_value(pick) or pick.odds >= 1.7):
                continue
        chosen.append(pick)
    if style in {"banker", "core"}:
        chosen.sort(key=lambda pick: (pick.odds, -pick.confidence))
        return chosen[:14]
    values = [pick for pick in chosen if _value_rank(pick) >= 1]
    anchors = [pick for pick in chosen if pick.market == MARKET_1X2 and pick.odds <= 2.0]
    values.sort(key=lambda pick: (-_value_rank(pick), -pick.confidence))
    anchors.sort(key=lambda pick: (pick.odds, -pick.confidence))
    return _unique(values[:8] + anchors[:6])[:16]


def _search(tier: Tier, pool: list[ConsensusPick]) -> tuple[list[ConsensusPick] | None, bool]:
    if len(pool) < tier.min_legs:
        return None, False
    require_value = _needs_value(tier)
    min_scores = 0
    if tier.name.startswith("50"):
        min_scores = 1 if sum(1 for pick in pool if pick.market == MARKET_CORRECT_SCORE) else 0
    if tier.name.startswith("100"):
        scores = sum(1 for pick in pool if pick.market == MARKET_CORRECT_SCORE)
        min_scores = 2 if scores >= 2 else (1 if scores == 1 else 0)

    beam: list[tuple[float, frozenset[str], tuple[ConsensusPick, ...]]] = [
        (1.0, frozenset(), tuple())
    ]
    best: tuple[float, tuple[ConsensusPick, ...]] | None = None
    closest: tuple[float, tuple[ConsensusPick, ...]] | None = None

    for _depth in range(tier.max_legs):
        nxt: list[tuple[float, frozenset[str], tuple[ConsensusPick, ...]]] = []
        for product, used, legs in beam:
            for pick in pool:
                if pick.fixture_key in used:
                    continue
                if any(leg.market == pick.market and leg.pick == pick.pick and leg.fixture_key == pick.fixture_key for leg in legs):
                    continue
                new_product = product * pick.odds
                if new_product > tier.high * 1.35 and len(legs) + 1 >= tier.min_legs:
                    continue
                new_legs = legs + (pick,)
                new_used = used | {pick.fixture_key}
                nxt.append((new_product, new_used, new_legs))
                if len(new_legs) < tier.min_legs:
                    continue
                if not _meets(new_legs, require_value, min_scores):
                    continue
                distance = abs(math.log(new_product / tier.target))
                if closest is None or distance < closest[0]:
                    closest = (distance, new_legs)
                if tier.low <= new_product <= tier.high:
                    score = -distance + 0.15 * (sum(leg.confidence for leg in new_legs) / len(new_legs))
                    score += 0.04 * sum(1 for leg in new_legs if leg.is_consensus)
                    if best is None or score > best[0]:
                        best = (score, new_legs)
        nxt.sort(key=lambda item: (-_partial_score(item[2], tier), item[0] if item[0] > tier.high else 0))
        beam = nxt[:36]
        if not beam:
            break

    if best is not None:
        return list(best[1]), False
    if closest is not None and tier.low * 0.75 <= math.prod(leg.odds for leg in closest[1]) <= tier.high * 1.25:
        return list(closest[1]), False
    return None, False


def _meets(legs: tuple[ConsensusPick, ...], require_value: bool, min_scores: int) -> bool:
    if require_value and not any(_is_value(leg) for leg in legs):
        return False
    scores = sum(1 for leg in legs if leg.market == MARKET_CORRECT_SCORE)
    return scores >= min_scores


def _partial_score(legs: tuple[ConsensusPick, ...], tier: Tier) -> float:
    if not legs:
        return 0.0
    product = math.prod(leg.odds for leg in legs)
    closeness = -abs(math.log(max(product, 1.01) / tier.target))
    confidence = sum(leg.confidence for leg in legs) / len(legs)
    return closeness + confidence + 0.2 * sum(1 for leg in legs if _is_value(leg))


def _is_safe(pick: ConsensusPick) -> bool:
    if pick.market == MARKET_1X2:
        return True
    if pick.market == MARKET_OVER_UNDER:
        return (goal_line(pick.pick) or 2.5) <= 2.5
    if pick.market == MARKET_HANDICAP:
        return (goal_line(pick.pick) or 1.0) <= 1.0
    return False


def _is_value(pick: ConsensusPick) -> bool:
    if pick.market in {MARKET_CORRECT_SCORE, MARKET_CORNERS}:
        return True
    if pick.market == MARKET_OVER_UNDER and (goal_line(pick.pick) or 0) >= 3.5:
        return True
    return pick.odds >= 4.5


def _value_rank(pick: ConsensusPick) -> int:
    if pick.market == MARKET_CORRECT_SCORE:
        return 3
    if pick.market == MARKET_CORNERS:
        return 2
    if _is_value(pick):
        return 1
    return 0


def _needs_value(tier: Tier) -> bool:
    return tier.style in {"value", "longshot"}


def _unique(picks: list[ConsensusPick]) -> list[ConsensusPick]:
    seen: set[tuple[str, str, str]] = set()
    ordered: list[ConsensusPick] = []
    for pick in picks:
        key = (pick.fixture_key, pick.market, pick.pick)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(pick)
    return ordered
