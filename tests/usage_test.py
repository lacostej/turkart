"""Checks for the request tally.

Strava's internal endpoints send no rate-limit headers, so this count is the only
signal we have about the per-application budget. If it is wrong, it is worse than
having none -- it would be wrong confidently.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from turkart import usage
from turkart.store import Store

passed = failed = 0


def ok(label, cond):
    global passed, failed
    print(("PASS  " if cond else "FAIL  ") + label)
    if cond:
        passed += 1
    else:
        failed += 1


with tempfile.TemporaryDirectory() as tmp:
    store = Store(Path(tmp))

    ok("an empty tally reads as zero", usage.today(store) == 0)
    ok("no file is written for zero requests",
       usage.record(store, 0) == 0 and not usage.usage_path(store).exists())

    usage.record(store, 5)
    ok("requests are recorded", usage.today(store) == 5)
    usage.record(store, 3)
    ok("requests accumulate rather than replace", usage.today(store) == 8)

    ok("the report names the budget", str(usage.DAILY_BUDGET) in usage.report(store, 3))
    ok("the report shows what is left", "992" in usage.report(store, 3))

    # Days are separate, and old ones are pruned.
    for day in range(1, 21):
        usage.record(store, 1, day=f"2026-01-{day:02d}")
    tally = usage.load(store)
    ok("old days are pruned", len(tally) <= usage.KEEP_DAYS + 1)
    ok("today survives pruning", usage.today(store) == 8)

    usage.record(store, usage.DAILY_BUDGET)
    ok("going over budget is said plainly", "over budget" in usage.report(store, 0))

    # A corrupt file must not take a fetch down.
    usage.usage_path(store).write_text("{not json")
    ok("unreadable tally degrades to empty", usage.load(store) == {})
    usage.record(store, 2)
    ok("and recovers on the next write", usage.today(store) == 2)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
