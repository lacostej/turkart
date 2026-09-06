"""Counting the requests we make.

Strava's documented rate limits are per application: 100 requests / 15 min and
1,000 / day for non-upload calls, which is all turkart makes. The internal
endpoints we use return **no** ``X-RateLimit-*`` headers -- those exist only on
the official API -- so nothing tells us how much budget is left. The only way to
know is to count our own requests.

The tally is local and advisory. It is not a quota we enforce; it is there so the
cost of a command is visible before it becomes a problem, and so the effect of
fetching lazily can be seen rather than assumed.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .store import Store

# Strava's non-upload budget for the default tier. turkart only ever reads.
DAILY_BUDGET = 1000
QUARTER_HOUR_BUDGET = 100

# Keep a fortnight; this is a running check, not an audit trail.
KEEP_DAYS = 14


def usage_path(store: Store) -> Path:
    return store.root / "usage.json"


def load(store: Store) -> dict[str, int]:
    path = usage_path(store)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {}


def record(store: Store, requests: int, day: str | None = None) -> int:
    """Add to today's tally and return the new total."""
    if requests <= 0:
        return today(store)
    day = day or date.today().isoformat()
    tally = load(store)
    tally[day] = tally.get(day, 0) + requests

    for old in sorted(tally)[:-KEEP_DAYS]:
        del tally[old]

    path = usage_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tally, indent=2, sort_keys=True))
    return tally[day]


def today(store: Store, day: str | None = None) -> int:
    return load(store).get(day or date.today().isoformat(), 0)


def report(store: Store, made: int) -> str:
    """One line on what a command cost and what remains of the daily budget."""
    used = today(store)
    left = max(0, DAILY_BUDGET - used)
    line = f"{made} request(s) this run; {used} today"
    if used:
        line += f", ~{left} left of Strava's {DAILY_BUDGET}/day non-upload budget"
    if used >= DAILY_BUDGET:
        line += " -- over budget, expect failures"
    elif used > DAILY_BUDGET * 0.8:
        line += " -- close to the limit"
    return line
