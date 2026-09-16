"""Client for strava.com's internal (browser) JSON endpoints.

These are the endpoints the Strava web app calls for itself. They are not
documented or versioned, so treat every response shape as provisional: the
parsing here is deliberately forgiving, and `raw` payloads are kept on disk so a
shape change costs a re-parse rather than a re-fetch.
"""

from __future__ import annotations

import random
import time
from pathlib import Path
import uuid
from dataclasses import dataclass
from typing import Any, Iterator

import requests

from .session import BrowserSession, SessionError

BASE = "https://www.strava.com"

# Politeness: this is a browser-facing backend, so we pace ourselves rather than
# hammering it. Roughly one request per second, jittered.
MIN_INTERVAL = 0.8
JITTER = 0.4

# Stream types worth having for mapping. `latlng` is the track itself; the rest
# make the legend and any repair work possible.
DEFAULT_STREAM_TYPES = ("latlng", "distance", "altitude", "time")


class StravaError(RuntimeError):
    """A request to Strava failed or came back in an unexpected shape."""


@dataclass
class Activity:
    """A ride as summarised by the training-activities list."""

    id: int
    name: str
    start_date_local: str | None
    distance_m: float | None
    elevation_gain_m: float | None
    moving_time_s: float | None
    sport_type: str | None
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> Activity:
        return cls(
            id=int(item["id"]),
            name=item.get("name") or "(untitled)",
            start_date_local=item.get("start_date_local_raw") or item.get("start_date_local"),
            distance_m=_to_float(item.get("distance_raw")),
            elevation_gain_m=_to_float(item.get("elevation_gain_raw")),
            moving_time_s=_to_float(item.get("moving_time_raw")),
            sport_type=item.get("sport_type") or item.get("type"),
            raw=item,
        )


class StravaClient:
    def __init__(self, session: BrowserSession, min_interval: float = MIN_INTERVAL):
        self._creds = session
        self._http = session.build()
        self._min_interval = min_interval
        self._last_request = 0.0
        # Nothing in the responses reports rate-limit state -- the internal
        # endpoints send no X-RateLimit headers -- so counting here is the only
        # way to know what a run cost.
        self.requests = 0
        # Strava's activity search ties a page sequence to a session id; reusing
        # one across pages keeps pagination stable while we walk a result set.
        self._search_session_id = str(uuid.uuid4())

    # --------------------------------------------------------------- transport

    def _get(self, path: str, *, params: dict[str, Any] | None = None, **kwargs) -> requests.Response:
        elapsed = time.monotonic() - self._last_request
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait + random.uniform(0, JITTER))

        url = path if path.startswith("http") else f"{BASE}{path}"
        response = self._http.get(url, params=params, timeout=30, **kwargs)
        self._last_request = time.monotonic()
        # Only calls to Strava itself spend the per-application budget. Photo
        # bytes come from their CDN on a different host, and counting those was
        # inflating the tally badly: one download run of 186 images read as 186
        # API requests against a 1,000/day limit it does not touch.
        if url.startswith(BASE):
            self.requests += 1

        if response.status_code in (401, 403):
            raise SessionError(
                f"Strava rejected the request ({response.status_code}). The browser "
                "session has most likely expired -- grab a fresh 'Copy as cURL' "
                "and re-run: turkart auth import <file>"
            )
        if response.status_code == 429:
            raise StravaError("Rate limited by Strava (429). Wait a while and retry.")
        # A logged-out session is often served as a 200 redirect to the login page
        # rather than a 401, so check where we actually landed.
        if "/login" in response.url or "/onboarding" in response.url:
            raise SessionError(
                "Redirected to the login page -- the browser session has expired. "
                "Re-run: turkart auth import <file>"
            )
        if not response.ok:
            raise StravaError(f"GET {url} -> {response.status_code}")
        return response

    def _get_json(self, path: str, **kwargs) -> Any:
        response = self._get(path, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            snippet = response.text[:200].replace("\n", " ")
            raise StravaError(f"Expected JSON from {path}, got: {snippet!r}") from exc

    def download_to(self, url: str, dest: "Path") -> bool:
        """Fetch a URL to a file. False if it was already there.

        Written through a temporary name so an interrupted download cannot leave
        a truncated image that later looks complete.
        """
        if dest.exists():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        response = self._get(url)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(dest)
        return True

    # ---------------------------------------------------------------- identity

    def whoami(self) -> int | None:
        """Athlete id embedded in the session's ``_strava_idcf`` JWT, if present."""
        import base64
        import json

        token = self._creds.cookies.get("_strava_idcf")
        if not token:
            return None
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload)).get("athleteId")
        except Exception:
            return None

    # -------------------------------------------------------------- activities

    def training_activities(
        self,
        *,
        sport_type: str | None = None,
        keywords: str = "",
        tags: str = "",
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Activity], int]:
        """One page of /athlete/training_activities. Returns (activities, total)."""
        params = {
            "keywords": keywords,
            "sport_type": sport_type or "",
            "tags": tags,
            "commute": "",
            "private_activities": "",
            "trainer": "",
            "gear": "",
            "search_session_id": self._search_session_id,
            "new_activity_only": "false",
            "order": "",
            "page": page,
            "per_page": per_page,
        }
        payload = self._get_json(
            "/athlete/training_activities",
            params=params,
            headers={
                "Accept": (
                    "text/javascript, application/javascript, "
                    "application/ecmascript, application/x-ecmascript"
                ),
                "Referer": f"{BASE}/athlete/training",
            },
        )
        if not isinstance(payload, dict) or "models" not in payload:
            raise StravaError(
                f"Unexpected training_activities shape: {type(payload).__name__} "
                f"keys={list(payload)[:8] if isinstance(payload, dict) else 'n/a'}"
            )
        models = payload.get("models") or []
        total = int(payload.get("total") or len(models))
        return [Activity.from_payload(m) for m in models], total

    def iter_activities(
        self,
        *,
        sport_type: str | None = None,
        keywords: str = "",
        tags: str = "",
        per_page: int = 20,
        max_pages: int = 200,
    ) -> Iterator[Activity]:
        """Walk every page of a training-activities search."""
        seen: set[int] = set()
        for page in range(1, max_pages + 1):
            activities, total = self.training_activities(
                sport_type=sport_type, keywords=keywords, tags=tags,
                page=page, per_page=per_page,
            )
            if not activities:
                return
            for activity in activities:
                if activity.id not in seen:
                    seen.add(activity.id)
                    yield activity
            if len(seen) >= total:
                return

    # ----------------------------------------------------------------- streams

    def streams(
        self, activity_id: int, types: tuple[str, ...] = DEFAULT_STREAM_TYPES
    ) -> dict[str, list]:
        """Raw sensor streams for one activity, keyed by stream type.

        Every stream is the same length and index-aligned, so `latlng[i]`,
        `altitude[i]` and `time[i]` all describe the same instant.
        """
        payload = self._get_json(
            f"/activities/{activity_id}/streams",
            params=[("stream_types[]", t) for t in types],
            headers={"Referer": f"{BASE}/activities/{activity_id}"},
        )
        if not isinstance(payload, dict):
            raise StravaError(f"Unexpected streams shape for {activity_id}")
        return payload


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
