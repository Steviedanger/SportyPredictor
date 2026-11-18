"""Format the matchday slip and deliver it through the Telegram Bot API."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import requests

from aggregator import AggregateReport, ConsensusPick
from builder import Ticket
from markets import MARKET_LABELS

log = logging.getLogger("sporty.notifier")

_CHUNK = 3500


def notify(
    report: AggregateReport,
    tickets: list[Ticket],
    *,
    generated_at: datetime,
    first_kickoff: Optional[datetime],
    dry_run: bool = False,
) -> list[str]:
    """Print the slip and, unless this is a dry run, send it to Telegram."""
    messages = format_messages(report, tickets, generated_at, first_kickoff)
    for message in messages:
        print(message)
        print()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if dry_run:
        log.info("Dry run: Telegram was not called.")
        return messages
    if not token or not chat_id:
        log.warning(
            "Telegram is not configured. Copy .env.example to .env and set "
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        )
        return messages
    _send(token, chat_id, messages)
    return messages


def format_messages(
    report: AggregateReport,
    tickets: list[Ticket],
    generated_at: datetime,
    first_kickoff: Optional[datetime],
) -> list[str]:
    """Split the slip into Telegram-sized plain-text messages."""
    blocks = [_header(report, generated_at, first_kickoff), _sources(report)]
    shown, hidden = _select_picks(report.picks, tickets)
    blocks.append(_picks_block("CONSENSUS", [pick for pick in shown if pick.is_consensus]))
    blocks.append(_picks_block("FALLBACK", [pick for pick in shown if not pick.is_consensus]))
    if hidden:
        blocks.append(f"{hidden} more fixture picks were kept out of the message.")
    blocks.extend(_ticket_block(ticket) for ticket in tickets)
    blocks.append(
        "Informational only. A consensus is an overlap between tip sites, not a promise of a result. 18+."
    )
    return _pack(blocks)


def _header(
    report: AggregateReport,
    generated_at: datetime,
    first_kickoff: Optional[datetime],
) -> str:
    kick = first_kickoff.strftime("%H:%M") if first_kickoff else "unknown"
    return "\n".join(
        [
            "SPORTYPREDICTOR",
            generated_at.strftime("%A %d %b %Y %H:%M"),
            f"Earliest kickoff: {kick}",
            "Single daily run, scheduled 1 hour before that kickoff.",
            f"Consensus picks: {len(report.consensus)}    Fallbacks: {len(report.fallbacks)}",
        ]
    )


def _sources(report: AggregateReport) -> str:
    lines = ["SOURCES"]
    names = sorted(set(report.source_counts) | set(report.source_errors))
    for name in names:
        count = report.source_counts.get(name, 0)
        error = report.source_errors.get(name)
        if error:
            lines.append(f"  {name}: unavailable ({_short_error(error)})")
        else:
            lines.append(f"  {name}: {count} tips")
    return "\n".join(lines)


def _picks_block(title: str, picks: list[ConsensusPick]) -> str:
    if not picks:
        return f"{title}\n  None today."
    lines = [title]
    current = None
    for pick in picks:
        if pick.fixture_key != current:
            current = pick.fixture_key
            when = pick.kickoff.strftime("%H:%M") if pick.kickoff else "time tbc"
            league = f"  {pick.league}" if pick.league else ""
            lines.append(f"  {pick.fixture}  {when}{league}")
        sources = ", ".join(pick.sources)
        lines.append(
            f"    {MARKET_LABELS.get(pick.market, pick.market)}: {pick.label}"
            f"   {pick.support} source{'s' if pick.support != 1 else ''}"
            f" ({sources})   {pick.odds:.2f}"
        )
    return "\n".join(lines)


def _ticket_block(ticket: Ticket) -> str:
    lines = [f"{ticket.name.upper()}   target {ticket.target:g}"]
    if not ticket.legs:
        lines.append(f"  {ticket.note or 'No ticket.'}")
        return "\n".join(lines)
    for index, leg in enumerate(ticket.legs, start=1):
        flag = "consensus" if leg.is_consensus else "fallback"
        when = leg.kickoff.strftime("%H:%M") if leg.kickoff else "--:--"
        lines.append(
            f"  {index}. {leg.fixture} ({when})"
        )
        lines.append(
            f"     {leg.label} @ {leg.odds:.2f}   {flag}   {leg.confidence:.0%}"
        )
    lines.append(f"  Combined odds: {ticket.combined_odds:.2f}")
    if ticket.note:
        lines.append(f"  Note: {ticket.note}")
    return "\n".join(lines)


def _select_picks(
    picks: list[ConsensusPick],
    tickets: list[Ticket],
    limit: int = 18,
) -> tuple[list[ConsensusPick], int]:
    used = {leg.fixture_key for ticket in tickets for leg in ticket.legs}
    ranked_keys: list[str] = []
    for pick in sorted(picks, key=lambda item: (item.fixture_key not in used, -item.confidence)):
        if pick.fixture_key not in ranked_keys:
            ranked_keys.append(pick.fixture_key)
    keep = set(ranked_keys[:limit]) | used
    shown = [pick for pick in picks if pick.fixture_key in keep]
    hidden_fixtures = len(set(ranked_keys) - keep)
    return shown, hidden_fixtures


def _pack(blocks: list[str]) -> list[str]:
    messages: list[str] = []
    current = ""
    for block in blocks:
        piece = block.strip("\n")
        if not piece:
            continue
        candidate = piece if not current else current + "\n\n" + piece
        if len(candidate) <= _CHUNK:
            current = candidate
            continue
        if current:
            messages.append(current)
        if len(piece) <= _CHUNK:
            current = piece
            continue
        for start in range(0, len(piece), _CHUNK):
            messages.append(piece[start : start + _CHUNK])
        current = ""
    if current:
        messages.append(current)
    return messages


def _send(token: str, chat_id: str, messages: list[str]) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for message in messages:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if not response.ok:
            detail = response.text[:300]
            log.error("Telegram HTTP %s: %s", response.status_code, detail)
            response.raise_for_status()
    log.info("Sent %s Telegram message(s).", len(messages))


def _short_error(error: str) -> str:
    text = " ".join(error.split())
    return text[:140]
