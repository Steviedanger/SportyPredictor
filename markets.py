"""Market types and extraction for football tips.

Selections are normalised into five markets: 1X2, correct score, over/under
goals, handicap, and corners. Site parsers hand raw text here so the
consensus engine can compare like with like.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

MARKET_1X2 = "1x2"
MARKET_CORRECT_SCORE = "correct_score"
MARKET_OVER_UNDER = "over_under"
MARKET_HANDICAP = "handicap"
MARKET_CORNERS = "corners"

MARKETS = (
    MARKET_1X2,
    MARKET_CORRECT_SCORE,
    MARKET_OVER_UNDER,
    MARKET_HANDICAP,
    MARKET_CORNERS,
)

MARKET_LABELS = {
    MARKET_1X2: "1X2",
    MARKET_CORRECT_SCORE: "Correct score",
    MARKET_OVER_UNDER: "Over/Under",
    MARKET_HANDICAP: "Handicap",
    MARKET_CORNERS: "Corners",
}

_CLUB_TOKENS = re.compile(
    r"\b(fc|afc|sc|cf|ac|sk|fk|club|the)\b",
    re.IGNORECASE,
)
_GENERIC_TOKENS = {
    "united",
    "city",
    "town",
    "real",
    "athletic",
    "sporting",
    "hotspur",
    "wanderers",
    "rovers",
    "county",
    "albion",
    "rangers",
    "olympic",
    "and",
}
_SPACE = re.compile(r"\s+")
_SCORE = re.compile(r"\b(\d{1,2})\s*[-:]\s*(\d{1,2})\b")
_ODDS = re.compile(
    r"(?:odds?|coef\.?|price|@)\s*:?\s*([+-]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_DECIMAL = re.compile(r"\b(\d{1,2}\.\d{2})\b")
_TIME_ONLY = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")
_DATE_DMY = re.compile(
    r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b"
)
_DATE_DM = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
_NOISE = re.compile(
    r"\b(cookie|privacy|subscribe|login|sign up|copyright|whatsapp|javascript)\b",
    re.IGNORECASE,
)
_DATE_TEXT = re.compile(
    r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_TO_WIN = re.compile(
    r"^(?P<team>.+?)\s+to\s+win(?:\s+(?P<place>at|vs\.?|v))?\s+(?P<opp>.+)$",
    re.IGNORECASE,
)
_LINE_VS = re.compile(
    r"^(?P<home>.+?)\s+(?:vs\.?|v)\s+(?P<away>.+?)$",
    re.IGNORECASE,
)
_SCORE_LINE = re.compile(
    r"^(?:(?P<label>draw|home|away)\s+)?(?P<h>\d{1,2})\s*[-:]\s*(?P<a>\d{1,2})"
    r"(?:\s+(?P<home>.+?)\s+(?:vs\.?|v)\s+(?P<away>.+))?$",
    re.IGNORECASE,
)
_OU = re.compile(
    r"\b(?P<side>over|under)\s+(?P<line>\d+(?:\.\d+)?)(?:\s+goals?)?\b",
    re.IGNORECASE,
)
_CORNERS = re.compile(
    r"\b(?P<side>over|under)\s+(?P<line>\d+(?:\.\d+)?)\s+corners?\b",
    re.IGNORECASE,
)
_HANDICAP = re.compile(
    r"\b(?:(?P<team>[A-Za-z][\w.'’\- ]{1,40}?)\s+)?"
    r"(?P<line>[+-]\d+(?:\.\d+)?)\s*(?:asian\s+)?handicap\b",
    re.IGNORECASE,
)
_RESULT_WORD = re.compile(
    r"\b(?P<word>home(?:\s+win)?|away(?:\s+win)?|draw|home\s+or\s+draw|away\s+or\s+draw)\b",
    re.IGNORECASE,
)


@dataclass
class Selection:
    """One tip from one source for one fixture and market."""

    source: str
    home: str
    away: str
    market: str
    pick: str
    kickoff: Optional[datetime] = None
    league: Optional[str] = None
    confidence: Optional[float] = None
    odds: Optional[float] = None
    note: str = ""

    @property
    def fixture(self) -> str:
        return f"{self.home} vs {self.away}"


def normalize_team(name: str) -> str:
    """Fold a club name into a comparable token string."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ").replace("'", "")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = _CLUB_TOKENS.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def teams_match(left: str, right: str) -> bool:
    """True when two normalised names clearly refer to the same club."""
    if not left or not right:
        return False
    if left == right:
        return True
    left_parts = left.split()
    right_parts = right.split()
    if not left_parts or not right_parts:
        return False
    if set(left_parts) <= set(right_parts) or set(right_parts) <= set(left_parts):
        return True

    def distinctive(parts: list[str]) -> list[str]:
        tokens = [part for part in parts if part not in _GENERIC_TOKENS and len(part) >= 4]
        return tokens or parts

    return distinctive(left_parts) == distinctive(right_parts)


