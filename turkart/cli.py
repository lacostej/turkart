"""Command line entry point.

    turkart auth import LOCAL/strava_web.md   # lift cookies from a cURL capture
    turkart auth status                       # what's stored, and how stale
    turkart activities sync --sport-type Ride # build/refresh the ride index
    turkart activities list --after 2026-01-01
    turkart streams fetch --after 2026-01-01  # download tracks for those rides
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .client import DEFAULT_STREAM_TYPES, StravaClient, StravaError
from .session import BrowserSession, SessionError
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="turkart", description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="group", required=True)

    # -- auth ---------------------------------------------------------------
    auth = sub.add_parser("auth", help="manage the browser session").add_subparsers(
        dest="command", required=True
    )
    imp = auth.add_parser("import", help="extract cookies from a cURL capture")
    imp.add_argument("file", type=Path, help="file containing 'Copy as cURL' output ('-' for stdin)")
    auth.add_parser("status", help="show the stored session and its staleness")
    auth.add_parser("check", help="verify the session against Strava")

    # -- activities ---------------------------------------------------------
    acts = sub.add_parser("activities", help="the ride index").add_subparsers(
        dest="command", required=True
    )
    sync = acts.add_parser("sync", help="fetch ride summaries into the index")
    sync.add_argument("--sport-type", default="Ride", help="Ride, Run, ... (default: Ride)")
    sync.add_argument("--tags", default="", help="Strava tag id, e.g. 16")
    sync.add_argument("--keywords", default="")
    sync.add_argument("--pages", type=int, default=200, help="max pages to walk")
    sync.add_argument("--per-page", type=int, default=20)
    sync.add_argument("--full", action="store_true",
                      help="walk every page instead of stopping at known activities")

    lst = acts.add_parser("list", help="show indexed rides")
    lst.add_argument("--after", type=_parse_date, help="YYYY-MM-DD, inclusive")
    lst.add_argument("--before", type=_parse_date, help="YYYY-MM-DD, inclusive")
    lst.add_argument("--tag", action="append", type=int,
                     help="Strava tag id, repeatable (16 = With Kids)")
    lst.add_argument("--sport-type-filter", action="append", metavar="TYPE",
                     help="keep only these sport types, repeatable (e.g. Ride)")
    lst.add_argument("--limit", type=int, default=0, help="0 = no limit")

    # -- streams ------------------------------------------------------------
    streams = sub.add_parser("streams", help="ride tracks").add_subparsers(
        dest="command", required=True
    )
    fetch = streams.add_parser("fetch", help="download tracks for indexed rides")
    fetch.add_argument("--after", type=_parse_date)
    fetch.add_argument("--before", type=_parse_date)
    fetch.add_argument("--ids", nargs="*", type=int, help="explicit activity ids")
    fetch.add_argument("--refresh", action="store_true", help="re-fetch already-stored tracks")
    fetch.add_argument("--selection", type=Path, metavar="FILE",
                       help="a selection.json saved from the explorer; fetches exactly "
                            "those rides (merged rides expand to their members)")
    fetch.add_argument("--tag", action="append", type=int)
    fetch.add_argument("--sport-type-filter", action="append", metavar="TYPE")
    fetch.add_argument("--limit", type=int, default=0)

    # -- merge --------------------------------------------------------------
    mrg = sub.add_parser("merge", help="join rides split by a broken recording").add_subparsers(
        dest="command", required=True
    )
    sug = mrg.add_parser("suggest", help="find likely split recordings")
    sug.add_argument("--max-time-gap", type=float, default=120.0, metavar="MIN")
    sug.add_argument("--max-space-gap", type=float, default=0.75, metavar="KM")
    sug.add_argument("--min-junction", type=float, default=2.0, metavar="KM",
                     help="junction must be this far from the ride start; below it, "
                          "the two rides simply met back at home (default: 2)")
    sug.add_argument("--apply", action="store_true", help="save every suggestion as a merge")

    app = mrg.add_parser("apply", help="merge specific activity ids")
    app.add_argument("ids", nargs="+", type=int)
    app.add_argument("--name", help="name for the merged ride")

    mrg.add_parser("list", help="show saved merges")
    rm = mrg.add_parser("remove", help="undo a saved merge")
    rm.add_argument("ids", nargs="+", type=int, help="any member id of the merge")

    # -- photos -------------------------------------------------------------
    pho = sub.add_parser("photos", help="ride photos and videos").add_subparsers(
        dest="command", required=True
    )
    psync = pho.add_parser("sync", help="find and download media for indexed rides")
    psync.add_argument("--after", type=_parse_date)
    psync.add_argument("--before", type=_parse_date)
    psync.add_argument("--tag", action="append", type=int)
    psync.add_argument("--sport-type-filter", action="append", metavar="TYPE")
    psync.add_argument("--ids", nargs="*", type=int)
    psync.add_argument("--selection", type=Path, metavar="FILE",
                       help="a selection.json saved from the explorer; syncs exactly "
                            "those rides (merged rides expand to their members)")
    psync.add_argument("--scan-only", action="store_true",
                       help="find the media and index it, but download nothing -- "
                            "reports how many photos and roughly how big before you commit")
    psync.add_argument("--limit", type=int, default=0)
    psync.add_argument("--refresh", action="store_true",
                       help="re-scan activities already in the photo index")
    pho.add_parser("list", help="show what media is indexed")

    # -- athlete ------------------------------------------------------------
    ath = sub.add_parser("athlete", help="another athlete's rides and photos").add_subparsers(
        dest="command", required=True
    )
    asy = ath.add_parser("sync", help="walk an athlete's weeks, collecting photos")
    asy.add_argument("--id", type=int, required=True, help="athlete id, from their profile URL")
    asy.add_argument("--weeks", type=int, default=12,
                     help="how many weeks back (default: 12); also the cap for --until-empty")
    asy.add_argument("--until-empty", type=int, nargs="?", const=10, metavar="N",
                     help="keep walking back until N consecutive weeks hold no activity "
                          "at all (default 10), rather than stopping at a fixed count. "
                          "A fixed count cannot tell a boundary from the end of a history.")
    asy.add_argument("--match", default=None,
                     help="only rides whose name contains this, accent-insensitively")
    asy.add_argument("--scan-only", action="store_true",
                     help="collect the index but download nothing, and report the size")
    asy.add_argument("--refresh", action="store_true", help="re-read every week already collected")
    asy.add_argument("--recheck", type=int, default=2, metavar="N",
                     help="always re-read the N most recent weeks even if already "
                          "collected (default 2). The current week is cached the moment "
                          "it is first read, so without this a ride added to it later "
                          "would never be picked up.")

    alist = ath.add_parser("list", help="show what has been collected")
    alist.add_argument("--id", type=int, required=True)
    alist.add_argument("--match", default=None)

    aexp = ath.add_parser("export", help="write a self-contained gallery folder")
    aexp.add_argument("--id", type=int, required=True)
    aexp.add_argument("--match", default=None)
    aexp.add_argument("-o", "--output", type=Path, default=None,
                      help="where to write it (default: galleries/<title>)")
    aexp.add_argument("--title", default="Gallery",
                      help="shown as the heading, and names the output folder")
    aexp.add_argument("--after", type=_parse_date, metavar="YYYY-MM-DD",
                      help="only photos from this date onwards")
    aexp.add_argument("--before", type=_parse_date, metavar="YYYY-MM-DD",
                      help="only photos up to this date")
    aexp.add_argument("--weeks", type=int, metavar="N",
                      help="only the last N weeks. The collection is cumulative and the "
                           "sync window does not shrink it, so limiting the export is a "
                           "separate thing from limiting the fetch.")
    aexp.add_argument("--show-location", action="store_true",
                      help="display the ride's start location under each photo. Off by "
                           "default: it is where the ride began, not where the picture "
                           "was taken, and saying otherwise is wrong more often than right")
    aexp.add_argument("--open", action="store_true", help="open it when done")

    # -- usage --------------------------------------------------------------
    use = sub.add_parser("usage", help="how many requests have been spent")
    use.set_defaults(command="show")

    # -- explore ------------------------------------------------------------
    exp = sub.add_parser("explore", help="build the local ride browser")
    exp.set_defaults(command="build")
    exp.add_argument("-o", "--output", type=Path, default=Path("build/explore.html"))
    exp.add_argument("--tolerance", type=float, default=8.0,
                     help="track simplification tolerance in metres (default: 8)")
    exp.add_argument("--open", action="store_true", help="open it in the browser when done")
    exp.add_argument("--no-carto", action="store_true",
                     help="ignore the CARTO key and use the keyless Esri basemap")
    exp.add_argument("--serve", nargs="?", type=int, const=8000, metavar="PORT",
                     help="serve it over http://localhost instead of file:// (default port 8000). "
                          "osm.org blocks tiles requested without a Referer, which file:// pages "
                          "do not send; serving fixes that.")
    exp.add_argument("--after", type=_parse_date, help="YYYY-MM-DD, inclusive")
    exp.add_argument("--before", type=_parse_date, help="YYYY-MM-DD, inclusive")
    exp.add_argument("--tag", action="append", type=int,
                     help="Strava tag id, repeatable (16 = With Kids)")
    exp.add_argument("--sport-type-filter", action="append", metavar="TYPE",
                     help="keep only these sport types, repeatable (e.g. Ride)")

    args = parser.parse_args(argv)
    store = Store(args.data_dir)

    try:
        handler = _HANDLERS[(args.group, args.command)]
        return handler(args, store)
    except SessionError as exc:
        print(f"session error: {exc}", file=sys.stderr)
        return 2
    except StravaError as exc:
        print(f"strava error: {exc}", file=sys.stderr)
        return 3


# --------------------------------------------------------------------- auth


def _auth_import(args, store: Store) -> int:
    text = sys.stdin.read() if str(args.file) == "-" else args.file.read_text()
    session = BrowserSession.from_text(text)
    path = session.save()
    print(f"saved session to {path}")
    print(session.describe())
    return 0


def _auth_status(args, store: Store) -> int:
    print(BrowserSession.load().describe())
    return 0


def _auth_check(args, store: Store) -> int:
    client = StravaClient(BrowserSession.load())
    activities, total = client.training_activities(per_page=1)
    athlete = client.whoami()
    print(f"session OK -- athlete {athlete}, {total} activities visible")
    if activities:
        print(f"most recent: {_format_activity(activities[0])}")
    _report_usage(store, client)
    return 0


# --------------------------------------------------------------- activities


def _activities_sync(args, store: Store) -> int:
    """Page the activity list, stopping once it reaches rides already indexed.

    Strava returns activities newest first, so a page containing nothing new
    means everything past it is already held. Stopping there turns a repeat sync
    from a full walk of the history into one or two requests -- which matters
    because the rate budget is per application and shared with everything else
    turkart does.
    """
    client = StravaClient(BrowserSession.load())
    known = set(store.load_activities())
    fetched: dict[str, dict] = {}
    stopped_early = False

    for page in range(1, args.pages + 1):
        activities, total = client.training_activities(
            sport_type=args.sport_type or None,
            keywords=args.keywords,
            tags=args.tags,
            page=page,
            per_page=args.per_page,
        )
        if not activities:
            break

        new_here = 0
        for activity in activities:
            key = str(activity.id)
            if key not in known and key not in fetched:
                new_here += 1
            fetched[key] = activity.raw
        print(f"\r  page {page}: {len(fetched)} seen, {new_here} new...",
              end="", file=sys.stderr, flush=True)

        # A whole page of already-known rides means the rest is older still.
        if not args.full and known and new_here == 0:
            stopped_early = True
            break
        if len(fetched) >= total:
            break
    print(file=sys.stderr)

    added, updated = store.merge_activities(fetched)
    print(f"fetched {len(fetched)} ({added} new, {updated} changed) -> {store.activities_file}")
    if stopped_early:
        print("stopped early: reached activities already indexed (--full to walk them all)")
    _report_usage(store, client)
    return 0


def _report_usage(store: Store, client) -> None:
    from . import usage

    usage.record(store, client.requests)
    print(usage.report(store, client.requests))


def _activities_list(args, store: Store) -> int:
    rows = _select_activities(store, args)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("no activities matched (run 'turkart activities sync' first?)")
        return 0
    for raw in rows:
        if raw.get("has_latlng") is False:
            marker = "-"
        elif store.has_streams(raw["id"]):
            marker = "*"
        else:
            marker = " "
        print(f"{marker} {_format_raw(raw)}")
    print(f"\n{len(rows)} activities  (* = track downloaded, - = no GPS)")
    return 0


# ------------------------------------------------------------------ streams


def _streams_fetch(args, store: Store) -> int:
    if args.selection:
        ids = _expand_members(store, _read_selection(args.selection))
        known = store.load_activities()
        targets = [known.get(str(i), {"id": i}) for i in ids]
        print(f"{len(targets)} activities from {args.selection}")
    elif args.ids:
        targets = [{"id": i} for i in _expand_members(store, args.ids)]
    else:
        targets = _select_activities(store, args)

    # A ride with no GPS (trainer, or a watch that never got a fix) has no track
    # to map, and its streams endpoint just returns empty arrays.
    trackless = [t for t in targets if t.get("has_latlng") is False]
    targets = [t for t in targets if t.get("has_latlng") is not False]

    pending = [t for t in targets if args.refresh or not store.has_streams(t["id"])]
    if args.limit:
        pending = pending[: args.limit]

    skipped = len(targets) - len(pending)
    if trackless:
        print(f"skipping {len(trackless)} rides with no GPS track")
    if not pending:
        print(f"nothing to fetch ({skipped} already downloaded)")
        return 0

    print(f"fetching {len(pending)} tracks ({skipped} already on disk)")
    client = StravaClient(BrowserSession.load())
    failures = 0
    for index, target in enumerate(pending, 1):
        activity_id = target["id"]
        label = target.get("name", activity_id)
        try:
            payload = client.streams(activity_id, DEFAULT_STREAM_TYPES)
        except StravaError as exc:
            failures += 1
            print(f"  [{index}/{len(pending)}] {activity_id} FAILED: {exc}", file=sys.stderr)
            continue
        store.save_streams(activity_id, payload)
        points = len(payload.get("latlng") or [])
        print(f"  [{index}/{len(pending)}] {activity_id} {label} -- {points} points")

    print(f"done: {len(pending) - failures} saved, {failures} failed")
    _report_usage(store, client)
    return 1 if failures else 0


# -------------------------------------------------------------------- merge


def _fmt_chain(chain: dict) -> str:
    head = f"  {chain['date']}  {chain['total_km']:5.1f} km  " + " + ".join(
        str(m) for m in chain["members"]
    )
    lines = [head]
    for name in chain["names"]:
        lines.append(f"        - {name[:56]}")
    for ev in chain["evidence"]:
        lines.append(
            f"        gap: {ev['time_gap_min']:.0f} min, "
            f"{ev['space_gap_km'] * 1000:.0f} m apart, "
            f"junction {ev['junction_km']:.1f} km from start"
        )
    return "\n".join(lines)


def _merge_suggest(args, store: Store) -> int:
    from .merge import load_merges, save_merges, suggest

    chains = suggest(store, args.max_time_gap, args.max_space_gap, args.min_junction)
    if not chains:
        print("no split recordings detected")
        return 0

    print(f"{len(chains)} likely split recording(s):\n")
    for chain in chains:
        print(_fmt_chain(chain))
        print()

    if not args.apply:
        print("re-run with --apply to save these, or: turkart merge apply <id> <id>")
        return 0

    merges = load_merges(store)
    known = {tuple(sorted(int(i) for i in m["members"])) for m in merges}
    added = 0
    for chain in chains:
        key = tuple(sorted(chain["members"]))
        if key not in known:
            merges.append({"members": chain["members"]})
            added += 1
    save_merges(store, merges)
    print(f"saved {added} new merge(s) to {merges_path_str(store)}")
    return 0


def merges_path_str(store: Store) -> str:
    from .merge import merges_path

    return str(merges_path(store))


def _merge_apply(args, store: Store) -> int:
    from .merge import MergeError, load_merges, merged_activity, save_merges

    if len(args.ids) < 2:
        print("need at least two activity ids to merge", file=sys.stderr)
        return 1
    try:
        summary = merged_activity(store, args.ids, args.name)
    except (MergeError, KeyError) as exc:
        print(f"cannot merge: {exc}", file=sys.stderr)
        return 1

    merges = load_merges(store)
    merges = [m for m in merges if not set(map(int, m["members"])) & set(args.ids)]
    entry = {"members": args.ids}
    if args.name:
        entry["name"] = args.name
    merges.append(entry)
    save_merges(store, merges)

    print(f"merged {' + '.join(map(str, args.ids))} -> {summary['id']}")
    print(f"  {summary['name']}")
    print(f"  {summary['distance_raw'] / 1000:.1f} km, {summary['elevation_gain_raw']:.0f} m")
    return 0


def _merge_list(args, store: Store) -> int:
    from .merge import load_merges

    merges = load_merges(store)
    if not merges:
        print("no merges saved")
        return 0
    activities = store.load_activities()
    for merge in merges:
        members = [int(m) for m in merge["members"]]
        names = [activities.get(str(m), {}).get("name", "?") for m in members]
        print(f"  {' + '.join(map(str, members))}")
        for member, name in zip(members, names):
            print(f"      {member}  {name[:52]}")
    print(f"\n{len(merges)} merge(s)")
    return 0


def _merge_remove(args, store: Store) -> int:
    from .merge import load_merges, save_merges

    merges = load_merges(store)
    targets = set(args.ids)
    kept = [m for m in merges if not set(map(int, m["members"])) & targets]
    save_merges(store, kept)
    print(f"removed {len(merges) - len(kept)} merge(s)")
    return 0


# ------------------------------------------------------------------- photos


def _read_selection(path: Path) -> list[int]:
    """Ride ids from a saved selection.

    Accepts what the explorer's "Save selection" button writes, and also a bare
    list of ids, so a hand-written file works too.
    """
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        rides = payload.get("rides") or []
        return [int(r["id"]) if isinstance(r, dict) else int(r) for r in rides]
    if isinstance(payload, list):
        return [int(r["id"]) if isinstance(r, dict) else int(r) for r in payload]
    raise ValueError(f"{path}: expected a selection object or a list of ids")


def _expand_members(store: Store, ids: list[int]) -> list[int]:
    """Replace each merged ride with the activities it absorbed.

    A merge collapses its members into one id, but photos are stored against the
    activity they were uploaded to, so the members are what must be scanned.
    """
    from .merge import load_merges

    merges = {int(m["members"][0]): [int(x) for x in m["members"]] for m in load_merges(store)}
    out: list[int] = []
    for i in ids:
        for member in merges.get(i, [i]):
            if member not in out:
                out.append(member)
    return out


def _photos_sync(args, store: Store) -> int:
    """Scan activities for media, then download whatever is still missing.

    Scanning and downloading are separate phases on purpose. Scanning loads an
    activity page; downloading only needs the URL the scan recorded. Keeping
    them apart means --scan-only can populate the index cheaply and a later run
    still fetches the images -- if "already indexed" were treated as "already
    downloaded", a scan-only pass would silently suppress the real sync.
    """
    from .photos import Media, download, fetch_media, load_index, save_index, photo_dir

    if args.selection:
        ids = _expand_members(store, _read_selection(args.selection))
        known = store.load_activities()
        targets = [known.get(str(i), {"id": i}) for i in ids]
        print(f"{len(targets)} activities from {args.selection}")
    elif args.ids:
        targets = [{"id": i} for i in _expand_members(store, args.ids)]
    else:
        targets = _select_activities(store, args)

    targets = [t for t in targets if t.get("has_latlng") is not False]
    if args.limit:
        targets = targets[: args.limit]
    index = load_index(store)

    # ---- phase 1: scan pages we have never looked at
    to_scan = [t for t in targets if args.refresh or str(t["id"]) not in index]
    client = None
    if to_scan:
        print(f"scanning {len(to_scan)} activities for media")
        client = StravaClient(BrowserSession.load())
        for n, target in enumerate(to_scan, 1):
            activity_id = int(target["id"])
            try:
                media = fetch_media(client, activity_id)
            except StravaError as exc:
                print(f"  [{n}/{len(to_scan)}] {activity_id} FAILED: {exc}", file=sys.stderr)
                continue
            index[str(activity_id)] = [
                {
                    "photo_id": m.photo_id, "media_type": m.media_type,
                    "caption": m.caption, "url": m.url, "video_url": m.video_url,
                    "is_video": m.is_video, "lat": m.lat, "lng": m.lng,
                    "width": m.width, "height": m.height,
                }
                for m in media
            ]
            name = target.get("name", "")
            print(f"  [{n}/{len(to_scan)}] {activity_id} {name[:34]:34s} {len(media)} item(s)")
        save_index(store, index)
    else:
        print("all target activities are already indexed")

    # ---- phase 2: download stills that are not on disk yet
    pending: list[tuple[int, dict]] = []
    videos = 0
    for target in targets:
        activity_id = int(target["id"])
        for item in index.get(str(activity_id), []):
            if item.get("is_video"):
                videos += 1
                continue
            if not (photo_dir(store, activity_id) / f"{item['photo_id']}.jpg").exists():
                pending.append((activity_id, item))

    if args.scan_only:
        avg_mb = _average_photo_mb(store)
        print(f"\n{len(pending)} photo(s) not yet on disk, {videos} video(s) (never downloaded)")
        print(f"roughly {len(pending) * avg_mb:.0f} MB to download "
              f"(at the {avg_mb:.2f} MB average of what is already here)")
        print("\nre-run without --scan-only to fetch them")
        if client:
            _report_usage(store, client)
        return 0

    if not pending:
        print(f"\nnothing to download -- every photo for these rides is already on disk")
        if client:
            _report_usage(store, client)
        return 0

    print(f"\ndownloading {len(pending)} photo(s)")
    client = client or StravaClient(BrowserSession.load())
    saved = failures = 0
    for n, (activity_id, item) in enumerate(pending, 1):
        media = Media(
            photo_id=item["photo_id"], activity_id=activity_id,
            media_type=item.get("media_type", 1), caption=item.get("caption", ""),
            url=item.get("url"), video_url=item.get("video_url"),
        )
        try:
            if download(client, media, store):
                saved += 1
        except StravaError as exc:
            failures += 1
            print(f"  {item['photo_id']} failed: {exc}", file=sys.stderr)
        if n % 10 == 0 or n == len(pending):
            print(f"  {n}/{len(pending)}")

    print(f"\n{saved} photo(s) downloaded, {failures} failed, {videos} video(s) skipped")
    _report_usage(store, client)
    return 1 if failures else 0


def _pending_photos(store: Store, index: dict, activity_ids: list[int]) -> list[str]:
    from .photos import photo_dir

    pending = []
    for activity_id in activity_ids:
        for item in index.get(str(activity_id), []):
            if item.get("is_video"):
                continue
            if not (photo_dir(store, activity_id) / f"{item['photo_id']}.jpg").exists():
                pending.append(item["photo_id"])
    return pending


def _average_photo_mb(store: Store, fallback: float = 0.6) -> float:
    """Mean size of the photos already downloaded, for estimating the rest."""
    files = list((store.root / "photos").rglob("*.jpg"))
    if not files:
        return fallback
    return sum(f.stat().st_size for f in files) / len(files) / 1e6


def _photos_list(args, store: Store) -> int:
    from .photos import load_index, photo_dir

    index = load_index(store)
    activities = store.load_activities()
    withmedia = {k: v for k, v in index.items() if v}
    for activity_id, items in sorted(withmedia.items()):
        name = activities.get(activity_id, {}).get("name", "?")
        on_disk = len(list(photo_dir(store, int(activity_id)).glob("*.jpg")))
        kinds = f"{sum(1 for i in items if not i['is_video'])} photo, {sum(1 for i in items if i['is_video'])} video"
        print(f"  {activity_id}  {kinds:20s} {on_disk} on disk  {name[:40]}")
    print(f"\n{len(withmedia)} of {len(index)} scanned activities have media")
    return 0


# ------------------------------------------------------------------ athlete


def _athlete_sync(args, store: Store) -> int:
    from . import athlete as A

    client = StravaClient(BrowserSession.load())
    data = A.load_collection(store, args.id)
    intervals = A.weeks_back(args.weeks)
    # A week already read is skipped -- except the most recent few. Those are
    # still accumulating: the current week gets cached on its first read, and a
    # ride uploaded into it afterwards would otherwise never be seen again.
    fresh = set(intervals[: max(0, args.recheck)])
    todo = [w for w in intervals
            if args.refresh or w not in data["weeks"] or w in fresh]
    empty_run = 0

    if todo:
        print(f"reading {len(todo)} week(s) of athlete {args.id}"
              f"{'' if args.refresh else f' ({len(intervals) - len(todo)} already collected)'}")
    for n, interval in enumerate(todo, 1):
        try:
            activities, photos = A.fetch_week(client, args.id, interval)
        except StravaError as exc:
            print(f"  {interval}: FAILED {exc}", file=sys.stderr)
            continue
        kept = 0
        for activity in activities:
            if not A.matches(activity["name"], args.match):
                continue
            data["activities"][str(activity["id"])] = activity
            kept += 1
        for photo in photos:
            act = data["activities"].get(str(photo.activity_id))
            if act is None:          # its ride did not match the filter
                continue
            data["photos"][photo.photo_id] = photo.to_dict()
        data["weeks"][interval] = {"activities": len(activities), "kept": kept}

        # An athlete's history ends where the weeks stop holding anything. A
        # fixed --weeks cannot distinguish that from its own boundary: twice
        # here the oldest ride sat exactly on the edge of the window, which
        # looked like a start date and was not.
        if args.until_empty:
            empty_run = 0 if activities else empty_run + 1
            if empty_run >= args.until_empty:
                print(f"\r  stopped at {interval}: {empty_run} consecutive empty weeks"
                      f"{' ' * 20}", file=sys.stderr)
                break
        print(f"\r  [{n}/{len(todo)}] {interval}: {len(activities)} rides, {kept} kept,"
              f" {len(data['photos'])} photos total   ", end="", file=sys.stderr, flush=True)
    if todo:
        print(file=sys.stderr)
    A.save_collection(store, args.id, data)

    pending = [p for p in data["photos"].values()
               if not A.photo_path(store, args.id, p["photo_id"]).exists()]
    print(f"{len(data['activities'])} matching ride(s), {len(data['photos'])} photo(s), "
          f"{len(pending)} not yet downloaded")

    if args.scan_only:
        avg = _average_photo_mb(store)
        print(f"roughly {len(pending) * avg:.0f} MB to download "
              f"(at the {avg:.2f} MB average seen so far)")
        print("\nre-run without --scan-only to fetch them")
        _report_usage(store, client)
        return 0

    saved = failed = 0
    for n, raw in enumerate(pending, 1):
        photo = A.AthletePhoto(**raw)
        try:
            if A.download_photo(client, photo, store):
                saved += 1
        except StravaError as exc:
            failed += 1
            print(f"  {photo.photo_id} failed: {exc}", file=sys.stderr)
        if n % 20 == 0 or n == len(pending):
            print(f"\r  downloaded {n}/{len(pending)}   ", end="", file=sys.stderr, flush=True)
    if pending:
        print(file=sys.stderr)
    print(f"{saved} photo(s) downloaded, {failed} failed")
    _report_usage(store, client)
    return 1 if failed else 0


def _athlete_list(args, store: Store) -> int:
    from . import athlete as A

    data = A.load_collection(store, args.id)
    rows = [a for a in data["activities"].values() if A.matches(a["name"], args.match)]
    rows.sort(key=lambda a: a.get("start") or "")
    by_activity: dict[str, list] = {}
    for p in data["photos"].values():
        by_activity.setdefault(str(p["activity_id"]), []).append(p)

    for a in rows:
        shots = by_activity.get(str(a["id"]), [])
        on_disk = sum(1 for p in shots
                      if A.photo_path(store, args.id, p["photo_id"]).exists())
        print(f"  {str(a.get('start'))[:10]}  {on_disk}/{len(shots)} photo  "
              f"{str(a.get('location') or '')[:22]:22} {str(a.get('name'))[:40]}")
    print(f"\n{len(rows)} ride(s), {sum(len(v) for v in by_activity.values())} photo(s), "
          f"weeks read: {len(data['weeks'])}")
    return 0


def _athlete_export(args, store: Store) -> int:
    from .gallery import default_output, export

    after = args.after
    if args.weeks:
        from datetime import date, timedelta

        cutoff = date.today() - timedelta(weeks=args.weeks)
        after = max(after, cutoff) if after else cutoff

    output = args.output or default_output(args.title)
    result = export(store, args.id, output, match=args.match, title=args.title,
                    show_location=args.show_location, after=after, before=args.before)
    if not result.photos:
        print("nothing to export -- run 'turkart athlete sync' first", file=sys.stderr)
        return 1
    window = " ".join(filter(None, [
        f"from {after}" if after else "", f"to {args.before}" if args.before else ""]))
    print(f"{result.photos} photo(s){' ' + window if window else ''} -> {result.folder}/")
    print(f"  {result.thumbs_made} thumbnail(s) generated, {result.bytes / 1e6:.0f} MB total")
    if result.missing:
        print(f"  {result.missing} photo(s) had no file on disk and were skipped")
    print(f"  open {result.folder}/index.html")
    if args.open:
        import webbrowser

        webbrowser.open((result.folder / "index.html").resolve().as_uri())
    return 0


# -------------------------------------------------------------------- usage


def _usage_show(args, store: Store) -> int:
    from . import usage

    tally = usage.load(store)
    if not tally:
        print("no requests recorded yet")
        return 0
    for day in sorted(tally):
        n = tally[day]
        bar = "#" * min(40, round(n / usage.DAILY_BUDGET * 40))
        print(f"  {day}  {n:5d}  {bar}")
    print()
    print(usage.report(store, 0))
    print(f"\nStrava's non-upload budget is {usage.QUARTER_HOUR_BUDGET}/15min and "
          f"{usage.DAILY_BUDGET}/day, per application.")
    print("These endpoints send no rate-limit headers, so this tally is our own count,")
    print("and it only sees requests made through turkart.")
    return 0


# ------------------------------------------------------------------ explore


def _explore_build(args, store: Store) -> int:
    from .explore import build_rides, load_carto_key, summarise, write_html

    wanted = {int(r["id"]) for r in _select_activities(store, args)}
    # A merge collapses its members into the first id; keep the survivor when a
    # filter matched any member, or merged rides would vanish from the page.
    from .merge import load_merges

    for merge in load_merges(store):
        members = [int(m) for m in merge["members"]]
        if wanted & set(members):
            wanted.add(members[0])
    rides = build_rides(store, tolerance_m=args.tolerance, only_ids=wanted)
    if not rides:
        print("no rides with tracks on disk -- run 'turkart streams fetch' first")
        return 1

    carto_key = None if args.no_carto else load_carto_key()
    path = write_html(rides, args.output, carto_key=carto_key)
    stats = summarise(rides)
    raw_points = sum(r["points"] for r in rides)
    kept = sum(len(r["track"]) for r in rides)

    print(f"{stats['count']} rides, {stats['total_km']:.0f} km, {stats['first']} -> {stats['last']}")
    print(f"simplified {raw_points} points to {kept} ({kept / raw_points:.0%}) at {args.tolerance} m")
    print(f"\n{len(stats['clusters'])} map sheet(s) needed for the full set:")
    for i, group in enumerate(stats["clusters"], 1):
        width, height = group["span_km"]
        print(
            f"  {i}. {group['size']:3d} rides  {width:5.0f} x {height:<5.0f} km  "
            f"{group['first']}..{group['last']}  ({group['label'][:34]})"
        )
    basemap = "Carto Positron" if carto_key else "Esri Light Gray (no CARTO key)"
    print(f"\nwrote {path} ({path.stat().st_size / 1e6:.1f} MB), basemap: {basemap}")

    if args.serve:
        return _serve(path, args.serve, open_browser=args.open, store=store,
                      tolerance_m=args.tolerance)
    if args.open:
        import webbrowser

        webbrowser.open(path.resolve().as_uri())
    return 0


def _serve(path: Path, port: int, open_browser: bool, store: Store,
           tolerance_m: float = 8.0) -> int:
    """Serve the built page over localhost until interrupted.

    Falls back to a free port rather than dying on EADDRINUSE: the default 8000
    is a popular squat, and the exact port doesn't matter here -- only that the
    page has a real http origin so tile servers see a Referer.
    """
    import errno
    import functools
    import http.server
    import webbrowser

    from . import fetchapi

    class Handler(http.server.SimpleHTTPRequestHandler):
        """Static files, plus the small photo-fetch API the page calls."""

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            try:
                status, payload = fetchapi.handle(store, self.path, body, tolerance_m)
            except Exception as exc:  # never take the server down for one request
                status, payload = 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            # Quieten per-tile and per-photo GET noise; keep API calls visible.
            if self.command == "POST" or "api" in self.path:
                super().log_message(fmt, *args)

    handler = functools.partial(Handler, directory=str(path.parent.resolve()))

    httpd = None
    for candidate in (port, 0):
        try:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            print(f"port {candidate} is already in use, picking a free one...", file=sys.stderr)
    if httpd is None:
        print("could not bind a local port", file=sys.stderr)
        return 4

    with httpd:
        url = f"http://localhost:{httpd.server_address[1]}/{path.name}"
        # flush: this process then blocks in serve_forever, and a buffered
        # stdout (anything but a tty) would hide the URL for the whole run.
        print(f"serving {url}  (ctrl-c to stop)", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


# ------------------------------------------------------------------- helpers


def _select_activities(store: Store, args) -> list[dict]:
    """Apply the shared --after/--before/--tag/--sport-type filters to the index."""
    rows = list(store.load_activities().values())

    after, before = getattr(args, "after", None), getattr(args, "before", None)
    if after or before:
        rows = [r for r in rows if _in_range(_start_date(r), after, before)]

    # Strava tags live in the activity as {"<tag id>": true}; absent means untagged.
    for tag in getattr(args, "tag", None) or []:
        rows = [r for r in rows if (r.get("tags") or {}).get(str(tag)) is True]

    sport = getattr(args, "sport_type_filter", None)
    if sport:
        wanted = {s.lower() for s in sport}
        rows = [r for r in rows if (r.get("sport_type") or "").lower() in wanted]

    return sorted(rows, key=lambda r: r.get("start_date_local_raw") or 0)


def _in_range(day: date | None, after: date | None, before: date | None) -> bool:
    if day is None:
        return False
    if after and day < after:
        return False
    if before and day > before:
        return False
    return True


def _start_date(raw: dict) -> date | None:
    value = raw.get("start_date_local_raw") or raw.get("start_date_local")
    if value is None:
        return None
    # start_date_local_raw is the *local wall clock* already shifted into a unix
    # timestamp, so it must be read as UTC. Reading it in the machine's own zone
    # would shift it a second time and roll rides near midnight onto a wrong day.
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}")


def _format_raw(raw: dict) -> str:
    day = _start_date(raw)
    distance = raw.get("distance_raw")
    km = f"{float(distance) / 1000:6.1f}km" if distance else "      ?"
    return f"{raw['id']:<12} {day or '????-??-??'}  {km}  {raw.get('name', '')[:50]}"


def _format_activity(activity) -> str:
    return _format_raw(activity.raw)


_HANDLERS = {
    ("auth", "import"): _auth_import,
    ("auth", "status"): _auth_status,
    ("auth", "check"): _auth_check,
    ("activities", "sync"): _activities_sync,
    ("activities", "list"): _activities_list,
    ("streams", "fetch"): _streams_fetch,
    ("merge", "suggest"): _merge_suggest,
    ("merge", "apply"): _merge_apply,
    ("merge", "list"): _merge_list,
    ("merge", "remove"): _merge_remove,
    ("athlete", "sync"): _athlete_sync,
    ("athlete", "list"): _athlete_list,
    ("athlete", "export"): _athlete_export,
    ("usage", "show"): _usage_show,
    ("photos", "sync"): _photos_sync,
    ("photos", "list"): _photos_list,
    ("explore", "build"): _explore_build,
}


if __name__ == "__main__":
    sys.exit(main())
