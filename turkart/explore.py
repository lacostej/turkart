"""Build the local ride browser.

Generates a single self-contained HTML file: every simplified track is embedded
in it, so it opens straight off the filesystem with no server and no upload of
your GPS traces anywhere. Use it to pick the rides that belong on the poster,
then export the selection for the render step.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .geo import bounds, cluster, haversine_km, simplify_indices, span_km
from .merge import apply_to
from .photos import load_index as load_photo_index
from .store import Store

DEFAULT_OUTPUT = Path("build/explore.html")

# CARTO's basemaps need an API key; without one their tiles come back watermarked
# "API KEY REQUIRED" (at HTTP 200, so only the image itself gives it away).
CARTO_KEY_FILE = Path(".secrets/carto")


def load_carto_key() -> str | None:
    """CARTO key from $CARTO_KEY or .secrets/carto, if either is set."""
    import os

    key = os.environ.get("CARTO_KEY")
    if key:
        return key.strip()
    if CARTO_KEY_FILE.exists():
        return CARTO_KEY_FILE.read_text().strip() or None
    return None


def build_rides(
    store: Store,
    tolerance_m: float = 8.0,
    only_ids: set[int] | None = None,
) -> list[dict]:
    """Join the activity index with the stored tracks into render-ready records.

    Each simplified track point is ``[lat, lng, metres_along, altitude_m]``: the
    other streams are carried through the simplification by index, so any later
    stage can read distance and elevation for a sub-range of a ride without
    reloading the full-resolution streams.
    """
    # Saved merges are folded in here rather than on disk, so the original
    # per-activity streams stay intact and a merge stays reversible.
    activities, merged = apply_to(store, store.load_activities())
    photo_index = load_photo_index(store)
    rides: list[dict] = []

    for key, raw in activities.items():
        if only_ids is not None and int(raw["id"]) not in only_ids:
            continue
        if int(raw["id"]) in merged:
            streams = merged[int(raw["id"])]
        elif store.has_streams(key):
            streams = store.load_streams(key)
        else:
            continue
        latlng = streams.get("latlng") or []
        if not latlng:
            continue

        indices = simplify_indices(latlng, tolerance_m)
        dist = streams.get("distance") or []
        alt = streams.get("altitude") or []
        track = [
            [
                round(latlng[i][0], 5),
                round(latlng[i][1], 5),
                round(dist[i]) if i < len(dist) else 0,
                round(alt[i]) if i < len(alt) else 0,
            ]
            for i in indices
        ]
        box = bounds([latlng])
        altitude = streams.get("altitude") or []

        # Every ride leaves from home, so a label pinned to the start point would
        # stack all of them on one pixel. The point furthest from the start is
        # the turnaround -- distinct per ride, and usually where the long stop
        # was, which is the part of the ride worth naming.
        start = latlng[0]
        far_i = max(range(len(latlng)), key=lambda i: haversine_km(start, latlng[i]))
        far_km = haversine_km(start, latlng[far_i])
        ts = raw.get("start_date_local_raw")
        start = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None

        # A merged ride inherits the media of every activity it absorbed. Paths
        # stay relative to the built page, which reaches data/photos via a
        # symlink, so the page works over both file:// and --serve.
        photos = []
        for member in (raw.get("merged_from") or [raw["id"]]):
            for item in photo_index.get(str(member), []):
                if item.get("is_video"):
                    continue
                rel = f"photos/{member}/{item['photo_id']}.jpg"
                if (store.root / rel).exists():
                    # `at` is where the photo was taken; a few have no fix, and
                    # those simply get no leader line back to the route.
                    at = (
                        [round(item["lat"], 6), round(item["lng"], 6)]
                        if item.get("lat") is not None and item.get("lng") is not None
                        else None
                    )
                    photos.append(
                        {"id": item["photo_id"], "src": rel,
                         "caption": item.get("caption") or "", "at": at,
                         "w": item.get("width"), "h": item.get("height")}
                    )

        rides.append(
            {
                "id": raw["id"],
                "photos": photos,
                "name": raw.get("name") or "(untitled)",
                "date": start.strftime("%Y-%m-%d") if start else None,
                "time": start.strftime("%H:%M") if start else None,
                "ts": ts,
                "km": round((raw.get("distance_raw") or 0) / 1000.0, 2),
                "elev": round(raw.get("elevation_gain_raw") or 0),
                "moving_s": raw.get("moving_time_raw") or 0,
                "commute": bool(raw.get("commute")),
                # Strava stores tags as {"<id>": bool}; keep only the set ones.
                "tags": sorted(
                    int(k) for k, v in (raw.get("tags") or {}).items() if v
                ),
                "sport": raw.get("sport_type") or "",
                "desc": (raw.get("description") or "")[:300],
                "url": f"https://www.strava.com/activities/{raw['id']}",
                "points": len(latlng),
                "merged_from": raw.get("merged_from") or [],
                "centre": [
                    round((box[0] + box[2]) / 2, 5),
                    round((box[1] + box[3]) / 2, 5),
                ],
                "bbox": [round(v, 5) for v in box],
                "alt_range": (
                    [round(min(altitude)), round(max(altitude))] if altitude else None
                ),
                "label_at": [round(latlng[far_i][0], 5), round(latlng[far_i][1], 5)],
                "far_km": round(far_km, 2),
                "track": track,
            }
        )

    rides.sort(key=lambda r: r["ts"] or 0)
    return rides


def summarise(rides: list[dict], max_gap_km: float = 40.0) -> dict:
    """Overview stats, including how many separate map sheets the set implies.

    Extent is reported per cluster, not globally: one ride on another continent
    makes the overall bounding box thousands of km wide, which says nothing about
    the sheet each group of rides actually needs.
    """
    if not rides:
        return {"count": 0}

    groups = cluster([r["centre"] for r in rides], max_gap_km)
    clusters = []
    for group in groups:
        members = [rides[i] for i in group]
        width, height = span_km(bounds([r["track"] for r in members]))
        clusters.append(
            {
                "size": len(members),
                "ids": [r["id"] for r in members],
                "span_km": [round(width, 1), round(height, 1)],
                "km": round(sum(r["km"] for r in members), 1),
                "first": min(r["date"] for r in members if r["date"]),
                "last": max(r["date"] for r in members if r["date"]),
                # A human-readable handle for the sheet: the longest ride in it.
                "label": max(members, key=lambda r: r["km"])["name"],
            }
        )

    return {
        "count": len(rides),
        "total_km": round(sum(r["km"] for r in rides), 1),
        "first": rides[0]["date"],
        "last": rides[-1]["date"],
        "clusters": clusters,
    }


def write_html(
    rides: list[dict],
    output: Path = DEFAULT_OUTPUT,
    carto_key: str | None = None,
) -> Path:
    """Render the page. The CARTO key is baked into the output, so the built
    file is a secret-bearing artefact -- build/ is gitignored for that reason."""
    output.parent.mkdir(parents=True, exist_ok=True)
    _link_photos(output.parent)
    html = _TEMPLATE.replace("__RIDES__", json.dumps(rides, separators=(",", ":")))
    html = html.replace("__CARTO_KEY__", json.dumps(carto_key))
    output.write_text(html, encoding="utf-8")
    return output


def _link_photos(build_dir: Path, photos_dir: Path = Path("data/photos")) -> None:
    """Point <build>/photos at the downloaded media.

    A symlink rather than a copy: the library is tens of megabytes and would
    otherwise be duplicated on every build.
    """
    link = build_dir / "photos"
    target = photos_dir.resolve()
    if not target.exists():
        return
    if link.is_symlink():
        if link.resolve() == target:
            return
        link.unlink()
    elif link.exists():
        return  # a real directory is there; leave it alone
    link.symlink_to(target)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>turkart — ride selector</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {
    --bg:#f7f7f5; --panel:#fff; --ink:#1b1b1a; --muted:#6b6b66;
    --line:#e2e2dd; --accent:#e2562a; --accent-soft:#fdece6;
  }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         color:var(--ink); background:var(--bg); height:100vh; display:flex; }
  #sidebar { width:440px; flex:none; background:var(--panel); border-right:1px solid var(--line);
             display:flex; flex-direction:column; height:100vh; }
  #map { flex:1; height:100vh; }
  header { padding:12px 16px 10px; border-bottom:1px solid var(--line); }
  h1 { margin:0 0 9px; font-size:15px; letter-spacing:.02em; text-transform:uppercase; }
  .setbar { display:flex; gap:5px; margin-bottom:10px; align-items:center; }
  .setbar select { flex:1; min-width:0; padding:5px 7px; border:1px solid var(--line);
                   border-radius:4px; font:inherit; background:#fff; }
  .filters { display:grid; grid-template-columns:1fr 1fr; gap:6px; }
  .filters input { width:100%; padding:5px 7px; border:1px solid var(--line);
                   border-radius:4px; font:inherit; background:#fff; }
  .filters label { grid-column:span 2; font-size:11px; color:var(--muted);
                   text-transform:uppercase; letter-spacing:.05em; margin-top:4px; }
  .toggles { grid-column:span 2; display:flex; gap:14px; }
  .tog { display:flex; align-items:center; gap:5px; font-size:12px; color:var(--ink);
         text-transform:none; letter-spacing:0; margin:0; cursor:pointer; }
  .tog input { margin:0; }
  .kid { font-size:10px; font-weight:700; color:#7a4a1e; background:#ffe8cf;
         border-radius:3px; padding:0 4px; margin-left:5px; vertical-align:1px; }
  .btnrow { display:flex; gap:6px; flex-wrap:wrap; padding:9px 16px; border-bottom:1px solid var(--line); }
  button { font:inherit; font-size:12px; padding:5px 10px; border:1px solid var(--line);
           background:#fff; border-radius:4px; cursor:pointer; }
  button:hover { background:var(--accent-soft); border-color:var(--accent); }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  button.icon { padding:5px 8px; }
  #stats { padding:9px 16px; font-size:12px; color:var(--muted); border-bottom:1px solid var(--line);
           background:#fbfbfa; }
  #stats b { color:var(--ink); }
  #stats .warn { color:var(--accent); font-weight:600; }
  #list { flex:1; overflow-y:auto; }
  .ride { display:flex; gap:9px; padding:7px 16px; cursor:pointer; align-items:baseline; }
  .ride:hover { background:var(--accent-soft); }
  .ride.on { background:#fff6f2; }
  .rideblock { border-bottom:1px solid #f0f0ec; }
  .ride input { margin:0; flex:none; position:relative; top:2px; }
  .ride .meta { flex:1; min-width:0; }
  .ride .nm { display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .ride.on .nm { font-weight:600; }
  .ride .sub { font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .swatch { width:9px; height:9px; border-radius:50%; flex:none; position:relative; top:4px; }
  .strip { display:flex; gap:5px; overflow-x:auto; padding:0 16px 8px 50px; }
  .strip img { width:46px; height:46px; object-fit:cover; border-radius:4px; cursor:pointer;
               border:2px solid transparent; opacity:.5; flex:none; }
  .strip img.on { opacity:1; border-color:var(--accent); }
  .strip img:hover { opacity:1; }
  #preview { position:fixed; z-index:2000; pointer-events:none; display:none;
             border:3px solid #fff; border-radius:5px; box-shadow:0 6px 24px rgba(0,0,0,.45);
             max-width:340px; max-height:340px; background:#fff; }
  .photo-pin .grip { position:absolute; right:-5px; bottom:-5px; width:14px; height:14px;
                     background:#fff; border:2px solid var(--accent); border-radius:50%;
                     cursor:nwse-resize; box-shadow:0 1px 3px rgba(0,0,0,.4); }
  .strip .cnt { font-size:10px; color:var(--muted); align-self:center; white-space:nowrap; }
  #export { padding:9px 16px; border-top:1px solid var(--line); background:#fbfbfa; }
  #importMsg { font-size:11px; margin-top:6px; color:var(--muted); min-height:0; }
  #importMsg.bad { color:var(--accent); }
  #export textarea { width:100%; height:50px; font:11px/1.4 ui-monospace, Menlo, monospace;
                     border:1px solid var(--line); border-radius:4px; padding:6px; resize:vertical; }
  .rank-pin { background:none !important; border:none !important; }
  .rank-pin span { display:flex; align-items:center; justify-content:center;
                   width:22px; height:22px; border-radius:50%; color:#fff;
                   font-weight:700; font-size:11px; border:2px solid #fff;
                   box-shadow:0 1px 3px rgba(0,0,0,.4); transition:transform .12s; }
  .rank-pin.hot span { transform:scale(1.55); }
  .photo-pin { background:none !important; border:none !important; cursor:grab; }
  .photo-pin img { width:100%; height:100%; object-fit:cover; border-radius:3px;
                   border:3px solid #fff; box-shadow:0 2px 6px rgba(0,0,0,.45); display:block; }
  .photo-pin.dragging { cursor:grabbing; }
  /* Legend: a card over the map, so one screenshot captures both. */
  #legend { position:absolute; z-index:1100; left:24px; top:24px; width:310px;
            background:rgba(255,255,255,.96); border-radius:8px; padding:16px 18px 14px;
            box-shadow:0 4px 22px rgba(0,0,0,.22); font-size:13px; }
  #legend h2 { margin:0 0 2px; font-size:19px; line-height:1.25; font-weight:700;
               letter-spacing:-.01em; outline:none; }
  #legend h2:empty::before { content:'Click to add a title'; color:#b9b9b2; }
  #legend .period { margin:0 0 11px; font-size:11px; color:var(--muted);
                    text-transform:uppercase; letter-spacing:.07em; }
  #legend ol { list-style:none; margin:0; padding:0; }
  #legend li { display:flex; gap:9px; align-items:baseline; padding:3px 0; }
  #legend .num { flex:none; width:20px; height:20px; border-radius:50%; color:#fff;
                 font-size:11px; font-weight:700; display:flex; align-items:center;
                 justify-content:center; position:relative; top:3px; }
  #legend .nm { flex:1; min-width:0; }
  #legend .fig { color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap;
                 font-size:12px; }
  #legend .tot { margin-top:10px; padding-top:8px; border-top:1px solid var(--line);
                 font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; }
  #legend .grab { position:absolute; inset:0 0 auto 0; height:14px; cursor:grab; }
  body.legendmode #sidebar { display:none; }
  body.legendmode #zoomout { display:none; }
  /* In legend mode the map is the artwork, so the chrome gets out of the way.
     Attribution stays: OSM and CARTO both require it, and a screenshot without
     it is not licensed for sharing. */
  body.legendmode .leaflet-control-zoom,
  body.legendmode .leaflet-control-layers,
  body.legendmode #legendbar {
    opacity:0; pointer-events:none; transition:opacity .12s;
  }
  body.legendmode.chrome .leaflet-control-zoom,
  body.legendmode.chrome .leaflet-control-layers,
  body.legendmode.chrome #legendbar {
    opacity:1; pointer-events:auto;
  }
  /* The resize grip is an editing affordance, not part of the artwork, so it
     goes with the rest of the chrome -- hidden in legend view, back while the
     reveal key is held. Photos stay draggable either way, for fine positioning. */
  body.legendmode .photo-pin .grip { display:none; }
  body.legendmode.chrome .photo-pin .grip { display:block; }
  #legendbar { display:none; position:absolute; z-index:1101; right:10px; top:10px; gap:6px; }
  body.legendmode #legendbar { display:flex; }
  #legendbar button { background:rgba(255,255,255,.95); padding:4px 9px; line-height:1.1; }
  #chromehint { position:absolute; z-index:1102; left:50%; transform:translateX(-50%);
                bottom:26px; background:rgba(27,27,26,.82); color:#fff; font-size:12px;
                padding:5px 12px; border-radius:13px; pointer-events:none;
                opacity:0; transition:opacity .4s; }
  #chromehint.show { opacity:1; }
  #zoomout { position:absolute; z-index:500; right:10px; bottom:22px; background:#fff;
             border:1px solid var(--line); border-radius:4px; padding:3px 7px;
             font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }
</style>
</head>
<body>
<div id="sidebar">
  <header>
    <h1>turkart</h1>
    <div class="setbar">
      <select id="setPicker" title="saved selections"></select>
      <button class="icon" id="setNew" title="new selection">+</button>
      <button class="icon" id="setDup" title="duplicate">&#10697;</button>
      <button class="icon" id="setRen" title="rename">&#9998;</button>
      <button class="icon" id="setDel" title="delete">&#215;</button>
    </div>
    <div class="filters">
      <label>Date range</label>
      <input type="date" id="from"><input type="date" id="to">
      <label>Search name / description</label>
      <input type="search" id="q" placeholder="e.g. Tryvann" style="grid-column:span 2">
      <label>Distance km (min / max)</label>
      <input type="number" id="dmin" placeholder="0"><input type="number" id="dmax" placeholder="any">
      <label style="margin-top:8px">Tags</label>
      <div class="toggles">
        <label class="tog"><input type="checkbox" id="kidsOnly"> With kids only</label>
        <label class="tog"><input type="checkbox" id="ridesOnly"> Rides only</label>
      </div>
    </div>
  </header>
  <div class="btnrow">
    <button id="selVisible">Select visible</button>
    <button id="clear">Clear</button>
    <button id="invert">Invert</button>
    <button id="zoomSel">Zoom to selection</button>
    <button id="ghosts">Show unselected</button>
    <button id="leaders">Hide photo lines</button>
    <button id="legendOn">Legend view</button>
    <label class="tog" style="margin-left:auto">size
      <input type="range" id="psize" min="40" max="220" step="4" style="width:90px">
    </label>
  </div>
  <div id="stats"></div>
  <div id="list"></div>
  <div id="export">
    <textarea id="out" spellcheck="false"
              title="the current selection as JSON -- paste one in and press Import"></textarea>
    <div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap">
      <button class="primary" id="copy">Copy JSON</button>
      <button id="download">Save</button>
      <button id="importText">Import pasted</button>
      <button id="importFile">Load file…</button>
      <button id="resetPhotos">Reset photo positions</button>
    </div>
    <div id="importMsg"></div>
    <input type="file" id="filePicker" accept="application/json,.json" hidden>
  </div>
</div>
<div id="map">
  <div id="zoomout"></div>
  <div id="legend" hidden>
    <div class="grab" id="legendGrab"></div>
    <h2 id="legendTitle" contenteditable="true" spellcheck="false"></h2>
    <p class="period" id="legendPeriod"></p>
    <ol id="legendList"></ol>
    <div class="tot" id="legendTotals"></div>
  </div>
  <div id="legendbar">
    <button id="legendBack" title="Back to selector">&#8592;</button>
  </div>
  <div id="chromehint">hold &#8679; or &#8997; to show controls</div>
</div>
<img id="preview" alt="">

<script>
const RIDES = __RIDES__;
const byId = new Map(RIDES.map(r => [r.id, r]));
const STORAGE = 'turkart-selections';
// Older key names, newest first. Migrated into STORAGE once at load and then
// deleted, so this pair can be removed outright after one page load.
const LEGACY_SETS = 'strava-poster-v2';        // named sets, pre-rename
const LEGACY_FLAT = 'strava-poster-selection'; // a bare id array, before named sets
const TAG_WITH_KIDS = 16;

// ---------------------------------------------------------------- state
// Multiple named selections. Each holds the chosen ride ids in order, plus
// per-photo map positions, so a selection fully describes one poster layout.
function blankSet(ids) {
  return { ids: ids || [], photos: {}, leaders: true, size: 72,
           title: '', legendPos: null };
}

// A placement is {pos:[lat,lng], size:px}. Early builds stored a bare [lat,lng],
// so normalise on read rather than forcing anyone to redo their layout.
function placement(value, fallbackSize) {
  if (Array.isArray(value)) return { pos: value, size: fallbackSize };
  return { pos: value.pos, size: value.size || fallbackSize };
}

function readKey(key) {
  try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (e) { return null; }
}

function dropKeys(...keys) {
  for (const key of keys) {
    try { localStorage.removeItem(key); } catch (e) {}
  }
}

function loadState() {
  const current = readKey(STORAGE);
  if (current && current.sets) return current;

  // Migrate from the pre-rename key, then drop it so this only happens once.
  const previous = readKey(LEGACY_SETS);
  if (previous && previous.sets) {
    try { localStorage.setItem(STORAGE, JSON.stringify(previous)); } catch (e) {}
    dropKeys(LEGACY_SETS, LEGACY_FLAT);
    return previous;
  }

  // Older still: a bare array of ids, from before selections had names.
  const flat = readKey(LEGACY_FLAT);
  const ids = Array.isArray(flat) ? flat.filter(i => byId.has(i)) : [];
  const state = { active: 'Selection 1', sets: { 'Selection 1': blankSet(ids) } };
  if (ids.length) {
    try { localStorage.setItem(STORAGE, JSON.stringify(state)); } catch (e) {}
    dropKeys(LEGACY_FLAT);
  }
  return state;
}

let state = loadState();
function cur() {
  const set = state.sets[state.active] || (state.sets[state.active] = blankSet());
  if (set.leaders === undefined) set.leaders = true;
  if (set.title === undefined) set.title = '';
  if (!set.size) set.size = 72;
  return set;
}
function persist() {
  try { localStorage.setItem(STORAGE, JSON.stringify(state)); } catch (e) {}
}
function selectedSet() { return new Set(cur().ids); }

let showGhosts = false;
const drawn = new Map();          // ride id -> {line, pin}
const photoMarkers = new Map();   // "rideId/photoId" -> marker
const stripScroll = new Map();    // ride id -> horizontal scroll of its photo strip
let pinState = [];                // {id, marker, truePos, colour} for pin de-collision

// ---------------------------------------------------------------- map
const map = L.map('map', { preferCanvas: true, zoomSnap: 0.25, zoomDelta: 0.25, maxZoom: 20 });

// Basemap choice is a real constraint, not a preference:
//   - CARTO needs an API key; without one its tiles come back watermarked
//     "API KEY REQUIRED" at HTTP 200, so only the image itself reveals it.
//   - osm.org blocks requests with no Referer, which is what a file:// page
//     sends; its layer works under `--serve`.
const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services';
const basemaps = {
  'Esri Light Gray': L.tileLayer(ESRI + '/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 16, maxNativeZoom: 16, referrerPolicy: 'origin',
    attribution: '&copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors' }),
  'Esri Topo': L.tileLayer(ESRI + '/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19, referrerPolicy: 'origin', attribution: '&copy; Esri' }),
  'CyclOSM': L.tileLayer('https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png', {
    subdomains: 'abc', maxZoom: 20, referrerPolicy: 'origin',
    attribution: 'CyclOSM | ' + OSM_ATTR }),
  'OSM': L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, referrerPolicy: 'origin', attribution: OSM_ATTR }),
  'OpenTopoMap': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    subdomains: 'abc', maxZoom: 17, referrerPolicy: 'origin',
    attribution: OSM_ATTR + ', SRTM | &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)' }),
};
const CARTO_KEY = __CARTO_KEY__;
const CARTO_ATTR = OSM_ATTR + ', &copy; <a href="https://carto.com/attributions">CARTO</a>';
if (CARTO_KEY) {
  const carto = (style) => L.tileLayer(
    `https://{s}.basemaps.cartocdn.com/${style}/{z}/{x}/{y}{r}.png?key=${CARTO_KEY}`,
    { subdomains: 'abcd', maxZoom: 20, referrerPolicy: 'origin', attribution: CARTO_ATTR });
  basemaps['Carto Positron'] = carto('light_all');
  basemaps['Carto Voyager'] = carto('rastertiles/voyager');
  basemaps['Carto Dark'] = carto('dark_all');
}
const defaultLayer = CARTO_KEY ? 'Carto Positron' : 'Esri Light Gray';
basemaps[defaultLayer].addTo(map);
L.control.layers(basemaps, null, { position: 'topright' }).addTo(map);

const ghostLayer = L.layerGroup().addTo(map);
const trackLayer = L.layerGroup().addTo(map);
const pinLinkLayer = L.layerGroup().addTo(map);
const photoLayer = L.layerGroup().addTo(map);

// Track points are [lat, lng, metres_along, altitude_m].
const latlngs = r => r.track.map(p => [p[0], p[1]]);
function colour(i, n) { return `hsl(${Math.round((i * 360) / Math.max(n, 1))} 72% 45%)`; }
function fmtDuration(s) {
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h}h${String(m).padStart(2, '0')}` : `${m}min`;
}
const previewEl = document.getElementById('preview');
function showPreview(photo, anchor) {
  previewEl.src = photo.src;
  previewEl.style.display = 'block';
  const box = anchor.getBoundingClientRect();
  // Sit beside the sidebar, vertically centred on the thumbnail, clamped to
  // the viewport so a photo near the bottom is still fully visible.
  const top = Math.min(Math.max(8, box.top + box.height / 2 - 170), window.innerHeight - 348);
  previewEl.style.left = (box.right + 12) + 'px';
  previewEl.style.top = top + 'px';
}
function hidePreview() { previewEl.style.display = 'none'; }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------------------------------------------------------------- filtering
const kidsOnly = document.getElementById('kidsOnly');
const ridesOnly = document.getElementById('ridesOnly');

function visibleRides() {
  const from = document.getElementById('from').value;
  const to = document.getElementById('to').value;
  const q = document.getElementById('q').value.trim().toLowerCase();
  const dmin = parseFloat(document.getElementById('dmin').value);
  const dmax = parseFloat(document.getElementById('dmax').value);
  return RIDES.filter(r => {
    if (from && (!r.date || r.date < from)) return false;
    if (to && (!r.date || r.date > to)) return false;
    if (!isNaN(dmin) && r.km < dmin) return false;
    if (!isNaN(dmax) && r.km > dmax) return false;
    if (q && !(`${r.name} ${r.desc}`.toLowerCase().includes(q))) return false;
    if (kidsOnly.checked && !(r.tags || []).includes(TAG_WITH_KIDS)) return false;
    if (ridesOnly.checked && r.sport !== 'Ride') return false;
    return true;
  });
}
function selectedRides() {
  const order = cur().ids;
  return order.map(id => byId.get(id)).filter(Boolean);
}

function haversineKm(a, b) {
  const R = 6371, rad = Math.PI / 180;
  const dLat = (b[0] - a[0]) * rad, dLon = (b[1] - a[1]) * rad;
  const h = Math.sin(dLat / 2) ** 2 +
            Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
function clusterCount(rides, gapKm = 40) {
  const parent = rides.map((_, i) => i);
  const find = i => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  for (let i = 0; i < rides.length; i++)
    for (let j = i + 1; j < rides.length; j++)
      if (haversineKm(rides[i].centre, rides[j].centre) <= gapKm) parent[find(i)] = find(j);
  return new Set(rides.map((_, i) => find(i))).size;
}

// ---------------------------------------------------------------- selection
function toggleRide(id) {
  const ids = cur().ids;
  const at = ids.indexOf(id);
  if (at >= 0) {
    ids.splice(at, 1);
    delete cur().photos[id];   // drop placements with the ride
  } else {
    ids.push(id);
  }
  persist(); render();
}

// A photo's default position is its ride's turnaround, fanned out slightly so
// several photos from one ride do not land exactly on top of each other.
function defaultPos(ride, index) {
  const step = 0.0016 * (index + 1);
  const angle = index * 2.399;  // golden angle, keeps a tidy spiral
  return [ride.label_at[0] + step * Math.cos(angle),
          ride.label_at[1] + step * Math.sin(angle) * 1.9];
}

function togglePhoto(ride, photo, index) {
  const photos = cur().photos;
  const forRide = photos[ride.id] || (photos[ride.id] = {});
  if (forRide[photo.id]) delete forRide[photo.id];
  // Start a geolocated photo at its own coordinates; the leader line is then
  // zero-length until it is dragged clear of the route.
  else forRide[photo.id] = { pos: photo.at || defaultPos(ride, index), size: cur().size };
  if (!Object.keys(forRide).length) delete photos[ride.id];
  persist(); render();
}

// Keep the photo's aspect ratio: `size` is the long edge in pixels.
function photoBox(photo, size) {
  const w = photo.w || 4, h = photo.h || 3;
  return w >= h ? [size, Math.round(size * h / w)] : [Math.round(size * w / h), size];
}

function highlight(id, on) {
  const d = drawn.get(id);
  if (!d) return;
  d.line.setStyle({ weight: on ? 7 : 3, opacity: on ? 1 : .9 });
  if (on) d.line.bringToFront();
  const el = d.pin.getElement();
  if (el) el.classList.toggle('hot', on);
}

// ---------------------------------------------------------------- render
function renderSets() {
  const picker = document.getElementById('setPicker');
  picker.innerHTML = '';
  for (const name of Object.keys(state.sets)) {
    const o = document.createElement('option');
    o.value = name; o.textContent = `${name} (${state.sets[name].ids.length})`;
    if (name === state.active) o.selected = true;
    picker.appendChild(o);
  }
}

function render() {
  renderSets();
  const vis = visibleRides();
  const sel = selectedRides();
  const chosen = selectedSet();
  const rank = new Map(sel.map((r, i) => [r.id, i]));

  // ---- list
  const list = document.getElementById('list');
  const listTop = list.scrollTop;
  const restore = [];
  list.innerHTML = '';
  for (const r of vis) {
    const on = chosen.has(r.id);
    const block = document.createElement('div');
    block.className = 'rideblock';
    const row = document.createElement('div');
    row.className = 'ride' + (on ? ' on' : '');
    const col = on ? colour(rank.get(r.id), sel.length) : '#d9d9d4';
    row.innerHTML = `
      <input type="checkbox" ${on ? 'checked' : ''}>
      <span class="swatch" style="background:${col}"></span>
      <span class="meta">
        <span class="nm">${on ? (rank.get(r.id) + 1) + '. ' : ''}${escapeHtml(r.name)}${
          (r.tags || []).includes(TAG_WITH_KIDS) ? '<span class="kid">KIDS</span>' : ''}</span>
        <span class="sub">${r.date} · ${r.km.toFixed(1)} km · ${r.elev} m · ${fmtDuration(r.moving_s)}${
          r.photos.length ? ' · ' + r.photos.length + ' photo' + (r.photos.length > 1 ? 's' : '') : ''}</span>
      </span>`;
    row.onmouseenter = () => highlight(r.id, true);
    row.onmouseleave = () => highlight(r.id, false);
    row.onclick = () => toggleRide(r.id);
    block.appendChild(row);

    // Photo strip, only for chosen rides -- picking photos for a ride that is
    // not on the poster has nowhere to show them.
    if (on && r.photos.length) {
      const strip = document.createElement('div');
      strip.className = 'strip';
      const placed = (cur().photos[r.id]) || {};
      r.photos.forEach((ph, i) => {
        const img = document.createElement('img');
        img.src = ph.src;
        img.loading = 'lazy';
        img.title = ph.caption || 'click to place on map';
        if (placed[ph.id]) img.classList.add('on');
        img.onclick = e => { e.stopPropagation(); togglePhoto(r, ph, i); };
        // Hovering shows the photo large, so browsing does not mean placing and
        // unplacing just to see what a thumbnail actually is.
        img.onmouseenter = e => showPreview(ph, e.currentTarget);
        img.onmouseleave = hidePreview;
        strip.appendChild(img);
      });
      const cnt = document.createElement('span');
      cnt.className = 'cnt';
      cnt.textContent = `${Object.keys(placed).length}/${r.photos.length} placed`;
      strip.appendChild(cnt);
      // A re-render rebuilds these nodes, which would otherwise snap the strip
      // back to the left every time a photo is clicked.
      strip.dataset.ride = r.id;
      strip.addEventListener('scroll', () => { stripScroll.set(r.id, strip.scrollLeft); });
      block.appendChild(strip);
      if (stripScroll.has(r.id)) restore.push([strip, stripScroll.get(r.id)]);
    }
    list.appendChild(block);
  }
  // Re-apply scroll offsets after the new nodes are in the document.
  list.scrollTop = listTop;
  for (const [el, left] of restore) el.scrollLeft = left;

  // ---- map
  trackLayer.clearLayers();
  ghostLayer.clearLayers();
  pinLinkLayer.clearLayers();
  photoLayer.clearLayers();
  drawn.clear();
  photoMarkers.clear();
  pinState = [];

  if (showGhosts) {
    for (const r of vis) {
      if (chosen.has(r.id)) continue;
      L.polyline(latlngs(r), { color: '#9a9a94', weight: 1, opacity: .45 }).addTo(ghostLayer);
    }
  }

  sel.forEach((r, i) => {
    const col = colour(i, sel.length);
    const line = L.polyline(latlngs(r), { color: col, weight: 3, opacity: .9 }).addTo(trackLayer);
    const popup =
      `<b>${i + 1}. ${escapeHtml(r.name)}</b><br>${r.date} ${r.time || ''}<br>` +
      `${r.km.toFixed(1)} km · ${r.elev} m · ${fmtDuration(r.moving_s)}<br>` +
      `turnaround ${r.far_km.toFixed(1)} km from start<br>` +
      `<a href="${r.url}" target="_blank">open in Strava</a>`;
    line.bindPopup(popup);
    const pin = L.marker(r.label_at, {
      icon: L.divIcon({ className: 'rank-pin', iconSize: [22, 22], iconAnchor: [11, 11],
                        html: `<span style="background:${col}">${i + 1}</span>` }),
      riseOnHover: true, zIndexOffset: 1000,
    }).addTo(trackLayer);
    pin.bindPopup(popup);
    drawn.set(r.id, { line, pin });
    pinState.push({ id: r.id, marker: pin, truePos: r.label_at, colour: col });

    // Placed photos: draggable, resizable, each tied back to where it was taken.
    const placed = cur().photos[r.id] || {};
    for (const [photoId, stored] of Object.entries(placed)) {
      const photo = r.photos.find(p => p.id === photoId);
      if (!photo) continue;
      const place = placement(stored, cur().size);
      const [pw, ph] = photoBox(photo, place.size);

      // Leader line back to the photo's own coordinates. Photos with no GPS fix
      // get no line -- there is nowhere truthful to point it.
      let leader = null, anchor = null;
      if (photo.at && cur().leaders) {
        leader = L.polyline([place.pos, photo.at], {
          color: col, weight: 1.5, opacity: .85, dashArray: '4,3', interactive: false,
        }).addTo(photoLayer);
        anchor = L.circleMarker(photo.at, {
          radius: 3, color: col, fillColor: '#fff', fillOpacity: 1, weight: 2, interactive: false,
        }).addTo(photoLayer);
      }

      const marker = L.marker(place.pos, {
        draggable: true, zIndexOffset: 2000,
        icon: L.divIcon({
          className: 'photo-pin', iconSize: [pw, ph], iconAnchor: [pw / 2, ph / 2],
          html: `<img src="${photo.src}" style="border-color:${col}"><div class="grip"></div>`,
        }),
      }).addTo(photoLayer);

      const save = () => {
        persist();
        const outEl = document.getElementById('out');
        if (document.activeElement !== outEl) outEl.value = exportJson();
      };
      marker.on('dragstart', () => marker.getElement()?.classList.add('dragging'));
      marker.on('drag', () => { if (leader) leader.setLatLngs([marker.getLatLng(), photo.at]); });
      marker.on('dragend', () => {
        marker.getElement()?.classList.remove('dragging');
        const ll = marker.getLatLng();
        // Keep `place` in step with the store. A drag does not re-render, so a
        // stale place.pos here is what later made a resize snap the photo back
        // to wherever it sat before the drag.
        place.pos = [+ll.lat.toFixed(6), +ll.lng.toFixed(6)];
        cur().photos[r.id][photoId] = { pos: place.pos, size: place.size };
        save();
      });
      marker.bindPopup(
        `<img src="${photo.src}" style="max-width:260px;display:block;margin-bottom:6px">` +
        `${escapeHtml(photo.caption || r.name)}<br>` +
        `<small>drag to move · corner grip to resize` +
        `${photo.at ? '' : ' · no GPS fix, so no line'}</small>`);

      // Corner grip resizes. Pointer events are taken off the map so a drag here
      // never pans it, and the size is written straight into the placement.
      const el = marker.getElement();
      const grip = el && el.querySelector('.grip');
      if (grip) {
        // Leaflet binds marker dragging to `mousedown` (and `touchstart`), not
        // `pointerdown`. Stopping only pointerdown left mousedown to bubble to
        // the marker, so dragging the grip moved the photo instead of resizing
        // it. Every start event has to be stopped, not just the one we act on.
        for (const evt of ['mousedown', 'touchstart', 'dblclick']) {
          grip.addEventListener(evt, e => { e.preventDefault(); e.stopPropagation(); });
        }
        grip.addEventListener('pointerdown', ev => {
          ev.preventDefault(); ev.stopPropagation();
          map.dragging.disable();
          const startX = ev.clientX, startY = ev.clientY, startSize = place.size;
          const move = mv => {
            const delta = Math.max(mv.clientX - startX, mv.clientY - startY);
            const next = Math.round(Math.min(400, Math.max(32, startSize + delta)));
            place.size = next;
            const [nw, nh] = photoBox(photo, next);
            el.style.width = nw + 'px'; el.style.height = nh + 'px';
            el.style.marginLeft = (-nw / 2) + 'px'; el.style.marginTop = (-nh / 2) + 'px';
          };
          const up = () => {
            window.removeEventListener('pointermove', move);
            window.removeEventListener('pointerup', up);
            map.dragging.enable();
            // Read the position off the marker rather than trusting `place`:
            // the marker is the one thing a drag always leaves correct.
            const ll = marker.getLatLng();
            place.pos = [+ll.lat.toFixed(6), +ll.lng.toFixed(6)];
            cur().photos[r.id][photoId] = { pos: place.pos, size: place.size };
            save(); render();
          };
          window.addEventListener('pointermove', move);
          window.addEventListener('pointerup', up);
        });
      }
      photoMarkers.set(`${r.id}/${photoId}`, marker);
    }
  });

  // ---- stats
  const stats = document.getElementById('stats');
  const nPhotos = Object.values(cur().photos).reduce((a, o) => a + Object.keys(o).length, 0);
  if (!sel.length) {
    stats.innerHTML = `<b>${vis.length}</b> rides shown · nothing selected in "${escapeHtml(state.active)}"`;
  } else {
    const km = sel.reduce((a, r) => a + r.km, 0);
    const elev = sel.reduce((a, r) => a + r.elev, 0);
    const n = clusterCount(sel);
    const sheets = n === 1 ? 'fits on <b>1 map</b>'
                           : `<span class="warn">needs ${n} maps</span> (rides &gt;40 km apart)`;
    stats.innerHTML =
      `<b>${sel.length}</b> of ${vis.length} shown · <b>${km.toFixed(0)} km</b> · ` +
      `<b>${elev.toLocaleString()} m</b> climbed · <b>${nPhotos}</b> photo(s) placed<br>` +
      `${sel[0].date} → ${sel[sel.length - 1].date} · ${sheets}`;
  }

  layoutPins();
  if (legendMode) renderLegend();
  // Do not clobber what is being pasted in.
  const out = document.getElementById('out');
  if (document.activeElement !== out) out.value = exportJson();
  document.getElementById('ghosts').textContent = showGhosts ? 'Hide unselected' : 'Show unselected';
  document.getElementById('leaders').textContent = cur().leaders ? 'Hide photo lines' : 'Show photo lines';
  document.getElementById('psize').value = cur().size;
}

// Two rides can share a turnaround exactly -- an out-and-back to the same spot
// twice -- which stacks their numbers into one unreadable pin. Push overlapping
// pins apart in *pixel* space so the separation is constant on screen, and
// recompute on zoom. A hairline connector keeps each pin tied to its true point.
const PIN_MIN_PX = 26;

function layoutPins() {
  pinLinkLayer.clearLayers();
  // Projecting latlng to pixels needs a centre and zoom; before the first
  // fitBounds the map has neither and Leaflet throws. getZoom() is the one
  // readiness check that returns undefined rather than throwing.
  if (pinState.length < 2 || map.getZoom() === undefined) return;

  const pts = pinState.map(s => map.latLngToLayerPoint(L.latLng(s.truePos)));
  const home = pts.map(p => ({ x: p.x, y: p.y }));

  for (let pass = 0; pass < 60; pass++) {
    let moved = false;
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        const a = pts[i], b = pts[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d = Math.sqrt(dx * dx + dy * dy);
        if (d >= PIN_MIN_PX) continue;
        if (d < 1e-6) {
          // Exactly coincident: nudge along the golden angle so a whole group
          // fans out evenly instead of all pushing the same direction.
          const ang = i * 2.399;
          dx = Math.cos(ang); dy = Math.sin(ang); d = 1;
        }
        const push = ((PIN_MIN_PX - d) / 2) / d;
        a.x -= dx * push; a.y -= dy * push;
        b.x += dx * push; b.y += dy * push;
        moved = true;
      }
    }
    if (!moved) break;
  }

  pinState.forEach((s, i) => {
    s.marker.setLatLng(map.layerPointToLatLng(pts[i]));
    const shift = Math.hypot(pts[i].x - home[i].x, pts[i].y - home[i].y);
    if (shift > 6) {
      L.polyline([map.layerPointToLatLng(pts[i]), s.truePos], {
        color: s.colour, weight: 1, opacity: .6, dashArray: '2,2', interactive: false,
      }).addTo(pinLinkLayer);
    }
  });
}

// ---------------------------------------------------------------- legend
// The legend uses the same numbering and colours as the map, because the two
// are read together: the circle beside a name is the pin out on the route.
let legendMode = false;

function renderLegend() {
  const sel = selectedRides();
  const set = cur();
  const title = document.getElementById('legendTitle');
  if (document.activeElement !== title) title.textContent = set.title || '';

  const period = document.getElementById('legendPeriod');
  period.textContent = sel.length
    ? (sel.map(r => r.date).sort()[0] === sel.map(r => r.date).sort().slice(-1)[0]
        ? fmtDate(sel[0].date)
        : `${fmtDate(sel.map(r => r.date).sort()[0])} – ${fmtDate(sel.map(r => r.date).sort().slice(-1)[0])}`)
    : '';

  const list = document.getElementById('legendList');
  list.innerHTML = '';
  sel.forEach((r, i) => {
    const li = document.createElement('li');
    const figures = `${r.km.toFixed(1)} km · ${r.elev} m`;
    li.innerHTML =
      `<span class="num" style="background:${colour(i, sel.length)}">${i + 1}</span>` +
      `<span class="nm"><b>${escapeHtml(r.name)}</b><br>` +
      `<span class="fig">${fmtDate(r.date)} · ${figures}</span></span>`;
    list.appendChild(li);
  });

  const km = sel.reduce((a, r) => a + r.km, 0);
  const elev = sel.reduce((a, r) => a + r.elev, 0);
  document.getElementById('legendTotals').textContent = sel.length
    ? `${sel.length} rides · ${km.toFixed(0)} km · ${elev.toLocaleString()} m climbed`
    : 'nothing selected';

  const el = document.getElementById('legend');
  if (set.legendPos) { el.style.left = set.legendPos[0] + 'px'; el.style.top = set.legendPos[1] + 'px'; }
}

function fmtDate(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${+d} ${['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+m - 1]} ${y}`;
}

function setLegendMode(on) {
  legendMode = on;
  document.body.classList.toggle('legendmode', on);
  document.body.classList.remove('chrome');
  document.getElementById('legend').hidden = !on;
  // The sidebar collapses in legend mode, so the map has to re-measure itself.
  setTimeout(() => { map.invalidateSize(); layoutPins(); }, 0);
  if (on) { renderLegend(); flashHint(); }
}

// Controls are hidden in legend mode, so say once how to get them back rather
// than leaving someone stuck with no way out.
function flashHint() {
  const hint = document.getElementById('chromehint');
  hint.classList.add('show');
  setTimeout(() => hint.classList.remove('show'), 2600);
}

function showChrome(on) {
  if (legendMode) document.body.classList.toggle('chrome', on);
}

// The export is the save format, so it carries everything needed to rebuild a
// selection: not just which rides, but the legend and every photo placement.
function exportJson() {
  const sel = selectedRides();
  const set = cur();
  return JSON.stringify({
    version: 1,
    name: state.active,
    title: set.title || '',
    legendPos: set.legendPos || null,
    leaders: set.leaders !== false,
    photoSize: set.size,
    rides: sel.map((r, i) => ({
      order: i + 1, id: r.id, name: r.name, date: r.date, km: r.km, elev: r.elev,
      photos: Object.entries(set.photos[r.id] || {}).map(([id, v]) => {
        const pl = placement(v, set.size);
        const photo = r.photos.find(p => p.id === id);
        return { id, pos: pl.pos, size: pl.size, taken_at: photo ? photo.at : null };
      }),
    })),
  }, null, 2);
}

// A merge collapses its members into one surviving id. A selection saved before
// a merge still names the members, so map them forward rather than dropping them.
const survivorOf = new Map();
for (const r of RIDES) {
  for (const member of (r.merged_from || [])) survivorOf.set(member, r.id);
}

function uniqueSetName(base) {
  let name = base || 'Imported';
  for (let n = 2; state.sets[name]; n++) name = base + ' (' + n + ')';
  return name;
}

function importSelection(text) {
  let payload;
  try {
    payload = JSON.parse(text);
  } catch (e) {
    return { error: 'That is not valid JSON.' };
  }
  const rides = Array.isArray(payload) ? payload : payload.rides;
  if (!Array.isArray(rides)) {
    return { error: 'No \"rides\" array found in that JSON.' };
  }

  const ids = [];
  const photos = {};
  const missing = [];
  for (const entry of rides) {
    const rawId = typeof entry === 'object' ? entry.id : entry;
    const id = byId.has(rawId) ? rawId : survivorOf.get(rawId);
    if (id === undefined || !byId.has(id)) { missing.push(rawId); continue; }
    if (!ids.includes(id)) ids.push(id);

    const ride = byId.get(id);
    for (const ph of (entry && entry.photos) || []) {
      // Only keep placements for photos this build actually has on disk.
      if (!ride.photos.some(p => p.id === ph.id)) continue;
      (photos[id] = photos[id] || {})[ph.id] = {
        pos: ph.pos, size: ph.size || payload.photoSize || 72,
      };
    }
  }

  const name = uniqueSetName(payload.name || 'Imported');
  state.sets[name] = {
    ids, photos,
    leaders: payload.leaders !== false,
    size: payload.photoSize || 72,
    title: payload.title || '',
    legendPos: payload.legendPos || null,
  };
  state.active = name;
  persist();
  return { name: name, count: ids.length, missing: missing };
}

function fitTo(rides) {
  if (!rides.length) return false;
  const b = L.latLngBounds([]);
  rides.forEach(r => r.track.forEach(p => b.extend([p[0], p[1]])));
  // fitBounds throws on empty bounds, which happens if every ride is trackless.
  if (!b.isValid || !b.isValid()) return false;
  map.fitBounds(b, { padding: [30, 30] });
  return true;
}

// ---------------------------------------------------------------- wiring
['from', 'to', 'q', 'dmin', 'dmax', 'kidsOnly', 'ridesOnly'].forEach(id =>
  document.getElementById(id).addEventListener('input', render));

document.getElementById('setPicker').onchange = e => {
  state.active = e.target.value; persist(); render(); fitTo(selectedRides());
};
document.getElementById('setNew').onclick = () => {
  const name = prompt('Name for the new selection:', `Selection ${Object.keys(state.sets).length + 1}`);
  if (!name || state.sets[name]) return;
  state.sets[name] = blankSet(); state.active = name; persist(); render();
};
document.getElementById('setDup').onclick = () => {
  const name = prompt('Name for the copy:', `${state.active} copy`);
  if (!name || state.sets[name]) return;
  state.sets[name] = JSON.parse(JSON.stringify(cur()));
  state.active = name; persist(); render();
};
document.getElementById('setRen').onclick = () => {
  const name = prompt('Rename selection:', state.active);
  if (!name || name === state.active || state.sets[name]) return;
  state.sets[name] = cur(); delete state.sets[state.active];
  state.active = name; persist(); render();
};
document.getElementById('setDel').onclick = () => {
  const names = Object.keys(state.sets);
  if (names.length < 2) { alert('Keep at least one selection.'); return; }
  if (!confirm(`Delete selection "${state.active}"?`)) return;
  delete state.sets[state.active];
  state.active = Object.keys(state.sets)[0];
  persist(); render();
};

document.getElementById('selVisible').onclick = () => {
  const ids = cur().ids;
  visibleRides().forEach(r => { if (!ids.includes(r.id)) ids.push(r.id); });
  persist(); render();
};
document.getElementById('clear').onclick = () => {
  state.sets[state.active] = blankSet(); persist(); render();
};
document.getElementById('invert').onclick = () => {
  visibleRides().forEach(r => {
    const ids = cur().ids, at = ids.indexOf(r.id);
    if (at >= 0) { ids.splice(at, 1); delete cur().photos[r.id]; } else ids.push(r.id);
  });
  persist(); render();
};
document.getElementById('legendOn').onclick = () => setLegendMode(true);
document.getElementById('legendBack').onclick = () => setLegendMode(false);
const legendTitle = document.getElementById('legendTitle');
legendTitle.addEventListener('input', () => {
  cur().title = legendTitle.textContent.trim(); persist();
});
// Enter would insert a line break in a contenteditable; commit instead.
legendTitle.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); legendTitle.blur(); }
});

// Drag the legend by its top strip, so it can be placed clear of the tracks.
(() => {
  const card = document.getElementById('legend');
  const grab = document.getElementById('legendGrab');
  grab.addEventListener('pointerdown', ev => {
    ev.preventDefault();
    const rect = card.getBoundingClientRect();
    const mapBox = document.getElementById('map').getBoundingClientRect();
    const offX = ev.clientX - rect.left, offY = ev.clientY - rect.top;
    grab.style.cursor = 'grabbing';
    const move = mv => {
      card.style.left = (mv.clientX - mapBox.left - offX) + 'px';
      card.style.top = (mv.clientY - mapBox.top - offY) + 'px';
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      grab.style.cursor = 'grab';
      cur().legendPos = [parseInt(card.style.left, 10), parseInt(card.style.top, 10)];
      persist();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  });
})();

document.getElementById('leaders').onclick = () => {
  cur().leaders = !cur().leaders; persist(); render();
};
const psize = document.getElementById('psize');
psize.oninput = () => {
  // Resize every placed photo in this selection, and set the default for new ones.
  const size = +psize.value;
  cur().size = size;
  for (const forRide of Object.values(cur().photos))
    for (const id of Object.keys(forRide))
      forRide[id] = { pos: placement(forRide[id], size).pos, size };
  persist(); render();
};
document.getElementById('zoomSel').onclick = () => fitTo(selectedRides());
document.getElementById('ghosts').onclick = () => { showGhosts = !showGhosts; render(); };
document.getElementById('resetPhotos').onclick = () => {
  for (const r of selectedRides()) {
    const placed = cur().photos[r.id];
    if (!placed) continue;
    Object.keys(placed).forEach((pid, i) => {
      const photo = r.photos.find(p => p.id === pid);
      placed[pid] = { pos: (photo && photo.at) || defaultPos(r, i), size: cur().size };
    });
  }
  persist(); render();
};
document.getElementById('copy').onclick = () => {
  navigator.clipboard.writeText(document.getElementById('out').value);
  const b = document.getElementById('copy');
  b.textContent = 'Copied'; setTimeout(() => (b.textContent = 'Copy JSON'), 1200);
};
function applyImport(text) {
  const msg = document.getElementById('importMsg');
  const result = importSelection(text);
  if (result.error) {
    msg.className = 'bad';
    msg.textContent = result.error;
    return;
  }
  msg.className = '';
  msg.textContent = `imported "${result.name}" -- ${result.count} ride(s)` +
    (result.missing.length ? `, ${result.missing.length} not in this build (skipped)` : '');
  render();
  fitTo(selectedRides());
}

document.getElementById('importText').onclick = () =>
  applyImport(document.getElementById('out').value);

document.getElementById('importFile').onclick = () =>
  document.getElementById('filePicker').click();

document.getElementById('filePicker').onchange = e => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => applyImport(String(reader.result));
  reader.readAsText(file);
  e.target.value = '';   // let the same file be picked again
};

document.getElementById('download').onclick = () => {
  const blob = new Blob([exportJson()], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${state.active.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
};

// Hold shift or option to bring the map controls back while in legend mode.
//
// Cmd is deliberately not a reveal key, and any *pair* of modifiers keeps the
// controls hidden: cmd-shift-4 is the macOS screenshot shortcut, so revealing on
// cmd or on shift-with-anything would put the controls into the very screenshot
// the legend view exists to take.
function wantsChrome(e) {
  const held = [e.shiftKey, e.altKey, e.metaKey, e.ctrlKey].filter(Boolean).length;
  return held === 1 && (e.shiftKey || e.altKey);
}

// Shift is also how capitals are typed, so ignore keys aimed at the legend
// title or the JSON box.
function isTyping(e) {
  const el = e.target;
  return !!el && (el.isContentEditable === true ||
                  el.tagName === 'INPUT' || el.tagName === 'TEXTAREA');
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && legendMode && !isTyping(e)) { setLegendMode(false); return; }
  if (isTyping(e)) return;
  showChrome(wantsChrome(e));
});
document.addEventListener('keyup', e => {
  if (isTyping(e)) return;
  showChrome(wantsChrome(e));
});
// Releasing the key outside the page never fires keyup, which would strand the
// controls visible.
window.addEventListener('blur', () => showChrome(false));

const zoomout = document.getElementById('zoomout');
function showZoom() {
  const c = map.getCenter();
  zoomout.textContent = `z ${map.getZoom().toFixed(2)}  ${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}`;
}
map.on('zoom move', showZoom);
// Pin separation is defined in pixels, so it has to be recomputed per zoom level.
map.on('zoomend', layoutPins);

// The view must exist before the first render: render() lays out the pins, and
// that projects coordinates. Falling back to a world view keeps an empty or
// trackless data set from leaving the map unusable.
if (!fitTo(cur().ids.length ? selectedRides() : RIDES)) map.setView([0, 0], 2);
render();
showZoom();
</script>
</body>
</html>
"""
