"""Collecting another athlete's rides and photos.

The rest of turkart reads your own activities through
``/athlete/training_activities``, which only ever returns the logged-in athlete.
For someone else there is a different route: the weekly interval fragment behind
their profile page.

    GET /athletes/<id>/interval?chart_type=miles&interval=YYYYWW
                               &interval_type=week&year_offset=0

``year_offset`` is not optional -- without it the endpoint answers 200 with an
empty body, which looks like "no activities" rather than "malformed request".

The response is a jQuery fragment that mounts the feed microfrontend, and its
props carry, for every activity that week, the activity summary *and* its photo
list. That makes it far cheaper than our own path: one request per **week** for
both, against one request per **activity** for photos alone.

Only what the athlete has shared with you is visible; this sees exactly what
their profile shows you in a browser, and nothing more.

One thing it does **not** carry: another athlete's photos arrive with no `lat`
or `lng` at all -- verified absent from the raw payload, not merely unparsed.
Your own photos do have them. So another athlete's collection can be organised
by date and by activity, but not by where a picture was taken.
"""

from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterator

from .client import StravaClient, StravaError

_PROPS_RE = re.compile(r"data-react-props=\\?['\"](.*)", re.S)


class ProfileParseError(StravaError):
    """The interval fragment no longer matches the shape we scrape."""


@dataclass
class AthletePhoto:
    """One photo from another athlete's ride."""

    photo_id: str
    activity_id: int
    athlete_id: int
    url: str | None
    thumbnail: str | None
    caption: str
    lat: float | None
    lng: float | None
    width: int | None
    height: int | None
    taken_on: str | None       # the activity's date, ISO
    activity_name: str | None
    location: str | None       # e.g. "Oslo", as Strava displays it

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lng is not None

    @property
    def filename(self) -> str:
        return f"{self.photo_id}.jpg"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def week_key(day: date) -> str:
    """Strava's ``interval`` value: ISO year and week, zero-padded."""
    iso = day.isocalendar()
    return f"{iso[0]}{iso[1]:02d}"


def weeks_back(count: int, end: date | None = None) -> list[str]:
    """Week keys from the most recent backwards, newest first."""
    end = end or date.today()
    return [week_key(end - timedelta(weeks=i)) for i in range(count)]


_JS_SIMPLE = {'"': '"', "'": "'", "/": "/", "n": "\n", "r": "\r", "t": "\t", "\\": "\\"}


