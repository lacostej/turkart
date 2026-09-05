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
from .store import Store

DEFAULT_OUTPUT = Path("build/explore.html")


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
    activities = store.load_activities()
    rides: list[dict] = []

    for key, raw in activities.items():
        if only_ids is not None and int(raw["id"]) not in only_ids:
            continue
        if not store.has_streams(key):
            continue
        streams = store.load_streams(key)
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

        rides.append(
            {
                "id": raw["id"],
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


def write_html(rides: list[dict], output: Path = DEFAULT_OUTPUT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rides, separators=(",", ":"))
    output.write_text(_TEMPLATE.replace("__RIDES__", payload), encoding="utf-8")
    return output


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
  #sidebar { width:420px; flex:none; background:var(--panel); border-right:1px solid var(--line);
             display:flex; flex-direction:column; height:100vh; }
  #map { flex:1; height:100vh; }
  header { padding:14px 16px 10px; border-bottom:1px solid var(--line); }
  h1 { margin:0 0 10px; font-size:15px; letter-spacing:.02em; text-transform:uppercase; }
  .filters { display:grid; grid-template-columns:1fr 1fr; gap:6px; }
  .filters input, .filters select { width:100%; padding:5px 7px; border:1px solid var(--line);
                                    border-radius:4px; font:inherit; background:#fff; }
  .filters label { grid-column:span 2; font-size:11px; color:var(--muted);
                   text-transform:uppercase; letter-spacing:.05em; margin-top:4px; }
  .toggles { grid-column:span 2; display:flex; gap:14px; }
  .tog { display:flex; align-items:center; gap:5px; font-size:12px; color:var(--ink);
         text-transform:none; letter-spacing:0; margin:0; cursor:pointer; }
  .tog input { margin:0; }
  .kid { font-size:10px; font-weight:700; color:#7a4a1e; background:#ffe8cf;
         border-radius:3px; padding:0 4px; margin-left:5px; vertical-align:1px; }
  .btnrow { display:flex; gap:6px; flex-wrap:wrap; padding:10px 16px; border-bottom:1px solid var(--line); }
  button { font:inherit; font-size:12px; padding:5px 10px; border:1px solid var(--line);
           background:#fff; border-radius:4px; cursor:pointer; }
  button:hover { background:var(--accent-soft); border-color:var(--accent); }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  #stats { padding:10px 16px; font-size:12px; color:var(--muted); border-bottom:1px solid var(--line);
           background:#fbfbfa; }
  #stats b { color:var(--ink); }
  #stats .warn { color:var(--accent); font-weight:600; }
  #list { flex:1; overflow-y:auto; }
  .ride { display:flex; gap:9px; padding:7px 16px; border-bottom:1px solid #f0f0ec; cursor:pointer; align-items:baseline; }
  .ride:hover { background:var(--accent-soft); }
  .ride.on { background:#fff6f2; }
  .ride input { margin:0; flex:none; position:relative; top:2px; }
  .ride .meta { flex:1; min-width:0; }
  .ride .nm { display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .ride.on .nm { font-weight:600; }
  .ride .sub { font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .swatch { width:9px; height:9px; border-radius:50%; flex:none; position:relative; top:4px; }
  #export { padding:10px 16px; border-top:1px solid var(--line); background:#fbfbfa; }
  #export textarea { width:100%; height:58px; font:11px/1.4 ui-monospace, Menlo, monospace;
                     border:1px solid var(--line); border-radius:4px; padding:6px; resize:vertical; }
  .rank-pin { background:none !important; border:none !important; }
  .rank-pin span { display:flex; align-items:center; justify-content:center;
                   width:22px; height:22px; border-radius:50%; color:#fff;
                   font-weight:700; font-size:11px; border:2px solid #fff;
                   box-shadow:0 1px 3px rgba(0,0,0,.4); transition:transform .12s; }
  .rank-pin.hot span { transform:scale(1.55); }
  #zoomout { position:absolute; z-index:500; right:10px; bottom:22px; background:#fff;
             border:1px solid var(--line); border-radius:4px; padding:3px 7px;
             font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }
</style>
</head>
<body>
<div id="sidebar">
  <header>
    <h1>Ride selector</h1>
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
      <button class="primary" id="copy">Copy IDs</button>
      <button id="download">Save selection.json</button>
    </div>
  </div>
</div>
<div id="map"><div id="zoomout"></div></div>

<script>
const RIDES = __RIDES__;
const byId = new Map(RIDES.map(r => [r.id, r]));
const STORAGE = 'strava-poster-selection';
const TAG_WITH_KIDS = 16;

let selected = new Set();
try {
  const saved = JSON.parse(localStorage.getItem(STORAGE) || '[]');
  selected = new Set(saved.filter(id => byId.has(id)));
} catch (e) { /* first run, or storage blocked */ }

let showGhosts = false;
const drawn = new Map();  // ride id -> {line, pin}, for hover linking

// zoomSnap/zoomDelta at 1/4 step: framing a poster needs finer control than
// Leaflet's default whole-integer zoom levels allow.
const map = L.map('map', { preferCanvas: true, zoomSnap: 0.25, zoomDelta: 0.25, maxZoom: 20 });

// osm.org blocks tile requests that arrive without a Referer, which is exactly
// what a page opened over file:// sends -- hence 403s there. CARTO's basemaps
// have no such rule, so they are the default and the page works either way.
// They are also far better backdrops for tracks: muted, low-contrast, few
// competing colours. Serve over http (`--serve`) to use the osm.org layer.
const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const basemaps = {
  'Carto Light': L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    subdomains: 'abcd', maxZoom: 20, referrerPolicy: 'origin',
    attribution: OSM_ATTR + ', &copy; <a href="https://carto.com/attributions">CARTO</a>' }),
  'Carto Voyager': L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
    subdomains: 'abcd', maxZoom: 20, referrerPolicy: 'origin',
    attribution: OSM_ATTR + ', &copy; <a href="https://carto.com/attributions">CARTO</a>' }),
  'Carto Dark': L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    subdomains: 'abcd', maxZoom: 20, referrerPolicy: 'origin',
    attribution: OSM_ATTR + ', &copy; <a href="https://carto.com/attributions">CARTO</a>' }),
  'Esri Topo': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19, referrerPolicy: 'origin', attribution: '&copy; Esri' }),
  'OSM (needs http)': L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, referrerPolicy: 'origin', attribution: OSM_ATTR }),
};
basemaps['Carto Light'].addTo(map);
L.control.layers(basemaps, null, { position: 'topright' }).addTo(map);
const ghostLayer = L.layerGroup().addTo(map);
const trackLayer = L.layerGroup().addTo(map);

