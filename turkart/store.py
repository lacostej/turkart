"""On-disk cache for fetched Strava data.

Layout under ``data/``::

    activities.json      index of ride summaries, keyed by activity id
    streams/<id>.json    raw stream payload for one activity

Fetching is the slow, rate-limited, credential-dependent step, so everything
lands on disk as raw payloads and every later stage (selection, cleanup,
rendering) reads only from here. Re-running a fetch is then cheap and offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path("data")


class Store:
    def __init__(self, root: Path = DATA_DIR):
        self.root = root
        self.activities_file = root / "activities.json"
        self.streams_dir = root / "streams"

    # -------------------------------------------------------------- activities

    def load_activities(self) -> dict[str, dict[str, Any]]:
        if not self.activities_file.exists():
            return {}
        return json.loads(self.activities_file.read_text())

    def save_activities(self, activities: dict[str, dict[str, Any]]) -> None:
        self.activities_file.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self.activities_file, activities)

    def merge_activities(self, new: dict[str, dict[str, Any]]) -> tuple[int, int]:
        """Merge fetched summaries into the index. Returns (added, updated)."""
        existing = self.load_activities()
        added = sum(1 for key in new if key not in existing)
        updated = sum(1 for key, value in new.items() if key in existing and existing[key] != value)
        existing.update(new)
        self.save_activities(existing)
        return added, updated

    # ----------------------------------------------------------------- streams

    def stream_path(self, activity_id: int | str) -> Path:
        return self.streams_dir / f"{activity_id}.json"

    def has_streams(self, activity_id: int | str) -> bool:
        return self.stream_path(activity_id).exists()

    def load_streams(self, activity_id: int | str) -> dict[str, list]:
        return json.loads(self.stream_path(activity_id).read_text())

    def save_streams(self, activity_id: int | str, payload: dict[str, list]) -> Path:
        path = self.stream_path(activity_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, payload)
        return path

    def stored_stream_ids(self) -> list[int]:
        if not self.streams_dir.exists():
            return []
        ids = []
        for path in self.streams_dir.glob("*.json"):
            try:
                ids.append(int(path.stem))
            except ValueError:
                continue
        return sorted(ids)


def _write_json(path: Path, payload: Any) -> None:
    """Write atomically, so an interrupted fetch can't leave truncated JSON."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    tmp.replace(path)
