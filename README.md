# Strava → poster

Pull rides out of Strava with a browser session, pick a subset by hand, and (next)
render them as a printable poster.

## Setup

```bash
uv venv && uv pip install requests
```

## 1. Auth

Strava's internal JSON endpoints authenticate with your browser's cookies. In
Firefox/Chrome devtools → Network, right-click any request to `strava.com` →
**Copy → Copy as cURL**, paste it into a file, then:

```bash
python -m strava auth import LOCAL/strava_web.md   # accepts a whole scratch file
python -m strava auth check                        # confirm it works
```

Cookies go to `.secrets/session.json` (gitignored, mode 600). When they expire,
`auth check` says so — grab a fresh cURL and re-import.

> `_strava_idcf` carries a JWT expiry that `auth status` reports as a *hint*.
> The real login cookie is `_strava4_session`, which has no visible expiry, so
> the hint often reads stale while the session still works. `auth check` is the
> authoritative answer.

## 2. Fetch

```bash
python -m strava activities sync --sport-type Ride        # ride summaries
python -m strava activities sync --sport-type "" --tags 16 # everything tagged "With Kids"
python -m strava streams fetch                             # GPS tracks for them
```

Everything lands in `data/` (gitignored): `activities.json` plus one
`streams/<id>.json` per ride holding `latlng`, `distance`, `altitude`, `time` —
four index-aligned arrays, so `latlng[i]` and `altitude[i]` are the same instant.
Fetching is the only step that needs credentials; every later step is offline.

Fetches are paced at ~1 req/sec. Rides with `has_latlng: false` are skipped.

## 3. Repair split recordings

A ride stopped and restarted mid-outing lands in Strava as two activities. On a
poster they read as two separate loops from home, and get double-counted.

```bash
python -m strava merge suggest            # detect, with evidence
python -m strava merge suggest --apply    # accept them all
python -m strava merge apply <idA> <idB> --name "..."
python -m strava merge list / remove <id>
```

Detection takes **two** tests, and the second is the one that matters:

1. the second activity starts near where the first ended, soon after — and
2. that junction is **far from where the first ride began**.

Test 1 alone is badly insufficient: a ride ending at home followed by another
starting at home also has a tiny junction gap. On this data that paired a ride
*with kids* to a later *solo* ride purely because both touched home. Requiring
the junction to sit out on the route (≥2 km from the start, `--min-junction`)
removes those while keeping real splits, whose junctions are 10 km+ out.

Merges are stored in `data/merges.json` as *intent* — just member ids. The
original streams are never rewritten, so a merge is always reversible. They are
folded in when the page is built: `distance` and `time` are cumulative per
activity, so they get re-accumulated, and the straight-line hop across the gap
is added to the total rather than dropped.

## 4. Select

```bash
python -m strava explore --open            # file://
python -m strava explore --serve --open    # http://localhost:8000
```

Builds `build/explore.html`, a self-contained page (tracks embedded, Leaflet from
CDN) — it runs off the filesystem, so your GPS traces are never uploaded anywhere.

### Basemaps

Defaults to **CARTO Light**; a layer switcher (top right) offers Voyager, Dark,
and Esri Topo. The muted CARTO styles are the better backdrop for coloured tracks
anyway, and they matter for a poster.

`tile.openstreetmap.org` is also offered but **only works under `--serve`**:
[OSM blocks tile requests that arrive without a `Referer`][osm-blocked], and a
page opened over `file://` sends none — which is why it 403s there. Serving over
localhost gives the page a real origin and the OSM layer starts working.

[osm-blocked]: https://wiki.openstreetmap.org/wiki/Blocked_tiles

- Filter by date, distance, text, **With kids only**, **Rides only**
- Click rides to select; selection persists in `localStorage`
- Selected rides are numbered and colour-coded on the map and in the list.
  Numbers sit at each ride's **turnaround** (its furthest point from the start),
  not at the start — every ride leaves from home, so start-anchored labels stack
  on one pixel. Hovering a row thickens its track and enlarges its pin.
- The stats bar reports whether the selection **fits on one map**, or how many
  sheets it needs (rides more than 40 km apart get their own)
- **Save selection.json** exports the chosen ids, in order

CLI filters mirror the UI, e.g. `--tag 16 --sport-type-filter Ride --after 2026-01-01`.

## Layout

| path | role |
|---|---|
| `strava/session.py` | parse cURL captures, store/rebuild the browser session |
| `strava/client.py` | the internal endpoints, paced and error-mapped |
| `strava/store.py` | `data/` cache, atomic writes |
| `strava/geo.py` | RDP simplification, bounding boxes, ride clustering |
| `strava/merge.py` | detect and fold together split recordings |
| `strava/explore.py` | builds the selector page |
| `strava/cli.py` | command line |

Track points in the built page are `[lat, lng, metres_along, altitude_m]` — the
other streams are carried through simplification by index, so a sub-range of a
ride can be measured without reloading full-resolution data.

## Not built yet

- **Poster renderer** — the end goal.
- **Partial rides** — cut a ride at a chosen point and include only part of it
  (the groundwork is in: per-point distance/altitude is already embedded).
- **Patching** a recording that dropped out mid-ride (merging is done).