// Distinct hues, evenly spaced, so adjacent rides in the legend stay separable.
// Track points are [lat, lng, metres_along, altitude_m].
const latlngs = r => r.track.map(p => [p[0], p[1]]);

function colour(i, n) { return `hsl(${Math.round((i * 360) / Math.max(n, 1))} 72% 45%)`; }

function fmtDuration(s) {
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h}h${String(m).padStart(2, '0')}` : `${m}min`;
}

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
  return RIDES.filter(r => selected.has(r.id));
}

// --- clustering: how many map sheets does this selection need? --------------
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

function render() {
  const vis = visibleRides();
  const sel = selectedRides();

  // ---- list
  const list = document.getElementById('list');
  list.innerHTML = '';
  const rank = new Map(sel.map((r, i) => [r.id, i]));
  for (const r of vis) {
    const on = selected.has(r.id);
    const row = document.createElement('div');
    row.className = 'ride' + (on ? ' on' : '');
    const swatch = on
      ? `<span class="swatch" style="background:${colour(rank.get(r.id), sel.length)}"></span>`
      : `<span class="swatch" style="background:#d9d9d4"></span>`;
    row.innerHTML = `
      <input type="checkbox" ${on ? 'checked' : ''}>
      ${swatch}
      <span class="meta">
        <span class="nm">${on ? (rank.get(r.id) + 1) + '. ' : ''}${escapeHtml(r.name)}${(r.tags || []).includes(TAG_WITH_KIDS) ? '<span class="kid">KIDS</span>' : ''}</span>
        <span class="sub">${r.date} · ${r.km.toFixed(1)} km · ${r.elev} m · ${fmtDuration(r.moving_s)}</span>
      </span>`;
    row.onmouseenter = () => highlight(r.id, true);
    row.onmouseleave = () => highlight(r.id, false);
    row.onclick = e => {
      if (e.target.tagName === 'A') return;
      selected.has(r.id) ? selected.delete(r.id) : selected.add(r.id);
      persist(); render();
    };
    list.appendChild(row);
  }

  // ---- map
  trackLayer.clearLayers();
  ghostLayer.clearLayers();
  if (showGhosts) {
    for (const r of vis) {
      if (selected.has(r.id)) continue;
      L.polyline(latlngs(r), { color: '#9a9a94', weight: 1, opacity: .45 }).addTo(ghostLayer);
    }
  }
  drawn.clear();
  sel.forEach((r, i) => {
    const col = colour(i, sel.length);
    const line = L.polyline(latlngs(r), { color: col, weight: 3, opacity: .9 }).addTo(trackLayer);
    const popup =
      `<b>${i + 1}. ${escapeHtml(r.name)}</b><br>${r.date} ${r.time || ''}<br>` +
      `${r.km.toFixed(1)} km · ${r.elev} m · ${fmtDuration(r.moving_s)}<br>` +
      `turnaround ${r.far_km.toFixed(1)} km from start<br>` +
      `<a href="${r.url}" target="_blank">open in Strava</a>`;
    line.bindPopup(popup);

    // Pin sits at the turnaround, not the start, and carries the track's own
    // colour -- so a number can always be traced back to its ride.
    const pin = L.marker(r.label_at, {
      icon: L.divIcon({
        className: 'rank-pin', iconSize: [22, 22], iconAnchor: [11, 11],
        html: `<span style="background:${col}">${i + 1}</span>`,
      }),
      riseOnHover: true, zIndexOffset: 1000,
    }).addTo(trackLayer);
    pin.bindPopup(popup);
    drawn.set(r.id, { line, pin });
  });

  // ---- stats
  const stats = document.getElementById('stats');
  if (!sel.length) {
    stats.innerHTML = `<b>${vis.length}</b> rides shown · nothing selected yet`;
  } else {
    const km = sel.reduce((a, r) => a + r.km, 0);
    const elev = sel.reduce((a, r) => a + r.elev, 0);
    const n = clusterCount(sel);
    const sheets = n === 1
      ? `fits on <b>1 map</b>`
      : `<span class="warn">needs ${n} maps</span> (rides &gt;40 km apart)`;
    stats.innerHTML =
      `<b>${sel.length}</b> of ${vis.length} shown selected · <b>${km.toFixed(0)} km</b> · ` +
      `<b>${elev.toLocaleString()} m</b> climbed<br>` +
      `${sel[0].date} → ${sel[sel.length - 1].date} · ${sheets}`;
  }

  document.getElementById('out').value = JSON.stringify(sel.map(r => r.id));
  document.getElementById('ghosts').textContent = showGhosts ? 'Hide unselected' : 'Show unselected';
}

// Make one ride unmistakable among overlapping neighbours.
function highlight(id, on) {
  const d = drawn.get(id);
  if (!d) return;
  d.line.setStyle({ weight: on ? 7 : 3, opacity: on ? 1 : .9 });
  if (on) d.line.bringToFront();
  const el = d.pin.getElement();
  if (el) el.classList.toggle('hot', on);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function persist() {
  try { localStorage.setItem(STORAGE, JSON.stringify([...selected])); } catch (e) {}
}

function fitTo(rides) {
  if (!rides.length) return;
  const b = L.latLngBounds([]);
  rides.forEach(r => r.track.forEach(p => b.extend([p[0], p[1]])));
  map.fitBounds(b, { padding: [30, 30] });
}

// --- wiring -----------------------------------------------------------------
['from', 'to', 'q', 'dmin', 'dmax', 'kidsOnly', 'ridesOnly'].forEach(id =>
  document.getElementById(id).addEventListener('input', render));

document.getElementById('selVisible').onclick = () => {
  visibleRides().forEach(r => selected.add(r.id)); persist(); render();
};
document.getElementById('clear').onclick = () => { selected.clear(); persist(); render(); };
document.getElementById('invert').onclick = () => {
  visibleRides().forEach(r => selected.has(r.id) ? selected.delete(r.id) : selected.add(r.id));
  persist(); render();
};
document.getElementById('zoomSel').onclick = () => fitTo(selectedRides());
document.getElementById('ghosts').onclick = () => { showGhosts = !showGhosts; render(); };
document.getElementById('copy').onclick = () => {
  navigator.clipboard.writeText(document.getElementById('out').value);
  const b = document.getElementById('copy');
  b.textContent = 'Copied'; setTimeout(() => (b.textContent = 'Copy IDs'), 1200);
};
document.getElementById('download').onclick = () => {
  const blob = new Blob([JSON.stringify(selectedRides().map(r => r.id), null, 2)],
                        { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'selection.json';
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
fitTo(selected.size ? selectedRides() : RIDES);
showZoom();
</script>
</body>
</html>
"""
