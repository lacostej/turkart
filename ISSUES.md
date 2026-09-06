# Known issues

Raised but not yet fixed. Causes are recorded where they are already understood,
so the discussion can start from the mechanism rather than the symptom.

Context that shapes several of these: the final image is captured with Firefox's
built-in screenshot tool, on a **fullscreen window**, from **legend view**. So
legend view is not a preview — it is the composing surface, and everything
needed to compose has to work there.

---

## 1. Cannot save while in legend view — *partly fixed*

Legend view is where photo placement and map framing actually happen, but Save /
Copy JSON / Import live in the sidebar, which legend view hides. So the work done
in the place it is done cannot be exported from there.

Changes are not lost — every edit still writes to `localStorage` — but exporting
means leaving the view, which loses the framing.

**Save view** now exists in both the sidebar and the legend chrome (hold ⇧/⌥),
so the framing can be captured without leaving the view. Exporting the JSON from
legend view still is not possible — deferred, since the map no longer moves when
switching, so leaving to export costs nothing.

## 2. Photo size does not track zoom

Photo markers are sized in **pixels**, so zooming changes their size relative to
the map. The composition arranged at one zoom is wrong at another, which matters
because framing is adjusted after placement.

Cause: `photoBox()` returns a pixel box, and the marker's `iconSize` is set from
it once per render.

Fix direction: store the size in **metres** rather than pixels and derive pixels
per zoom level, recomputing on `zoomend` — the same approach already used for pin
de-collision, which is stable across zoom for exactly this reason. Needs a
migration for placements that currently store a pixel size.

Open question: should a photo scale *fully* with zoom (fixed ground size), or
partially, so it stays legible when zoomed out?

## 3. Entering legend view moves the map — *fixed*

Confirmed cause: `setLegendMode()` calls `map.invalidateSize()`, whose default is
`pan: true`. Leaflet then pans by the difference between the old and new
container centres — the sidebar is 440px wide, so the map shifts by ~220px.

`invalidateSize({pan: false})` only changes which corner is anchored; the content
still moves relative to the viewport.

Fixed by that route: the map fills the window permanently and the sidebar floats
over it, so toggling changes nothing about the map's size and there is nothing to
re-measure. `invalidateSize` is gone. Fitting now pads by the sidebar width in
editor mode so fitted rides do not land underneath it.

## 4. Legend toggle and close are in different places — *fixed*

"Legend view" sits in the sidebar toolbar; "←" sits top-right of the map. Enter
and exit should be the same control in the same place, so it reads as one toggle.

Fixed: one button, fixed at the window's top-right, in the same pixel position
both ways. Visible in editor mode; in legend view it reappears in that same spot
while ⇧/⌥ is held, so screenshots stay clean. The sidebar's "Legend view" button
and the map's "←" are both gone — there is one control now, not two.

## 5. Fullscreen capture

Final capture is Firefox's screenshot on a fullscreen window. Worth verifying
that nothing in the layout depends on window size in a way that shifts between
the working window and fullscreen — the legend card is positioned in pixels from
the map's top-left, so it should hold, but photo positions are geographic and the
visible framing will differ. Related to 2 and 3.

## 6. Tracks still need a CLI step — *fixed*

Photos are now fetched from the UI on demand, but streams are not, and a ride
with no stream does not appear in the page at all — so `streams fetch` remains a
prerequisite before the editor is useful.

The chicken-and-egg: the page only knows about rides it can draw, so to offer
"fetch this ride's track" it would first have to list rides it *cannot* draw.
That means `build_rides` including trackless rides as placeholders, and the list
distinguishing "not downloaded" from "no GPS". Not hard, but a bigger change than
the photo endpoint, which only had to fill in something the page already showed.

Fixed by that route: `build_rides` now emits a record for every activity, with
`hasTrack: false` and empty geometry for those not downloaded. They appear in the
list, greyed, with a **Fetch track** button, and clicking one fetches its track
and then selects it. `POST /api/streams/sync` returns records rebuilt by
`build_rides`, so a fetched ride is byte-identical to one present at build time —
verified. A ride Strava recorded without GPS is marked distinctly and never
offered, since no track exists to ask for.

First-run setup is now auth plus `activities sync`; tracks and photos can both be
pulled from the editor.

---

*More to come — this list is expected to grow before any of it is acted on.*