def parse_kickoff(text: str, today: Optional[date] = None) -> Optional[datetime]:
    """Parse a kickoff from a fragment of page text. Naive local time."""
    if not text:
        return None
    today = today or date.today()
    year = today.year
    month = today.month
    day = today.day
    found_date = False

    iso = _ISO_DATE.search(text)
    named = _DATE_TEXT.search(text)
    numeric = _DATE_DMY.search(text)
    if iso:
        year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        found_date = True
    elif named:
        day = int(named.group(1))
        month = _MONTHS[named.group(2)[:3].lower()]
        year = int(named.group(3))
        found_date = True
    elif numeric:
        day, month = int(numeric.group(1)), int(numeric.group(2))
        year = int(numeric.group(3))
        if year < 100:
            year += 2000
        found_date = True
    else:
        short = _DATE_DM.search(text)
        if short:
            day, month = int(short.group(1)), int(short.group(2))
            found_date = True

    clock = _TIME_ONLY.search(text)
    if not clock and not found_date:
        return None
    hour, minute = (int(clock.group(1)), int(clock.group(2))) if clock else (15, 0)
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def sane_odds(value: Optional[float], market: str) -> Optional[float]:
    """Keep decimal prices, converting American prices when they are obvious."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number >= 100 or number <= -100:
        if number > 0:
            number = number / 100.0 + 1.0
        else:
            number = 100.0 / abs(number) + 1.0
    cap = 51.0 if market == MARKET_CORRECT_SCORE else 21.0
    if market == MARKET_CORNERS:
        cap = 8.0
    if 1.01 <= number <= cap:
        return round(number, 2)
    return None


def estimate_odds(market: str, confidence: Optional[float], pick: str = "") -> float:
    """Fill a decimal price when a source did not publish one."""
    confidence = 0.55 if confidence is None else min(max(confidence, 0.2), 0.88)
    if market == MARKET_CORRECT_SCORE:
        price = 7.5 / max(confidence, 0.35)
        return round(min(max(price, 6.0), 15.0), 2)
    if market == MARKET_CORNERS:
        price = 1.05 / confidence
        return round(min(max(price, 1.55), 3.4), 2)
    if market == MARKET_OVER_UNDER:
        line = _line_value(pick)
        base = 1.02 / confidence
        if line is not None and line >= 3.5:
            base += 0.55
        return round(min(max(base, 1.35), 3.6), 2)
    if market == MARKET_HANDICAP:
        price = 1.08 / confidence
        return round(min(max(price, 1.45), 3.2), 2)
    price = 0.96 / confidence
    return round(min(max(price, 1.18), 4.2), 2)


def canonical_pick(market: str, pick: str, home: str, away: str) -> Optional[str]:
    """Map a raw pick onto home/away/draw, a score, or a line."""
    raw = _SPACE.sub(" ", (pick or "").strip())
    if not raw:
        return None
    lowered = raw.lower()
    home_n = normalize_team(home)
    away_n = normalize_team(away)

    if market == MARKET_1X2:
        if lowered in {"1", "home", "home win", "h"} or _names_team(lowered, home_n, "win"):
            return "home"
        if lowered in {"2", "away", "away win", "a"} or _names_team(lowered, away_n, "win"):
            return "away"
        if lowered in {"x", "draw", "d"}:
            return "draw"
        if "home or draw" in lowered or lowered in {"1x"}:
            return None
        if "away or draw" in lowered or lowered in {"x2"}:
            return None
        team = _team_side(lowered.replace(" to win", ""), home_n, away_n)
        if team:
            return team
        return None

    if market == MARKET_CORRECT_SCORE:
        match = _SCORE.search(lowered)
        if not match:
            return None
        return f"{int(match.group(1))}-{int(match.group(2))}"

    if market == MARKET_OVER_UNDER:
        match = _OU.search(lowered)
        if not match:
            return None
        return f"{match.group('side').lower()} {_format_line(match.group('line'))}"

    if market == MARKET_CORNERS:
        match = _CORNERS.search(lowered) or _OU.search(lowered)
        if not match:
            return None
        return f"{match.group('side').lower()} {_format_line(match.group('line'))}"

    if market == MARKET_HANDICAP:
        match = re.search(
            r"\b(?P<team>[A-Za-z][\w.'’\- ]{1,40}?)\s+(?P<line>[+-]\d+(?:\.\d+)?)"
            r"\s*(?:asian\s+)?handicap\b",
            lowered,
        ) or _HANDICAP.search(lowered)
        if not match:
            return None
        line = _format_line(match.group("line"), signed=True)
        team = (match.group("team") or "").strip()
        side = _team_side(team, home_n, away_n) if team else None
        token = team.lower()
        if side not in {"home", "away"}:
            if token in {"home", "1"} or token.startswith("home"):
                side = "home"
            elif token in {"away", "2"} or token.startswith("away"):
                side = "away"
        if side not in {"home", "away"}:
            return None
        return f"{side} {line}"
    return None


def display_pick(market: str, pick: str, home: str, away: str) -> str:
    """Human label for a canonical pick."""
    if market == MARKET_1X2:
        if pick == "home":
            return f"{home} to win"
        if pick == "away":
            return f"{away} to win"
        return "Draw"
    if market == MARKET_CORRECT_SCORE:
        return f"Correct score {pick}"
    if market == MARKET_OVER_UNDER:
        side, line = pick.split()
        return f"{side.title()} {line} goals"
    if market == MARKET_HANDICAP:
        side, line = pick.split()
        team = home if side == "home" else away
        return f"{team} {line} handicap"
    if market == MARKET_CORNERS:
        side, line = pick.split()
        return f"{side.title()} {line} corners"
    return pick


def flip_pick(market: str, pick: str) -> str:
    """Re-express a pick after swapping home and away."""
    if market == MARKET_1X2:
        return {"home": "away", "away": "home", "draw": "draw"}.get(pick, pick)
    if market == MARKET_CORRECT_SCORE and "-" in pick:
        left, right = pick.split("-", 1)
        return f"{right}-{left}"
    parts = pick.split()
    if parts and parts[0] in {"home", "away"}:
        parts[0] = "away" if parts[0] == "home" else "home"
        return " ".join(parts)
    return pick


def goal_line(pick: str) -> Optional[float]:
    return _line_value(pick)


def selections_from_prose(
    text: str,
    source: str,
    league: Optional[str] = None,
    default_confidence: float = 0.58,
    today: Optional[date] = None,
) -> list[Selection]:
    """Pull structured tips out of article or card text."""
    found: list[Selection] = []
    pending_time: Optional[str] = None
    lines = [line.strip(" -\t") for line in text.splitlines()]
    lines = [line for line in lines if line and not _NOISE.search(line)]
    for index, line in enumerate(lines):
        if re.fullmatch(r"([01]?\d|2[0-3])[:.][0-5]\d", line):
            pending_time = line
            continue
        window = "\n".join(lines[index : index + 4])
        kickoff = parse_kickoff(window, today=today)
        if kickoff is None and pending_time:
            kickoff = parse_kickoff(pending_time, today=today)
        odds = _first_odds(window)
        built = _selections_from_line(
            line,
            source=source,
            league=league,
            kickoff=kickoff,
            odds=odds,
            confidence=default_confidence,
        )
        if built:
            found.extend(built)
            pending_time = None
    return dedupe_selections(found)


def dedupe_selections(items: Iterable[Selection]) -> list[Selection]:
    """Keep the strongest copy of the same source/fixture/market/pick."""
    best: dict[tuple, Selection] = {}
    for item in items:
        key = (
            item.source,
            normalize_team(item.home),
            normalize_team(item.away),
            item.market,
            item.pick,
        )
        current = best.get(key)
        if current is None or (item.confidence or 0) > (current.confidence or 0):
            best[key] = item
    return list(best.values())


def _selections_from_line(
    line: str,
    source: str,
    league: Optional[str],
    kickoff: Optional[datetime],
    odds: Optional[float],
    confidence: float,
) -> list[Selection]:
    results: list[Selection] = []
    win = _TO_WIN.match(line)
    if win and "both teams" not in line.lower():
        team = _clean_side(win.group("team"))
        opp = _clean_side(win.group("opp"))
        place = (win.group("place") or "vs").lower()
        if place.startswith("at"):
            home, away, pick = opp, team, "away"
        else:
            home, away, pick = team, opp, "home"
        if home and away:
            results.append(
                _make(
                    source, home, away, MARKET_1X2, pick,
                    kickoff, league, confidence, odds,
                )
            )

    corners = _CORNERS.search(line)
    fixture = _fixture_in_line(line)
    if corners and fixture:
        home, away = fixture
        pick = canonical_pick(MARKET_CORNERS, corners.group(0), home, away)
        if pick:
            results.append(
                _make(
                    source, home, away, MARKET_CORNERS, pick,
                    kickoff, league, confidence, odds,
                )
            )

    handicap = _HANDICAP.search(line)
    if handicap and fixture and "corner" not in line.lower() and not corners:
        home, away = fixture
        raw = line
        pick = canonical_pick(MARKET_HANDICAP, raw, home, away)
        if pick:
            results.append(
                _make(
                    source, home, away, MARKET_HANDICAP, pick,
                    kickoff, league, max(confidence - 0.04, 0.45), odds,
                )
            )

    if (
        not corners
        and re.search(r"\b(over|under)\b", line, re.IGNORECASE)
        and "corner" not in line.lower()
    ):
        ou = _OU.search(line)
        if ou and fixture:
            home, away = fixture
            pick = canonical_pick(MARKET_OVER_UNDER, ou.group(0), home, away)
            if pick:
                results.append(
                    _make(
                        source, home, away, MARKET_OVER_UNDER, pick,
                        kickoff, league, confidence, odds,
                    )
                )

    named_score = re.match(
        r"^(?P<home>[A-Za-z][\w.'’ ]{1,40}?)\s+(?P<h>\d{1,2})\s*[-:]\s*(?P<a>\d{1,2})"
        r"\s+(?P<home2>[A-Za-z][\w.'’ ]{1,40}?)\s+(?:vs\.?|v)\s+(?P<away>.+)$",
        line,
        re.IGNORECASE,
    )
    if (
        named_score
        and len(line) <= 120
        and teams_match(
            normalize_team(named_score.group("home")),
            normalize_team(named_score.group("home2")),
        )
    ):
        results.append(
            _make(
                source,
                _clean_side(named_score.group("home")),
                _clean_side(named_score.group("away")),
                MARKET_CORRECT_SCORE,
                f"{int(named_score.group('h'))}-{int(named_score.group('a'))}",
                kickoff,
                league,
                max(confidence - 0.06, 0.42),
                odds,
            )
        )

    score = _SCORE_LINE.match(line)
    if score and len(line) <= 120 and (score.group("home") or fixture) and not named_score:
        if score.group("home"):
            home = _clean_side(score.group("home"))
            away = _clean_side(score.group("away") or "")
        else:
            home, away = fixture or ("", "")
        if home and away:
            pick = f"{int(score.group('h'))}-{int(score.group('a'))}"
            results.append(
                _make(
                    source, home, away, MARKET_CORRECT_SCORE, pick,
                    kickoff, league, max(confidence - 0.06, 0.42), odds,
                )
            )

    if not results and fixture and re.search(r"\b(win|draw)\b", line, re.IGNORECASE):
        home, away = fixture
        word = _RESULT_WORD.search(line)
        if word:
            token = word.group("word").lower()
            mapped = {
                "home": "home",
                "home win": "home",
                "away": "away",
                "away win": "away",
                "draw": "draw",
            }.get(token)
            if mapped:
                results.append(
                    _make(
                        source, home, away, MARKET_1X2, mapped,
                        kickoff, league, confidence, odds,
                    )
                )
    return results


def _make(
    source: str,
    home: str,
    away: str,
    market: str,
    pick: str,
    kickoff: Optional[datetime],
    league: Optional[str],
    confidence: float,
    odds: Optional[float],
) -> Selection:
    canonical = canonical_pick(market, pick, home, away) or pick
    return Selection(
        source=source,
        home=home,
        away=away,
        market=market,
        pick=canonical,
        kickoff=kickoff,
        league=league,
        confidence=round(confidence, 3),
        odds=sane_odds(odds, market),
    )


def _fixture_in_line(line: str) -> Optional[tuple[str, str]]:
    cleaned = re.sub(
        r"\b(?:over|under)\s+\d+(?:\.\d+)?\s*corners?\b",
        " ",
        line,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:over|under)\s+\d+(?:\.\d+)?(?:\s+goals?)?\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"[+-]\d+(?:\.\d+)?\s*(?:asian\s+)?handicap",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bcorrect score\b", " ", cleaned, flags=re.IGNORECASE)
    match = re.search(
        r"(?P<home>[A-Za-z][\w.'’\- ]{1,42}?)\s+(?:vs\.?|v)\s+(?P<away>[A-Za-z][\w.'’\- ]{1,42})",
        cleaned,
    )
    if not match:
        return None
    home = _clean_side(match.group("home"))
    away = _clean_side(match.group("away"))
    if len(home) < 2 or len(away) < 2:
        return None
    return home, away


def _clean_side(value: str) -> str:
    value = re.split(r"\s+[+-]\d", value, maxsplit=1)[0]
    value = re.sub(
        r"\b(correct score|prediction|tip|odds?|over|under|handicap|corners?|draw)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = value.strip(" .:-|")
    value = re.sub(r"\s+\d+(?:\.\d+)?$", "", value)
    return _SPACE.sub(" ", value).strip(" .:-|")


def _first_odds(text: str) -> Optional[float]:
    labelled = _ODDS.search(text)
    if labelled:
        try:
            return float(labelled.group(1))
        except ValueError:
            return None
    for match in _DECIMAL.finditer(text):
        value = float(match.group(1))
        if value >= 1.2:
            return value
    return None


def _names_team(text: str, team: str, suffix: str) -> bool:
    if not team:
        return False
    stripped = text.replace(f" to {suffix}", "").strip()
    return teams_match(normalize_team(stripped), team)


def _team_side(text: str, home: str, away: str) -> Optional[str]:
    norm = normalize_team(text)
    home_hit = teams_match(norm, home) or (home and home in norm)
    away_hit = teams_match(norm, away) or (away and away in norm)
    if home_hit and not away_hit:
        return "home"
    if away_hit and not home_hit:
        return "away"
    return None


def _format_line(value: str, signed: bool = False) -> str:
    number = float(value)
    if signed:
        text = f"{number:+.1f}" if not number.is_integer() else f"{int(number):+d}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
            if text[-1] in "+-" :
                text += "0"
        return text
    if float(number).is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _line_value(pick: str) -> Optional[float]:
    match = re.search(r"([+-]?\d+(?:\.\d+)?)", pick or "")
    if not match:
        return None
    try:
        return abs(float(match.group(1)))
    except ValueError:
        return None


def default_kickoff(day: date, clock: str = "15:00") -> datetime:
    hour, minute = (15, 0)
    match = _TIME_ONLY.search(clock or "")
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
    return datetime.combine(day, time(hour, minute))


def aware(moment: datetime) -> datetime:
    """Attach the local timezone when a kickoff was parsed as naive."""
    if moment.tzinfo is not None:
        return moment
    return moment.astimezone()


def earliest_upcoming(
    moments: Iterable[Optional[datetime]],
    now: datetime,
) -> Optional[datetime]:
    """Soonest kickoff that has not already started."""
    upcoming: list[datetime] = []
    for moment in moments:
        if moment is None:
            continue
        stamped = aware(moment)
        if stamped > now - timedelta(minutes=1):
            upcoming.append(stamped)
    if not upcoming:
        return None
    return min(upcoming)