def _js_unescape(text: str) -> str:
    r"""Undo JavaScript string-literal escaping without touching UTF-8.

    `codecs.decode(..., "unicode_escape")` is the obvious tool and is wrong here:
    it decodes through latin-1, so every non-ASCII byte is mangled -- accented
    place names came back as mojibake and emoji were destroyed. Note that an
    ASCII-only sample hides this entirely. This body is already UTF-8
    text with no \uXXXX escapes, so only the backslash pairs need undoing.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            simple = _JS_SIMPLE.get(nxt)
            if simple is not None:
                out.append(simple)
                i += 2
                continue
            if nxt == "u" and i + 6 <= len(text):
                try:
                    out.append(chr(int(text[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            # Any other escape: JavaScript yields the bare character.
            out.append(nxt)
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _decode_fragment(body: str) -> dict[str, Any]:
    """Pull the react props object out of the jQuery fragment."""
    text = _js_unescape(body)
    match = _PROPS_RE.search(text)
    if not match:
        raise ProfileParseError("no react props in the interval fragment")
    raw = html_module.unescape(match.group(1))
    try:
        # raw_decode: the props are followed by the rest of the fragment.
        props, _ = json.JSONDecoder().raw_decode(raw)
    except ValueError as exc:
        raise ProfileParseError(f"could not parse interval props: {exc}") from exc
    return props


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_week(body: str, athlete_id: int) -> tuple[list[dict], list[AthletePhoto]]:
    """Activities and photos for one week. Returns (activities, photos)."""
    props = _decode_fragment(body)
    context = props.get("appContext")
    if context is None:
        raise ProfileParseError("interval props carry no appContext -- markup changed?")

    activities: list[dict] = []
    photos: list[AthletePhoto] = []

    for entry in context.get("preFetchedEntries") or []:
        activity = entry.get("activity") or {}
        activity_id = activity.get("id")
        if activity_id is None:
            continue

        when = activity.get("startDate")
        where = (activity.get("timeAndLocation") or {}).get("location")
        name = activity.get("activityName") or activity.get("name")
        activities.append(
            {
                "id": int(activity_id),
                "athlete_id": athlete_id,
                "name": name,
                "start": when,
                "location": where,
                "is_commute": activity.get("isCommute"),
            }
        )

        for item in (activity.get("mapAndPhotos") or {}).get("photoList") or []:
            # Videos carry an HLS url and no still worth keeping.
            if item.get("video") not in (None, "None"):
                continue
            dims = (item.get("dimensions") or {}).get("large") or {}
            photos.append(
                AthletePhoto(
                    photo_id=str(item.get("photo_id") or item.get("id")),
                    activity_id=int(item.get("activity_id") or activity_id),
                    athlete_id=athlete_id,
                    url=item.get("large") or item.get("thumbnail"),
                    thumbnail=item.get("thumbnail"),
                    caption=item.get("caption_escaped") or "",
                    lat=_as_float(item.get("lat")),
                    lng=_as_float(item.get("lng")),
                    width=dims.get("width"),
                    height=dims.get("height"),
                    taken_on=(when or "")[:10] or None,
                    activity_name=name,
                    location=where,
                )
            )

    return activities, photos


def fetch_week(
    client: StravaClient, athlete_id: int, interval: str
) -> tuple[list[dict], list[AthletePhoto]]:
    """One week of an athlete's activities and photos."""
    response = client._get(
        f"/athletes/{athlete_id}/interval",
        params={
            "chart_type": "miles",
            "interval": interval,
            "interval_type": "week",
            "year_offset": 0,
        },
        headers={
            "Accept": (
                "text/javascript, application/javascript, "
                "application/ecmascript, application/x-ecmascript"
            ),
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.strava.com/athletes/{athlete_id}",
        },
    )
    if not response.text.strip():
        # An empty body means the request shape was wrong, not that the week is
        # empty -- a genuinely empty week still returns the chart scaffolding.
        raise ProfileParseError(
            f"empty response for week {interval}; check the request parameters"
        )
    return parse_week(response.text, athlete_id)


def walk(
    client: StravaClient,
    athlete_id: int,
    weeks: int,
    end: date | None = None,
    on_week=None,
) -> Iterator[tuple[str, list[dict], list[AthletePhoto]]]:
    """Walk back `weeks` weeks, newest first, yielding each week's haul."""
    for interval in weeks_back(weeks, end):
        activities, photos = fetch_week(client, athlete_id, interval)
        if on_week:
            on_week(interval, activities, photos)
        yield interval, activities, photos


# ------------------------------------------------------------------- storage


def athlete_dir(store, athlete_id: int):
    return store.root / "athletes" / str(athlete_id)


def collection_path(store, athlete_id: int):
    return athlete_dir(store, athlete_id) / "index.json"


def load_collection(store, athlete_id: int) -> dict[str, Any]:
    path = collection_path(store, athlete_id)
    if not path.exists():
        return {"athlete_id": athlete_id, "weeks": {}, "photos": {}, "activities": {}}
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {"athlete_id": athlete_id, "weeks": {}, "photos": {}, "activities": {}}


def save_collection(store, athlete_id: int, data: dict[str, Any]):
    from .store import write_json

    return write_json(collection_path(store, athlete_id), data,
                      indent=1, ensure_ascii=False)


def normalise(text: str) -> str:
    """Casefold and strip accents, so an accented ride name matches a plain
    ASCII search term.

    Ride names carry accents and emoji; nobody types those into a filter.
    """
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def matches(name: str | None, needle: str | None) -> bool:
    if not needle:
        return True
    return normalise(needle) in normalise(name or "")


def photo_path(store, athlete_id: int, photo_id: str):
    return athlete_dir(store, athlete_id) / "photos" / f"{photo_id}.jpg"


def download_photo(client: StravaClient, photo: AthletePhoto, store):
    """Save one still. Returns the path, or None if it was already there."""
    if not photo.url:
        return None
    dest = photo_path(store, photo.athlete_id, photo.photo_id)
    return dest if client.download_to(photo.url, dest) else None
