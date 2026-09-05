"""Merging rides that a broken recording split in two.

A ride that got stopped and restarted -- a dead battery, an auto-pause that
stuck, a lunch stop long enough that the watch gave up -- lands in Strava as two
activities that are really one outing. On a poster they read as two separate
loops from home, which is wrong twice over: the shape is broken, and the ride is
double-counted in the legend.

Detection needs two tests, not one. "The second starts where the first ended"
is necessary but nowhere near sufficient: a ride that ends at home followed by
another that starts at home also has a tiny junction gap, and merging those two
would be wrong. Observed here, that single test paired a ride *with kids* to a
later *solo* ride purely because both touched home.

The discriminator is where the junction sits relative to the ride's own start.
A split recording resumes out on the route, far from home; two separate outings
meet back at the start. So a merge requires the junction to be both close
between the two activities AND far from where the first one began.

Merges are stored as intent (a list of member ids), never as rewritten data, so
the original streams stay untouched on disk and a merge can always be undone.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .geo import haversine_km
from .store import Store

# A restart after a long café stop is still one ride; a second outing hours later
# is not. Two hours is a generous but still discriminating default.
DEFAULT_MAX_TIME_GAP_MIN = 120.0
# GPS drift at a standstill is tens of metres, and a watch reacquiring a fix
# after a stop can land a few hundred metres off, so allow a little slack.
DEFAULT_MAX_SPACE_GAP_KM = 0.75
# The junction must be genuinely out on the route. Below this it is a
# return-to-home between two separate outings, not a broken recording.
DEFAULT_MIN_JUNCTION_FROM_START_KM = 2.0


class MergeError(RuntimeError):
    """A merge could not be formed from the given activities."""


def merges_path(store: Store) -> Path:
    return store.root / "merges.json"


def load_merges(store: Store) -> list[dict[str, Any]]:
    path = merges_path(store)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_merges(store: Store, merges: list[dict[str, Any]]) -> Path:
    path = merges_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merges, indent=2))
    return path


# ------------------------------------------------------------------ detection


def _start_utc(raw: dict) -> datetime | None:
    """True UTC start. ``start_time`` is genuinely zoned; the ``_local_raw``
    timestamp is a wall clock and would misorder rides across a DST change."""
    value = raw.get("start_time")
    if value:
        try:
            return datetime.fromisoformat(value.replace("+0000", "+00:00"))
        except ValueError:
            pass
    ts = raw.get("start_date_local_raw")
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


def _endpoints(store: Store, activity_id: int | str) -> tuple[list, list] | None:
    if not store.has_streams(activity_id):
        return None
    latlng = (store.load_streams(activity_id).get("latlng")) or []
    return (latlng[0], latlng[-1]) if latlng else None


def suggest(
    store: Store,
    max_time_gap_min: float = DEFAULT_MAX_TIME_GAP_MIN,
    max_space_gap_km: float = DEFAULT_MAX_SPACE_GAP_KM,
    min_junction_from_start_km: float = DEFAULT_MIN_JUNCTION_FROM_START_KM,
    only_ids: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    """Find chains of activities that look like one split recording.

    Returns one entry per chain, each with the evidence that produced it so the
    call can be judged rather than trusted.
    """
    activities = store.load_activities()
    rows = []
    wanted = {int(i) for i in only_ids} if only_ids is not None else None

    for raw in activities.values():
        if wanted is not None and int(raw["id"]) not in wanted:
            continue
        start = _start_utc(raw)
        ends = _endpoints(store, raw["id"])
        if start is None or ends is None:
            continue
        rows.append(
            {
                "id": int(raw["id"]),
                "raw": raw,
                "start": start,
                "elapsed": float(raw.get("elapsed_time_raw") or 0),
                "first_pt": ends[0],
                "last_pt": ends[1],
            }
        )

    rows.sort(key=lambda r: r["start"])

    # Link each activity to at most one successor, then read off the chains.
    successor: dict[int, dict] = {}
    for i, a in enumerate(rows):
        a_end = a["start"].timestamp() + a["elapsed"]
        for b in rows[i + 1:]:
            time_gap_min = (b["start"].timestamp() - a_end) / 60.0
            if time_gap_min > max_time_gap_min:
                break  # rows are sorted, so every later b is further away
            space_gap_km = haversine_km(a["last_pt"], b["first_pt"])
            # How far out on the route the two activities meet. Near zero means
            # they meet at home, i.e. two separate outings rather than a split.
            junction_km = haversine_km(a["first_pt"], b["first_pt"])
            if (
                time_gap_min >= -5.0
                and space_gap_km <= max_space_gap_km
                and junction_km >= min_junction_from_start_km
            ):
                successor[a["id"]] = {
                    "next": b["id"],
                    "time_gap_min": round(time_gap_min, 1),
                    "space_gap_km": round(space_gap_km, 3),
                    "junction_km": round(junction_km, 2),
                }
                break

    by_id = {r["id"]: r for r in rows}
    followed = {link["next"] for link in successor.values()}
    chains: list[dict[str, Any]] = []
    for row in rows:
        if row["id"] in followed or row["id"] not in successor:
            continue  # not a chain head, or a chain of one
        members, evidence, cursor = [row["id"]], [], row["id"]
        while cursor in successor:
            link = successor[cursor]
            evidence.append(
                {
                    "from": cursor,
                    "to": link["next"],
                    "time_gap_min": link["time_gap_min"],
                    "space_gap_km": link["space_gap_km"],
                    "junction_km": link["junction_km"],
                }
            )
            cursor = link["next"]
            members.append(cursor)
        chains.append(
            {
                "members": members,
                "evidence": evidence,
                "names": [by_id[m]["raw"].get("name", "") for m in members],
                "date": row["start"].strftime("%Y-%m-%d"),
                "total_km": round(
                    sum((by_id[m]["raw"].get("distance_raw") or 0) for m in members) / 1000.0, 1
                ),
            }
        )
    return chains


# -------------------------------------------------------------------- merging


def merged_streams(store: Store, members: list[int]) -> dict[str, list]:
    """Concatenate member streams into one, re-basing distance and time.

    ``distance`` and ``time`` are cumulative *within* an activity, so they must
    be re-accumulated rather than concatenated. The straight-line hop across each
    recording gap is added to the distance: the rider did cover it, and dropping
    it would leave the total short.
    """
    out: dict[str, list] = {"latlng": [], "distance": [], "altitude": [], "time": []}
    dist_offset = 0.0
    time_offset = 0.0
    activities = store.load_activities()
    prev_last_pt: list | None = None
    prev_end_utc: float | None = None

    for member in members:
        streams = store.load_streams(member)
        latlng = streams.get("latlng") or []
        if not latlng:
            continue
        distance = streams.get("distance") or [0.0] * len(latlng)
        altitude = streams.get("altitude") or [0.0] * len(latlng)
        times = streams.get("time") or list(range(len(latlng)))

        raw = activities.get(str(member), {})
        start_utc = _start_utc(raw)
        start_ts = start_utc.timestamp() if start_utc else None

        if prev_last_pt is not None:
            dist_offset += haversine_km(prev_last_pt, latlng[0]) * 1000.0
            # Prefer the real clock gap; fall back to a nominal minute if a
            # member has no usable start time.
            if start_ts is not None and prev_end_utc is not None:
                time_offset += max(0.0, start_ts - prev_end_utc)
            else:
                time_offset += 60.0

        for i, point in enumerate(latlng):
            out["latlng"].append(point)
            out["distance"].append(round(dist_offset + (distance[i] if i < len(distance) else 0), 1))
            out["altitude"].append(altitude[i] if i < len(altitude) else 0)
            out["time"].append(round(time_offset + (times[i] if i < len(times) else i)))

        dist_offset = out["distance"][-1]
        time_offset = out["time"][-1]
        prev_last_pt = latlng[-1]
        prev_end_utc = (start_ts + times[-1]) if start_ts is not None else None

    return out


def merged_activity(store: Store, members: list[int], name: str | None = None) -> dict[str, Any]:
    """Build a synthetic activity summary standing in for the member rides."""
    activities = store.load_activities()
    parts = [activities[str(m)] for m in members if str(m) in activities]
    if not parts:
        raise MergeError(f"none of {members} are in the activity index")

    streams = merged_streams(store, members)
    tags: dict[str, bool] = {}
    for part in parts:
        for key, value in (part.get("tags") or {}).items():
            tags[key] = bool(tags.get(key)) or bool(value)

    head = parts[0]
    total_distance = streams["distance"][-1] if streams["distance"] else 0.0
    return {
        **head,
        "id": head["id"],
        "id_str": str(head["id"]),
        "name": name or head.get("name") or "(merged ride)",
        "distance_raw": round(total_distance, 1),
        "distance": f"{total_distance / 1000:.2f}",
        "elevation_gain_raw": sum((p.get("elevation_gain_raw") or 0) for p in parts),
        "moving_time_raw": sum((p.get("moving_time_raw") or 0) for p in parts),
        "elapsed_time_raw": streams["time"][-1] if streams["time"] else 0,
        "tags": tags,
        "has_latlng": True,
        "merged_from": members,
    }


def apply_to(store: Store, activities: dict[str, dict]) -> tuple[dict[str, dict], dict[int, dict]]:
    """Fold saved merges into an activity index.

    Returns the rewritten index plus the merged streams, keyed by the surviving
    activity id, so callers can use them without touching the files on disk.
    """
    merges = load_merges(store)
    if not merges:
        return activities, {}

    result = dict(activities)
    streams: dict[int, dict] = {}
    for merge in merges:
        members = [int(m) for m in merge["members"]]
        present = [m for m in members if str(m) in result]
        if len(present) < 2:
            continue
        summary = merged_activity(store, present, merge.get("name"))
        for member in present:
            result.pop(str(member), None)
        result[str(summary["id"])] = summary
        streams[int(summary["id"])] = merged_streams(store, present)
    return result, streams
