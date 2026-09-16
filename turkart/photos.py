"""Photos and videos attached to a ride.

There is no JSON endpoint for these -- ``/activities/<id>/photos`` is a 404. The
activity *page* carries them instead, as the serialised props of a React
component:

    <div data-react-class='MediaThumbnailList'
         data-react-props='{"items":[{"photo_id":..., "large":..., "video":...}]}'>

So fetching media means loading the HTML page and reading that blob out. It is a
scrape and will break if Strava renames the component, hence the explicit error
rather than a silent empty list when the markup stops matching.

Media is downloaded to ``data/photos/<activity_id>/`` and, like the streams,
never re-fetched once present.
"""

from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import StravaClient, StravaError
from .store import Store

# Strava's media_type discriminator, as seen in the embedded props.
MEDIA_PHOTO = 1
MEDIA_VIDEO = 2

_PROPS_RE = re.compile(
    r"data-react-class=['\"]MediaThumbnailList['\"]\s+data-react-props=['\"](.*?)['\"]",
    re.S,
)


class MediaParseError(StravaError):
    """The activity page no longer matches the shape we scrape."""


@dataclass
class Media:
    photo_id: str
    activity_id: int
    media_type: int
    caption: str
    url: str | None       # best still image available
    video_url: str | None  # HLS playlist, when the item is a video
    # Where the photo was taken. Strava carries this per item, so a photo can be
    # tied back to the point on the route it belongs to.
    lat: float | None = None
    lng: float | None = None
    width: int | None = None
    height: int | None = None

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lng is not None

    @property
    def is_video(self) -> bool:
        return self.media_type == MEDIA_VIDEO or bool(self.video_url)

    @property
    def filename(self) -> str:
        return f"{self.photo_id}.jpg"


def extract_media(page_html: str, activity_id: int) -> list[Media]:
    """Pull the media list out of an activity page's HTML."""
    match = _PROPS_RE.search(page_html)
    if not match:
        return []  # no media on this activity

    raw = html_module.unescape(match.group(1))
    try:
        props = json.loads(raw)
    except ValueError as exc:
        raise MediaParseError(
            f"could not parse MediaThumbnailList props for {activity_id}"
        ) from exc

    items = props.get("items")
    if items is None:
        raise MediaParseError(
            f"MediaThumbnailList for {activity_id} has no 'items' -- markup changed?"
        )

    media = []
    for item in items:
        dims = (item.get("dimensions") or {}).get("large") or {}
        media.append(
            Media(
                photo_id=str(item.get("photo_id") or item.get("id")),
                activity_id=activity_id,
                media_type=int(item.get("media_type") or MEDIA_PHOTO),
                caption=item.get("caption_escaped") or "",
                # `large` and `thumbnail` are often the same URL; prefer large.
                url=item.get("large") or item.get("thumbnail"),
                video_url=item.get("video"),
                lat=_as_float(item.get("lat")),
                lng=_as_float(item.get("lng")),
                width=dims.get("width"),
                height=dims.get("height"),
            )
        )
    return media


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_media(client: StravaClient, activity_id: int) -> list[Media]:
    """Load an activity page and read its media list."""
    response = client._get(
        f"/activities/{activity_id}",
        headers={"Accept": "text/html", "Referer": "https://www.strava.com/"},
    )
    return extract_media(response.text, activity_id)


# ------------------------------------------------------------------- storage


def photo_dir(store: Store, activity_id: int) -> Path:
    return store.root / "photos" / str(activity_id)


def index_path(store: Store) -> Path:
    return store.root / "photos" / "index.json"


def load_index(store: Store) -> dict[str, list[dict[str, Any]]]:
    path = index_path(store)
    return json.loads(path.read_text()) if path.exists() else {}


def save_index(store: Store, index: dict[str, list[dict[str, Any]]]) -> Path:
    from .store import write_json

    return write_json(index_path(store), index, indent=2)


def download(client: StravaClient, media: Media, store: Store) -> Path | None:
    """Save one still image. Videos are indexed but not downloaded."""
    if media.url is None or media.is_video:
        return None
    dest = photo_dir(store, media.activity_id) / media.filename

    client.download_to(media.url, dest)
    return dest
