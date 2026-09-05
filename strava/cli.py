"""Command line entry point.

    strava auth import LOCAL/strava_web.md   # lift cookies from a cURL capture
    strava auth status                       # what's stored, and how stale
    strava activities sync --sport-type Ride # build/refresh the ride index
    strava activities list --after 2026-01-01
    strava streams fetch --after 2026-01-01  # download tracks for those rides
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .client import DEFAULT_STREAM_TYPES, StravaClient, StravaError
from .session import BrowserSession, SessionError
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="strava", description=__doc__)
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
    psync.add_argument("--limit", type=int, default=0)
    psync.add_argument("--refresh", action="store_true",
                       help="re-scan activities already in the photo index")
    pho.add_parser("list", help="show what media is indexed")

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
    return 0


# --------------------------------------------------------------- activities


def _activities_sync(args, store: Store) -> int:
    client = StravaClient(BrowserSession.load())
    fetched: dict[str, dict] = {}
    for activity in client.iter_activities(
        sport_type=args.sport_type or None,
        keywords=args.keywords,
        tags=args.tags,
        per_page=args.per_page,
        max_pages=args.pages,
    ):
        fetched[str(activity.id)] = activity.raw
        print(f"\r  {len(fetched)} activities...", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)

    added, updated = store.merge_activities(fetched)
    print(f"fetched {len(fetched)} ({added} new, {updated} changed) -> {store.activities_file}")
    return 0


def _activities_list(args, store: Store) -> int:
    rows = _select_activities(store, args)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("no activities matched (run 'strava activities sync' first?)")
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
    if args.ids:
        targets = [{"id": i} for i in args.ids]
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
        print("re-run with --apply to save these, or: strava merge apply <id> <id>")
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


def _photos_sync(args, store: Store) -> int:
    from .photos import download, fetch_media, load_index, save_index

    targets = [{"id": i} for i in args.ids] if args.ids else _select_activities(store, args)
    targets = [t for t in targets if t.get("has_latlng") is not False]
    index = load_index(store)
    if not args.refresh:
        targets = [t for t in targets if str(t["id"]) not in index]
    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        print("nothing to scan (all activities already in the photo index)")
        return 0

    print(f"scanning {len(targets)} activities for media")
    client = StravaClient(BrowserSession.load())
    found = saved = videos = 0
    for n, target in enumerate(targets, 1):
        activity_id = int(target["id"])
        try:
            media = fetch_media(client, activity_id)
        except StravaError as exc:
            print(f"  [{n}/{len(targets)}] {activity_id} FAILED: {exc}", file=sys.stderr)
            continue

        index[str(activity_id)] = [
            {
                "photo_id": m.photo_id, "media_type": m.media_type,
                "caption": m.caption, "url": m.url, "video_url": m.video_url,
                "is_video": m.is_video,
            }
            for m in media
        ]
        if not media:
            continue
        found += len(media)
        for item in media:
            if item.is_video:
                videos += 1
                continue
            try:
                if download(client, item, store):
                    saved += 1
            except StravaError as exc:
                print(f"      photo {item.photo_id} failed: {exc}", file=sys.stderr)
        name = target.get("name", "")
        print(f"  [{n}/{len(targets)}] {activity_id} {name[:34]:34s} {len(media)} item(s)")

    save_index(store, index)
    print(f"\n{found} media item(s): {saved} photo(s) downloaded, {videos} video(s) indexed only")
    return 0


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
        print("no rides with tracks on disk -- run 'strava streams fetch' first")
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
        return _serve(path, args.serve, open_browser=args.open)
    if args.open:
        import webbrowser

        webbrowser.open(path.resolve().as_uri())
    return 0


def _serve(path: Path, port: int, open_browser: bool) -> int:
    """Serve the built page over localhost until interrupted.

    Falls back to a free port rather than dying on EADDRINUSE: the default 8000
    is a popular squat, and the exact port doesn't matter here -- only that the
    page has a real http origin so tile servers see a Referer.
    """
    import errno
    import functools
    import http.server
    import webbrowser

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(path.parent.resolve())
    )

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
    ("photos", "sync"): _photos_sync,
    ("photos", "list"): _photos_list,
    ("explore", "build"): _explore_build,
}


if __name__ == "__main__":
    sys.exit(main())
