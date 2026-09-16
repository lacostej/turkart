"""Export a collected athlete's photos as a self-contained folder.

Produces a directory that stands on its own — copy it anywhere, open the HTML,
and it works with no server, no network and no turkart:

    galleries/<title>/
        index.html        gallery + slideshow, everything inline
        data.json         one record per photo: date, ride title, location
        images/
            <id>.jpg      the large picture
            <id>-thumb.jpg    a gallery-sized thumbnail

Thumbnails are generated locally with `sips` rather than fetched. Strava's CDN
does serve a thumbnail, but at 96x128 it is too small for a gallery, and
downloading a second size would double both the bytes and the requests for
something the machine can produce for free.
"""

from __future__ import annotations

import html as html_escape
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

THUMB_LONG_EDGE = 500

# Exports live together under one directory rather than scattering
# product-named folders at the repo root, so a single ignore rule covers them
# however many galleries get made.
GALLERIES_DIR = Path("galleries")


def folder_name(title: str) -> str:
    """A filesystem-safe directory name from a gallery title.

    Accents are stripped rather than encoded: a path is easier to type and to
    quote without them.
    """
    import unicodedata

    plain = unicodedata.normalize("NFKD", title or "gallery")
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    kept = [c if (c.isalnum() or c in "-_") else " " for c in plain]
    parts = "".join(kept).split()
    return "-".join(parts) or "gallery"


def default_output(title: str) -> Path:
    return GALLERIES_DIR / folder_name(title)


def jpeg_size(path: Path) -> tuple[int | None, int | None]:
    """Width and height straight from the JPEG's SOF marker.

    The metadata Strava reports is the size it *offers*, not the size the CDN
    actually served -- photos advertised as 1536x2048 arrive as 1200x1600. The
    file is the authority, and reading its header costs nothing.
    """
    try:
        with path.open("rb") as fh:
            if fh.read(2) != b"\xff\xd8":
                return None, None
            while True:
                marker = fh.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None, None
                if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                    fh.read(3)  # length and precision
                    height = int.from_bytes(fh.read(2), "big")
                    width = int.from_bytes(fh.read(2), "big")
                    return width, height
                length = int.from_bytes(fh.read(2), "big")
                if length < 2:
                    return None, None
                fh.seek(length - 2, 1)
    except OSError:
        return None, None


@dataclass
class ExportResult:
    folder: Path
    photos: int
    thumbs_made: int
    missing: int
    bytes: int


