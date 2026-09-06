# turkart

*Norwegian for "touring map" — and these are family turer.*

Pull rides out of Strava with a browser session, pick a subset by hand, arrange
them with their photos, and turn the result into a printable map poster.

## Setup

```bash
uv venv && uv pip install requests
```

## Getting started

End to end, from nothing to a poster image. Each step links to its reference
section below.

### 1. Give it a Strava session

turkart reads Strava as your browser does, so it needs your browser's session.

1. Open <https://www.strava.com> and make sure you are logged in.
2. Open devtools (`⌥⌘I` in Firefox) → **Network** tab.
3. Reload the page. Right-click any request to `www.strava.com` →
   **Copy → Copy as cURL**.
4. Paste it into a file under `LOCAL/` — that directory is gitignored precisely
   because the paste contains a live session cookie:

```bash
mkdir -p LOCAL && pbpaste > LOCAL/strava.curl
python -m turkart auth import LOCAL/strava.curl
python -m turkart auth check          # confirms it works, names your athlete id
```

`auth check` is the authoritative test — see [Auth](#auth) for why the expiry
hint it prints can look stale while the session is fine.

### 2. Fetch the rides

```bash
python -m turkart activities sync --sport-type Ride   # ride summaries
python -m turkart streams fetch                       # optional: all GPS tracks
python -m turkart usage                               # what it has cost so far
```

Paced at ~1 request/second, so a few hundred rides takes a couple of minutes.
Both are cached — re-running only fetches what is missing.

> `streams fetch` is the bulk option. You can skip it and pull tracks from the
> editor instead — rides without one are listed greyed out with a **Fetch track**
> button. Photos work the same way. Fetching only what you pick is much lighter
> than downloading a whole history.

### 3. Open the editor

```bash
python -m turkart explore --serve --open
```

Serving rather than opening the file directly is what enables photo fetching and
the OSM basemap. Then put the browser in **fullscreen** (`⌃⌘F` in Firefox on
macOS) — the final image is whatever the window shows, so composing at the size
you will capture at saves redoing it.

### 4. Name a selection *(optional)*

Press **+** in the bar at the top of the sidebar and give it a name, e.g.
"Rides with the kids 2026". Selections are independent layouts, so this is worth
doing before a second poster rather than after. Skip it and you work in
"Selection 1".

### 5. Pick the rides, then their photos

Filter down — **With kids only**, **Rides only**, a date range — then click rides
to select them. **The order you click is the order they are numbered**, on both
the map and the legend.

Then press **Fetch photos for selection**. Rides with no photos yet show their
own **Fetch photos** button. Click a thumbnail to place that photo on the map;
hover one to see it large without placing it.

### 6. Compose

Press **✕** at the top right to hide the editor and show the legend. The map does
not move when you do this.

- Click the legend title to type one.
- Drag the map, zoom with `+`/`−` or the scroll wheel, until the framing is right.
- Drag photos where you want them. Hold **⇧** or **⌥** to bring back the controls
  — including each photo's corner grip for resizing, and **Save view**.
- **Save view** stores that centre and zoom with the selection, so you can come
  back to exactly this framing.

### 7. Take the picture

With the controls hidden (release ⇧), use Firefox's own screenshot: right-click
the page → **Take Screenshot** → **Save visible**.

Note that `⌘⇧4` — macOS's screenshot — is deliberately *not* a control-reveal
shortcut, so using it will not pull the chrome back into your capture.

### Then what

**Save** writes the selection as JSON: ride order, legend, framing, photo
positions and sizes. **Load file…** reads it back. That file also drives the CLI:

```bash
python -m turkart photos sync --selection my-poster.json
```

---

## Reference

The steps above in more detail, plus what is not in them.

### Auth

Strava's internal JSON endpoints authenticate with your browser's cookies. In
Firefox/Chrome devtools → Network, right-click any request to `strava.com` →
**Copy → Copy as cURL**, paste it into a file, then:

```bash
python -m turkart auth import LOCAL/strava_web.md   # accepts a whole scratch file
python -m turkart auth check                        # confirm it works
```

Cookies go to `.secrets/session.json` (gitignored, mode 600). When they expire,
`auth check` says so — grab a fresh cURL and re-import.

> `_strava_idcf` carries a JWT expiry that `auth status` reports as a *hint*.
> The real login cookie is `_strava4_session`, which has no visible expiry, so
> the hint often reads stale while the session still works. `auth check` is the
> authoritative answer.

### Fetch

```bash
python -m turkart activities sync --sport-type Ride        # ride summaries
python -m turkart activities sync --sport-type "" --tags 16 # everything tagged "With Kids"
python -m turkart streams fetch                             # GPS tracks for them
```

Everything lands in `data/` (gitignored): `activities.json` plus one
`streams/<id>.json` per ride holding `latlng`, `distance`, `altitude`, `time` —
four index-aligned arrays, so `latlng[i]` and `altitude[i]` are the same instant.
Fetching is the only step that needs credentials; every later step is offline.

Fetches are paced at ~1 req/sec. Rides with `has_latlng: false` are skipped.

### Requests and rate limits

Strava's limits are **per application**, not per athlete: 100 requests / 15 min
and **1,000 / day** for non-upload calls, which is all turkart makes. The
internal endpoints return **no `X-RateLimit-*` headers** — those exist only on
the official API — so nothing tells us what is left. turkart counts its own
requests instead, in `data/usage.json`, and every fetching command reports what
it spent:

```
1 request(s) this run; 2 today, ~998 left of Strava's 1000/day non-upload budget
```

`turkart usage` shows the running tally. It is advisory, not enforced, and it
only sees requests made through turkart.

Fetching is lazy by design, because that budget is easy to burn:

- `activities sync` **stops at the first page of rides it already has**. Strava
  returns newest first, so a page with nothing new means the rest is older still.
  A repeat sync costs **1 request instead of 5**. `--full` walks everything.
- Tracks and photos are fetched **only for the rides you pick**, from the editor
  or with `--selection`. A full history sync is one request *per activity* —
  ~128 for this account, against a 1,000/day budget shared with everything else.
- Nothing is ever re-fetched: anything already on disk is skipped.

### Repair split recordings

A ride stopped and restarted mid-outing lands in Strava as two activities. On a
poster they read as two separate loops from home, and get double-counted.

```bash
python -m turkart merge suggest            # detect, with evidence
python -m turkart merge suggest --apply    # accept them all
python -m turkart merge apply <idA> <idB> --name "..."
python -m turkart merge list / remove <id>
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

### Photos

```bash
python -m turkart photos sync --scan-only            # how much is there? downloads nothing
python -m turkart photos sync --selection sel.json   # only the rides in a saved selection
python -m turkart photos sync --ids 123 456          # or by id
python -m turkart photos sync --tag 16               # or by the usual filters
python -m turkart photos list
```

Photos are the bulk of the data (~0.6 MB each), so there is no need to fetch them
for every ride. The workflow is: pick rides in the explorer, **Save**, then point
`--selection` at that file. **`--selection`** takes the JSON the explorer's *Save selection*
button writes and syncs exactly those rides — merged rides expand to the
activities they absorbed, since photos belong to the activity they were uploaded
to. **`--scan-only`** finds and indexes the media without downloading a byte, and
reports how many photos and roughly how many MB, so the decision is made on real
numbers.

Scanning and downloading are separate phases: scanning loads an activity page,
downloading only needs the URL the scan recorded. So `--scan-only` followed by a
real sync does the right thing, and re-running a sync downloads only what is
actually missing.

There is no JSON endpoint — `/activities/<id>/photos` is a 404. The media lives
in the activity *page*, as the serialised props of a `MediaThumbnailList` React
component, so this is a scrape and says so loudly if the markup stops matching.

Each item also carries `lat`/`lng` and pixel dimensions, so a photo can be tied
back to the point on the route where it was taken.

Stills land in `data/photos/<activity_id>/` at full resolution (1500×2000 here).
Videos are recorded in the index but not downloaded — only their HLS URL exists.
A merged ride inherits the media of every activity it absorbed.

### Select

```bash
python -m turkart explore --open            # file://
python -m turkart explore --serve --open    # http://localhost:8000
```

Builds `build/explore.html`, a self-contained page (tracks embedded, Leaflet from
CDN) — it runs off the filesystem, so your GPS traces are never uploaded anywhere.

#### Basemaps

Two constraints shape the choice, both found the hard way:

- **CARTO needs an API key.** Without one its tiles return HTTP 200, a normal
  byte count, and a real map — with `API KEY REQUIRED` watermarked across it.
  Only looking at the image reveals this. Put a key in `.secrets/carto` (or
  `$CARTO_KEY`) and **Carto Positron** becomes the default: near-colourless, so
  the tracks carry the image. `--no-carto` builds without it.
- **osm.org blocks requests with no `Referer`** ([why][osm-blocked]), which is
  exactly what a `file://` page sends. Its layer works under `--serve`.

Keyless fallback is **Esri Light Gray Canvas**, which needs nothing and is nearly
as good a poster backdrop. CyclOSM, Esri Topo and OpenTopoMap are also offered.

> The built page embeds the CARTO key, so `build/` is gitignored. Use
> `--no-carto` for a build you intend to share.

[osm-blocked]: https://wiki.openstreetmap.org/wiki/Blocked_tiles

- Filter by date, distance, text, **With kids only**, **Rides only**
- **Multiple named selections**, each an independent poster layout — create,
  duplicate, rename, delete from the bar at the top. Order of selection is the
  order on the poster, and is preserved rather than sorted.
- **Photos per ride**: a selected ride with media shows a thumbnail strip.
  **Hover** a thumbnail for a large preview — no need to place a photo just to
  see what it is. Click to place it on the map, then **drag it anywhere**.
- Each placed photo keeps a **leader line back to where it was taken** (Strava
  records per-photo coordinates; 83 of 86 here have them). *Hide photo lines*
  turns them off for the whole selection. A photo with no GPS fix gets no line.
- **Resize** a photo by its corner grip, or every photo at once with the *size*
  slider. Aspect ratio is preserved from the real image dimensions.
- Positions, sizes and the line toggle are stored per selection, so two layouts
  can arrange the same photos completely differently.
- Selected rides are numbered and colour-coded on the map and in the list.
  Numbers sit at each ride's **turnaround** (its furthest point from the start),
  not at the start — every ride leaves from home, so start-anchored labels stack
  on one pixel. Hovering a row thickens its track and enlarges its pin.
- The stats bar reports whether the selection **fits on one map**, or how many
  sheets it needs (rides more than 40 km apart get their own)
- **Save** writes the selection as JSON; **Load file…** or **Import pasted**
  (edit the textarea) reads one back. The export is the save format, so it
  round-trips completely: ride order, legend title and position, photo positions
  and sizes. An import lands in a new named selection rather than overwriting
  the current one.
- The same file drives `photos sync --selection`, so you can pick rides in the
  browser and fetch only their photos.
- **Fetch photos** — under `--serve`, a selected ride with no photos yet shows a
  *Fetch photos* button, and the toolbar has *Fetch photos for selection*. The
  page calls a small local endpoint (`POST /api/photos/sync`), and the results
  are spliced into the loaded rides, so the selection, framing and every photo
  placement survive — no rebuild, no reload. Over `file://` the buttons are
  disabled with a note, since there is no server to call.

This is deliberately lazy: media is not synced for the whole history up front,
only for the rides actually picked. The same pattern is what a hosted version
would need — a full sync costs one API request per activity.

CLI filters mirror the UI, e.g. `--tag 16 --sport-type-filter Ride --after 2026-01-01`.

#### Legend view

**Legend view** hides the sidebar and puts a legend card on the map, so a single
screenshot captures both. It carries an editable title, and each ride as a
numbered circle in **the same colour as its pin on the map** — the circle beside
a name is the pin out on the route — with name, date, distance and elevation,
plus totals. The card is draggable; its title and position are stored per
selection.

The map chrome gets out of the way: zoom, the layer switcher, the coordinate
readout and the photo resize grips are hidden. **Hold ⇧ or ⌥ to bring them
back**, and `Esc` leaves legend view. A hint says so on entry, so it is not
possible to get stuck.

> ⌘ is deliberately *not* a reveal key, and any pair of modifiers keeps the
> controls hidden — `⌘⇧4` is the macOS screenshot shortcut, so revealing on ⌘,
> or on ⇧ combined with anything, would put the controls into the very
> screenshot legend view exists to take.

> The attribution control deliberately stays visible. OSM and CARTO both require
> attribution, and a screenshot without it is not licensed for sharing.

#### Stored selections

Selections live in the browser under `turkart-selections`. Two older key names
are migrated into it once at load and then deleted:

| key | held |
|---|---|
| `strava-poster-v2` | named sets, before the rename |
| `strava-poster-selection` | a bare array of ids, before selections had names |

Both are one-way and one-time. Once the page has been loaded once on every
browser in use, `LEGACY_SETS` / `LEGACY_FLAT` and the migration branch in
`loadState()` can be deleted outright.

## Privacy

turkart is a local tool. **Nothing you fetch is uploaded anywhere** — no ride,
track, photo or selection ever leaves your machine. The page runs off your own
filesystem or a localhost server.

It does make three kinds of outbound request, and it is worth knowing all three:

- **Strava**, to fetch your own data.
- **A tile provider**, to draw the map — whichever layer is selected: CARTO,
  Esri, OpenStreetMap, CyclOSM (OSM France) or OpenTopoMap.
- **`unpkg.com`**, which serves the Leaflet library on every page load.

Links to individual activities go to `strava.com`, but only if you click them.

**Everything it holds is sensitive, in roughly this order:**

| path | holds | gitignored |
|---|---|---|
| `.secrets/session.json` | your **Strava session cookie** — unscoped, full account access | yes, and `chmod 600` |
| `.secrets/carto` | CARTO API key | yes |
| `LOCAL/` | raw devtools captures, which contain **live cookies** | yes |
| `data/streams/` | GPS traces | yes |
| `data/photos/` | your ride photos, full resolution | yes |
| `build/explore.html` | GPS traces, photo paths, **and the CARTO key** | yes |

Two things are worth being explicit about:

**GPS traces are home addresses.** Every ride starts at your front door, so a
track's start point identifies where you live — and the same is true of any
poster or screenshot made from one. This is the reason `build/` is gitignored
even though it is only a generated artefact, and the reason photos are kept
locally rather than published anywhere. If you share a poster, look at what the
start points give away first.

**The tile provider sees where you are looking.** Panning sends a request per
tile to whichever provider is active, which reveals the area being viewed —
though never the tracks themselves, which are drawn locally on top. `--no-carto`
avoids sending a key with those requests, but not the requests. Self-hosted
tiles would remove this entirely; see the hosting notes.

Photos are downloaded at full resolution (1500×2000 and up), and the coordinates
Strava records for each one are stored in `data/photos/index.json` — that is what
draws a photo's line back to where it was taken, and it is also a precise record
of where you were. Treat `data/photos/` as you would the originals.

Selections live in the browser's `localStorage` under `turkart-selections`, on
your machine only. Clearing site data loses them; **Save** writes a JSON file
that does not depend on the browser.

### Sharing this project

Committing the code is safe: the ignore rules cover credentials, ride data and
generated pages, and every commit so far was checked for leaked values by hand.

There is **no automated secret scanning**, so that guarantee only holds as long as
someone keeps checking. If you fork or publish this, run `git status --short`
before the first commit and actually read it — the trap is a capture file saved
somewhere other than `LOCAL/`, which nothing ignores.

## Making it usable by other people

Being evaluated, not built. The rough shape is either **each person runs their
own instance** (Docker, self-hosted) or **a small shared host for friends**.

The current cookie-based auth cannot be used for either — it would mean handing
someone else's server an unscoped Strava session cookie — so a multi-user version
has to move to the official API with OAuth. That swap has a known cost: Strava's
API does not expose **activity tags**, which is how the "with kids" ride set is
chosen here, so tagging would have to become something turkart stores itself.

The binding constraint is not engineering. Strava's API limits are per
application, and self-service access caps at **10 athletes**; going beyond that
needs Strava's approval, which is explicitly not guaranteed. A handful of friends
fits inside that comfortably; anything public does not, without asking first.

Working notes on all of this are kept locally in `LOCAL/hosting.md`
(unversioned), pending a proper issue.

## Tests

```bash
./tests/run.sh
```

Runs the built page's script headlessly against DOM and Leaflet stubs. The stubs
deliberately model two Leaflet behaviours that real bugs hid behind:

- projecting a coordinate before the map has a centre and zoom **throws**, so
  rendering before the first `fitBounds` is caught rather than passing silently;
- marker dragging starts on `mousedown`, not `pointerdown`, so a control that
  only stops the latter is caught letting a drag through.

Startup is checked both with and without a saved selection — with one there are
enough pins to trigger the projection path, without one there are not.

## Layout

| path | role |
|---|---|
| `turkart/session.py` | parse cURL captures, store/rebuild the browser session |
| `turkart/client.py` | the internal endpoints, paced and error-mapped |
| `turkart/store.py` | `data/` cache, atomic writes |
| `turkart/geo.py` | RDP simplification, bounding boxes, ride clustering |
| `turkart/merge.py` | detect and fold together split recordings |
| `turkart/photos.py` | scrape and download ride media |
| `turkart/fetchapi.py` | the local endpoint the page calls to fetch photos |
| `turkart/explore.py` | builds the selector page |
| `turkart/cli.py` | command line |

Track points in the built page are `[lat, lng, metres_along, altitude_m]` — the
other streams are carried through simplification by index, so a sub-range of a
ride can be measured without reloading full-resolution data.

## Known issues

Raised and not yet fixed — see [ISSUES.md](ISSUES.md). Mostly about legend view
being the composing surface rather than a preview: saving is not reachable from
it, photo sizes do not track zoom, and entering it shifts the map.

## Not built yet

- **Poster renderer** — the end goal. Selections export as JSON with ride
  order, per-ride stats and photo positions, which is its input.
- **Partial rides** — cut a ride at a chosen point and include only part of it
  (the groundwork is in: per-point distance/altitude is already embedded).
- **Patching** a recording that dropped out mid-ride (merging is done).
