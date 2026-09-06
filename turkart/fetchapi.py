"""A tiny local API behind ``explore --serve``.

The explorer is otherwise a static page, but choosing rides and then discovering
they have no photos means dropping to the shell, running a sync, and rebuilding.
This lets the page ask for the photos of the rides it has selected, which is also
the fetching pattern a hosted version would need: pull media for what someone
actually picked, not for their whole history.

It binds to localhost only and exists for the duration of ``--serve``.
"""

from __future__ import annotations

import json
from typing import Any

from .client import DEFAULT_STREAM_TYPES, StravaClient, StravaError
from .photos import Media, download, fetch_media, load_index, photo_dir, save_index
from .session import BrowserSession, SessionError
from .store import Store
from . import usage

# One request is one person clicking a button, so keep the work bounded.
MAX_IDS_PER_CALL = 40


def expand_members(store: Store, ids: list[int]) -> list[int]:
    """Replace each merged ride with the activities it absorbed.

    Photos belong to the activity they were uploaded to, so a merged ride's media
    lives under its members, not under the surviving id.
    """
    from .merge import load_merges

    merges = {int(m["members"][0]): [int(x) for x in m["members"]] for m in load_merges(store)}
    out: list[int] = []
    for i in ids:
        for member in merges.get(i, [i]):
            if member not in out:
                out.append(member)
    return out


def _account(store: Store, client: StravaClient | None) -> int:
    """Fold a client's request count into the shared daily tally.

    Fetches driven from the page spend the same per-application budget as the
    CLI's, so they have to be counted in the same place or the total is wrong.
    """
    made = client.requests if client else 0
    usage.record(store, made)
    return made


def sync_photos(store: Store, ids: list[int]) -> dict[str, Any]:
    """Scan and download media for specific activities.

    Mirrors the CLI's two phases: scan pages not yet indexed, then download any
    still missing from disk. Returns a summary plus fresh page-ready photo
    records, so the caller can update in place instead of rebuilding.
    """
    from .explore import photo_records

    members = expand_members(store, ids)[:MAX_IDS_PER_CALL]
    if not members:
        return {"ok": True, "scanned": 0, "downloaded": 0, "failed": 0,
                "requests": 0, "usedToday": usage.today(store), "rides": {}}

    index = load_index(store)
    client: StravaClient | None = None
    scanned = 0

    to_scan = [m for m in members if str(m) not in index]
    if to_scan:
        client = StravaClient(BrowserSession.load())
        for activity_id in to_scan:
            media = fetch_media(client, activity_id)
            index[str(activity_id)] = [
                {
                    "photo_id": m.photo_id, "media_type": m.media_type,
                    "caption": m.caption, "url": m.url, "video_url": m.video_url,
                    "is_video": m.is_video, "lat": m.lat, "lng": m.lng,
                    "width": m.width, "height": m.height,
                }
                for m in media
            ]
            scanned += 1
        save_index(store, index)

    pending = []
    videos = 0
    for activity_id in members:
        for item in index.get(str(activity_id), []):
            if item.get("is_video"):
                videos += 1
                continue
            if not (photo_dir(store, activity_id) / f"{item['photo_id']}.jpg").exists():
                pending.append((activity_id, item))

    downloaded = failed = 0
    if pending:
        client = client or StravaClient(BrowserSession.load())
        for activity_id, item in pending:
            media = Media(
                photo_id=item["photo_id"], activity_id=activity_id,
                media_type=item.get("media_type", 1), caption=item.get("caption", ""),
                url=item.get("url"), video_url=item.get("video_url"),
            )
            try:
                if download(client, media, store):
                    downloaded += 1
            except StravaError:
                failed += 1

    # Report against the ids the caller asked for, keyed by the ride they know,
    # not by the member activities the media actually lives under.
    fresh = load_index(store)
    rides = {}
    for ride_id in ids:
        rides[str(ride_id)] = photo_records(store, expand_members(store, [ride_id]), fresh)

    made = _account(store, client)
    return {
        "ok": True, "scanned": scanned, "downloaded": downloaded,
        "failed": failed, "videos": videos,
        "requests": made, "usedToday": usage.today(store),
        "rides": rides,
    }


def sync_streams(store: Store, ids: list[int], tolerance_m: float = 8.0) -> dict[str, Any]:
    """Download GPS tracks for specific activities.

    Returns freshly built ride records so the page can replace its placeholders
    in place. Records come from build_rides rather than being assembled here, so
    a fetched ride is identical to one that was present at build time -- merges
    folded in, photos attached, geometry derived the same way.
    """
    from .explore import build_rides

    members = expand_members(store, ids)[:MAX_IDS_PER_CALL]
    wanted = [m for m in members if not store.has_streams(m)]

    client: StravaClient | None = None
    fetched = failed = 0
    empty: list[int] = []
    if wanted:
        client = StravaClient(BrowserSession.load())
        activities = store.load_activities()
        for activity_id in wanted:
            # A ride Strava recorded without GPS has no track to ask for.
            if (activities.get(str(activity_id)) or {}).get("has_latlng") is False:
                empty.append(activity_id)
                continue
            try:
                payload = client.streams(activity_id, DEFAULT_STREAM_TYPES)
            except StravaError:
                failed += 1
                continue
            if not (payload.get("latlng") or []):
                empty.append(activity_id)
                continue
            store.save_streams(activity_id, payload)
            fetched += 1

    rides = build_rides(store, tolerance_m=tolerance_m, only_ids=set(members) | set(ids))
    # Fetches driven from the page count against the same budget as the CLI's.
    made = _account(store, client)
    return {
        "ok": True, "fetched": fetched, "failed": failed, "empty": len(empty),
        "requests": made, "usedToday": usage.today(store),
        "rides": {str(r["id"]): r for r in rides},
    }


def handle(
    store: Store, path: str, body: bytes, tolerance_m: float = 8.0
) -> tuple[int, dict[str, Any]]:
    """Route one API call. Returns (status, payload)."""
    if path not in ("/api/photos/sync", "/api/streams/sync"):
        return 404, {"ok": False, "error": f"no such endpoint: {path}"}

    try:
        payload = json.loads(body or b"{}")
        ids = [int(i) for i in (payload.get("ids") or [])]
    except (ValueError, TypeError) as exc:
        return 400, {"ok": False, "error": f"bad request: {exc}"}

    if not ids:
        return 400, {"ok": False, "error": "no ride ids given"}

    try:
        if path == "/api/streams/sync":
            return 200, sync_streams(store, ids, tolerance_m)
        return 200, sync_photos(store, ids)
    except SessionError as exc:
        # Expired cookies are the common failure and need a human, so say so
        # plainly rather than surfacing a bare 401.
        return 401, {"ok": False, "error": str(exc)}
    except StravaError as exc:
        return 502, {"ok": False, "error": str(exc)}