def _make_thumb(source: Path, dest: Path, long_edge: int = THUMB_LONG_EDGE) -> bool:
    """Downscale with sips. Returns False if it could not be produced."""
    if dest.exists():
        return True
    try:
        subprocess.run(
            ["sips", "-Z", str(long_edge), str(source), "--out", str(dest)],
            check=True, capture_output=True, timeout=60,
        )
        return dest.exists()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def export(
    store,
    athlete_id: int,
    output: Path,
    match: str | None = None,
    title: str = "Gallery",
    show_location: bool = False,
) -> ExportResult:
    """Write the gallery folder.

    ``show_location`` is off by default because the location on a record is the
    *ride's* -- where it started -- and a photo taken mid-ride is somewhere else
    entirely. For a commute photographed at a landmark along the way, displaying
    the ride's origin states something false. It stays in ``data.json`` either
    way; only the display is suppressed.
    """
    from . import athlete as A

    data = A.load_collection(store, athlete_id)
    activities = {
        str(a["id"]): a
        for a in data["activities"].values()
        if A.matches(a["name"], match)
    }

    images = output / "images"
    images.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    thumbs_made = missing = total_bytes = 0

    for raw in data["photos"].values():
        activity = activities.get(str(raw["activity_id"]))
        if activity is None:
            continue
        source = A.photo_path(store, athlete_id, raw["photo_id"])
        if not source.exists():
            missing += 1
            continue

        name = f"{raw['photo_id']}.jpg"
        thumb_name = f"{raw['photo_id']}-thumb.jpg"
        dest = images / name
        if not dest.exists():
            shutil.copy2(source, dest)
        total_bytes += dest.stat().st_size

        width, height = jpeg_size(dest)
        thumb = images / thumb_name
        if _make_thumb(dest, thumb):
            thumbs_made += 1
            total_bytes += thumb.stat().st_size
        else:
            # Better a heavier gallery than a broken one.
            thumb_name = name

        records.append(
            {
                "id": raw["photo_id"],
                "image": f"images/{name}",
                "thumb": f"images/{thumb_name}",
                "date": raw.get("taken_on"),
                "title": activity.get("name") or raw.get("activity_name"),
                "location": activity.get("location"),
                "activity_id": raw["activity_id"],
                "url": f"https://www.strava.com/activities/{raw['activity_id']}",
                "width": width,
                "height": height,
            }
        )

    # Oldest first: the interesting thing about a daily photo is the sequence.
    records.sort(key=lambda r: r.get("date") or "")

    payload = {
        "title": title,
        "athlete_id": athlete_id,
        "count": len(records),
        "showLocation": bool(show_location),
        "first": records[0]["date"] if records else None,
        "last": records[-1]["date"] if records else None,
        "photos": records,
    }
    (output / "data.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
    )

    # data.json is written for readability and reuse, but the page embeds the
    # same records so it also works opened straight off the filesystem, where
    # fetch() of a local file is blocked.
    page = _TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    # The title is escaped before it reaches markup: it defaults to a ride name,
    # which is someone else's free text.
    page = page.replace("__TITLE__", html_escape.escape(title))
    (output / "index.html").write_text(page, encoding="utf-8")

    return ExportResult(output, len(records), thumbs_made, missing, total_bytes)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#0e0e0f; --ink:#f2f2ef; --muted:#9a9a94; --line:#2a2a2c; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,
         BlinkMacSystemFont, "Segoe UI", sans-serif; }
  header { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;
           padding:18px 22px 14px; border-bottom:1px solid var(--line); position:sticky;
           top:0; background:var(--bg); z-index:5; }
  h1 { margin:0; font-size:18px; letter-spacing:.02em; }
  .sub { color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }
  .spacer { flex:1; }
  button, select, input { font:inherit; font-size:12px; color:var(--ink); background:#1b1b1d;
           border:1px solid var(--line); border-radius:5px; padding:5px 10px; cursor:pointer; }
  button:hover { border-color:#555; }
  button.on { background:var(--ink); color:var(--bg); border-color:var(--ink); }
  input[type=search] { cursor:text; min-width:150px; }

  /* ---- gallery ---- */
  #grid { display:grid; gap:10px; padding:16px 22px 40px;
          grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); }
  figure { margin:0; cursor:pointer; }
  figure img { width:100%; aspect-ratio:3/4; object-fit:cover; display:block;
               border-radius:5px; background:#1b1b1d; }
  figure img:hover { outline:2px solid var(--ink); outline-offset:2px; }
  figcaption { font-size:11px; color:var(--muted); margin-top:5px;
               white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
               font-variant-numeric:tabular-nums; }

  /* ---- viewer / slideshow: black, picture first ---- */
  #viewer { position:fixed; inset:0; background:#000; display:none;
            flex-direction:column; align-items:center; justify-content:center; z-index:50; }
  #viewer.show { display:flex; }
  #stage { flex:1; min-height:0; display:flex; align-items:center; justify-content:center;
           width:100%; padding:26px 26px 8px; }
  #stage img { max-width:100%; max-height:100%; object-fit:contain; display:block; }
  #caption { padding:10px 20px 22px; text-align:center; }
  #caption .t { font-size:15px; }
  #caption .d { font-size:12px; color:var(--muted); margin-top:3px;
                font-variant-numeric:tabular-nums; }
  #vbar { position:absolute; top:0; left:0; right:0; display:flex; gap:8px; align-items:center;
          padding:12px 16px; opacity:0; transition:opacity .15s; }
  #viewer:hover #vbar, #vbar:focus-within { opacity:1; }
  .nav { position:absolute; top:50%; transform:translateY(-50%); font-size:26px;
         padding:10px 16px; background:rgba(255,255,255,.07); border:none; }
  #prev { left:12px; } #next { right:12px; }
  #progress { position:absolute; bottom:0; left:0; height:2px; background:var(--ink);
              width:0; transition:width .2s linear; }
  @media (max-width:640px) { #grid { grid-template-columns:repeat(auto-fill, minmax(110px,1fr)); } }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <span class="sub" id="stats"></span>
  <span class="spacer"></span>
  <input type="search" id="q" placeholder="filter by title or date">
  <select id="order">
    <option value="asc">Oldest first</option>
    <option value="desc">Newest first</option>
  </select>
  <select id="every">
    <option value="2">2s</option>
    <option value="4" selected>4s</option>
    <option value="7">7s</option>
    <option value="12">12s</option>
  </select>
  <button id="shuffle" title="random order in the slideshow">Shuffle</button>
  <button id="play">▶ Slideshow</button>
</header>

<div id="grid"></div>

<div id="viewer">
  <div id="vbar">
    <button id="close">✕ Close</button>
    <button id="toggle">Pause</button>
    <span class="sub" id="vcount"></span>
    <span class="spacer"></span>
    <a id="strava" class="sub" href="#" target="_blank" style="color:var(--muted)">open in Strava ↗</a>
  </div>
  <button class="nav" id="prev">‹</button>
  <div id="stage"><img id="shot" alt=""></div>
  <div id="caption"><div class="t" id="ctitle"></div><div class="d" id="cdate"></div></div>
  <button class="nav" id="next">›</button>
  <div id="progress"></div>
</div>

<script>
const DATA = __DATA__;
const $ = id => document.getElementById(id);

let order = 'asc', shuffled = false, filter = '';
let view = [], at = 0, timer = null, playing = false;

const fmtDate = iso => {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${+d} ${['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+m-1]} ${y}`;
};

function visible() {
  const q = filter.trim().toLowerCase();
  let rows = DATA.photos.filter(p =>
    !q || (`${p.title || ''} ${p.date || ''} ${p.location || ''}`.toLowerCase().includes(q)));
  // (filtering still searches location even when it is not displayed)
  rows = rows.slice().sort((a, b) => (a.date || '').localeCompare(b.date || ''));
  if (order === 'desc') rows.reverse();
  return rows;
}

function renderGrid() {
  view = visible();
  const grid = $('grid');
  grid.innerHTML = '';
  view.forEach((p, i) => {
    const fig = document.createElement('figure');
    const img = document.createElement('img');
    img.src = p.thumb; img.loading = 'lazy'; img.alt = p.title || p.date || '';
    const cap = document.createElement('figcaption');
    cap.textContent = `${fmtDate(p.date)}${p.title ? ' · ' + p.title : ''}`;
    fig.append(img, cap);
    fig.onclick = () => open(i);
    grid.appendChild(fig);
  });
  $('stats').textContent =
    `${view.length} photo${view.length === 1 ? '' : 's'}` +
    (view.length ? ` · ${fmtDate(view[0].date)} – ${fmtDate(view[view.length-1].date)}` : '');
}

// Slideshow order is decided when it starts, so shuffling cannot reorder the
// picture currently on screen.
let sequence = [];
function buildSequence(startIndex) {
  sequence = view.map((_, i) => i);
  if (shuffled) {
    for (let i = sequence.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [sequence[i], sequence[j]] = [sequence[j], sequence[i]];
    }
    const pos = sequence.indexOf(startIndex);
    if (pos > 0) [sequence[0], sequence[pos]] = [sequence[pos], sequence[0]];
  }
  at = 0;
}

function show() {
  const p = view[sequence[at]];
  if (!p) return;
  $('shot').src = p.image;
  $('ctitle').textContent = p.title || '';
  // The record's location is where the *ride* started, which is not where the
  // photo was taken; shown only when asked for.
  $('cdate').textContent = [fmtDate(p.date), DATA.showLocation ? p.location : null]
    .filter(Boolean).join(' · ');
  $('strava').href = p.url;
  $('vcount').textContent = `${at + 1} / ${sequence.length}`;
  const bar = $('progress');
  bar.style.transition = 'none'; bar.style.width = '0';
  if (playing) {
    requestAnimationFrame(() => {
      bar.style.transition = `width ${+$('every').value}s linear`;
      bar.style.width = '100%';
    });
  }
}

function open(index) {
  if (!view.length) return;
  buildSequence(index);
  if (!shuffled) at = index;
  $('viewer').classList.add('show');
  show();
}
function close() { stop(); $('viewer').classList.remove('show'); }
function step(delta) {
  if (!sequence.length) return;
  at = (at + delta + sequence.length) % sequence.length;
  show();
}
function play() {
  playing = true; $('toggle').textContent = 'Pause'; $('play').classList.add('on');
  clearInterval(timer);
  timer = setInterval(() => step(1), +$('every').value * 1000);
  show();
}
function stop() {
  playing = false; clearInterval(timer); timer = null;
  $('toggle').textContent = 'Play'; $('play').classList.remove('on');
  $('progress').style.width = '0';
}

$('play').onclick = () => { if (!view.length) return; open(0); play(); };
$('close').onclick = close;
$('toggle').onclick = () => (playing ? stop() : play());
$('prev').onclick = () => { step(-1); if (playing) play(); };
$('next').onclick = () => { step(1); if (playing) play(); };
$('shuffle').onclick = () => {
  shuffled = !shuffled;
  $('shuffle').classList.toggle('on', shuffled);
  if ($('viewer').classList.contains('show')) { buildSequence(sequence[at] ?? 0); show(); }
};
$('every').onchange = () => { if (playing) play(); };
$('order').onchange = e => { order = e.target.value; renderGrid(); };
$('q').oninput = e => { filter = e.target.value; renderGrid(); };

document.addEventListener('keydown', e => {
  if (!$('viewer').classList.contains('show')) {
    if (e.key === ' ') { e.preventDefault(); $('play').click(); }
    return;
  }
  if (e.key === 'Escape') close();
  else if (e.key === 'ArrowRight') { step(1); if (playing) play(); }
  else if (e.key === 'ArrowLeft') { step(-1); if (playing) play(); }
  else if (e.key === ' ') { e.preventDefault(); playing ? stop() : play(); }
});

renderGrid();

// Open straight into the slideshow with #slideshow (add #shuffle to randomise),
// so the page can be pointed at a screen and left alone.
(function fromHash() {
  const h = (location.hash + location.search).toLowerCase();
  if (h.includes('shuffle')) { shuffled = true; $('shuffle').classList.add('on'); }
  const m = h.match(/(?:every|secs?)=(\d+)/);
  if (m) $('every').value = m[1];
  if (h.includes('slideshow') || h.includes('play')) { open(0); play(); }
})();
</script>
</body>
</html>
"""
