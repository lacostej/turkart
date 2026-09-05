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
                    photos.append(
                        {"id": item["photo_id"], "src": rel,
                         "caption": item.get("caption") or ""}
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
<title>Ride selector</title>
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
  .strip .cnt { font-size:10px; color:var(--muted); align-self:center; white-space:nowrap; }
  #export { padding:9px 16px; border-top:1px solid var(--line); background:#fbfbfa; }
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
  #zoomout { position:absolute; z-index:500; right:10px; bottom:22px; background:#fff;
             border:1px solid var(--line); border-radius:4px; padding:3px 7px;
             font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }
</style>
</head>
<body>
<div id="sidebar">
  <header>
    <h1>Ride selector</h1>
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
  </div>
  <div id="stats"></div>
  <div id="list"></div>
  <div id="export">
    <textarea id="out" readonly></textarea>
    <div style="display:flex;gap:6px;margin-top:6px">
      <button class="primary" id="copy">Copy JSON</button>
      <button id="download">Save selection</button>
      <button id="resetPhotos">Reset photo positions</button>
    </div>
  </div>
</div>
<div id="map"><div id="zoomout"></div></div>

<script>
const RIDES = __RIDES__;
const byId = new Map(RIDES.map(r => [r.id, r]));
const STORAGE = 'strava-poster-v2';
const LEGACY = 'strava-poster-selection';
const TAG_WITH_KIDS = 16;

// ---------------------------------------------------------------- state
// Multiple named selections. Each holds the chosen ride ids in order, plus
// per-photo map positions, so a selection fully describes one poster layout.
function blankSet(ids) { return { ids: ids || [], photos: {} }; }

function loadState() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE) || 'null');
    if (raw && raw.sets) return raw;
  } catch (e) {}
  // Carry over a selection made before named sets existed.
  let legacy = [];
  try { legacy = JSON.parse(localStorage.getItem(LEGACY) || '[]'); } catch (e) {}
  return { active: 'Selection 1', sets: { 'Selection 1': blankSet(legacy.filter(i => byId.has(i))) } };
}

let state = loadState();
function cur() { return state.sets[state.active] || (state.sets[state.active] = blankSet()); }
function persist() {
  try { localStorage.setItem(STORAGE, JSON.stringify(state)); } catch (e) {}
}
function selectedSet() { return new Set(cur().ids); }

let showGhosts = false;
const drawn = new Map();          // ride id -> {line, pin}
const photoMarkers = new Map();   // "rideId/photoId" -> marker

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
const photoLayer = L.layerGroup().addTo(map);

// Track points are [lat, lng, metres_along, altitude_m].
const latlngs = r => r.track.map(p => [p[0], p[1]]);
function colour(i, n) { return `hsl(${Math.round((i * 360) / Math.max(n, 1))} 72% 45%)`; }
function fmtDuration(s) {
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h}h${String(m).padStart(2, '0')}` : `${m}min`;
}
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
  else forRide[photo.id] = defaultPos(ride, index);
  if (!Object.keys(forRide).length) delete photos[ride.id];
  persist(); render();
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
        strip.appendChild(img);
      });
      const cnt = document.createElement('span');
      cnt.className = 'cnt';
      cnt.textContent = `${Object.keys(placed).length}/${r.photos.length} placed`;
      strip.appendChild(cnt);
      block.appendChild(strip);
    }
    list.appendChild(block);
  }

  // ---- map
  trackLayer.clearLayers();
  ghostLayer.clearLayers();
  photoLayer.clearLayers();
  drawn.clear();
  photoMarkers.clear();

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

    // Placed photos: draggable, position saved per selection on drop.
    const placed = cur().photos[r.id] || {};
    for (const [photoId, pos] of Object.entries(placed)) {
      const photo = r.photos.find(p => p.id === photoId);
      if (!photo) continue;
      const marker = L.marker(pos, {
        draggable: true, zIndexOffset: 2000,
        icon: L.divIcon({ className: 'photo-pin', iconSize: [64, 64], iconAnchor: [32, 32],
                          html: `<img src="${photo.src}" style="border-color:${col}">` }),
      }).addTo(photoLayer);
      marker.on('dragstart', () => marker.getElement()?.classList.add('dragging'));
      marker.on('dragend', () => {
        marker.getElement()?.classList.remove('dragging');
        const ll = marker.getLatLng();
        cur().photos[r.id][photoId] = [+ll.lat.toFixed(6), +ll.lng.toFixed(6)];
        persist();
        document.getElementById('out').value = exportJson();
      });
      marker.bindPopup(
        `<img src="${photo.src}" style="max-width:260px;display:block;margin-bottom:6px">` +
        `${escapeHtml(photo.caption || r.name)}<br><small>drag to reposition</small>`);
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

  document.getElementById('out').value = exportJson();
  document.getElementById('ghosts').textContent = showGhosts ? 'Hide unselected' : 'Show unselected';
}

function exportJson() {
  const sel = selectedRides();
  return JSON.stringify({
    name: state.active,
    rides: sel.map((r, i) => ({
      order: i + 1, id: r.id, name: r.name, date: r.date, km: r.km, elev: r.elev,
      photos: Object.entries(cur().photos[r.id] || {}).map(([id, pos]) => ({ id, pos })),
    })),
  }, null, 2);
}

function fitTo(rides) {
  if (!rides.length) return;
  const b = L.latLngBounds([]);
  rides.forEach(r => r.track.forEach(p => b.extend([p[0], p[1]])));
  map.fitBounds(b, { padding: [30, 30] });
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
document.getElementById('zoomSel').onclick = () => fitTo(selectedRides());
document.getElementById('ghosts').onclick = () => { showGhosts = !showGhosts; render(); };
document.getElementById('resetPhotos').onclick = () => {
  for (const r of selectedRides()) {
    const placed = cur().photos[r.id];
    if (!placed) continue;
    Object.keys(placed).forEach((pid, i) => { placed[pid] = defaultPos(r, i); });
  }
  persist(); render();
};
document.getElementById('copy').onclick = () => {
  navigator.clipboard.writeText(document.getElementById('out').value);
  const b = document.getElementById('copy');
  b.textContent = 'Copied'; setTimeout(() => (b.textContent = 'Copy JSON'), 1200);
};
document.getElementById('download').onclick = () => {
  const blob = new Blob([exportJson()], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${state.active.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
};

const zoomout = document.getElementById('zoomout');
function showZoom() {
  const c = map.getCenter();
  zoomout.textContent = `z ${map.getZoom().toFixed(2)}  ${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}`;
}
map.on('zoom move', showZoom);

render();
fitTo(cur().ids.length ? selectedRides() : RIDES);
showZoom();
</script>
</body>
</html>
"""
