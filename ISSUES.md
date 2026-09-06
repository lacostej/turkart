# Known issues

Raised but not yet fixed. Causes are recorded where they are already understood,
so the discussion can start from the mechanism rather than the symptom.

Context that shapes several of these: the final image is captured with Firefox's
built-in screenshot tool, on a **fullscreen window**, from **legend view**. So
legend view is not a preview — it is the composing surface, and everything
needed to compose has to work there.

---

## 1. Cannot save while in legend view

Legend view is where photo placement and map framing actually happen, but Save /
Copy JSON / Import live in the sidebar, which legend view hides. So the work done
in the place it is done cannot be exported from there.

Changes are not lost — every edit still writes to `localStorage` — but exporting
means leaving the view, which loses the framing.

Options: put a minimal save control in the legend chrome (revealed with ⇧/⌥
alongside the rest); or float a small always-available control; or auto-write the
selection somewhere the CLI can read without an explicit Save.

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

## 3. Entering legend view moves the map

Confirmed cause: `setLegendMode()` calls `map.invalidateSize()`, whose default is
`pan: true`. Leaflet then pans by the difference between the old and new
container centres — the sidebar is 440px wide, so the map shifts by ~220px.

`invalidateSize({pan: false})` only changes which corner is anchored; the content
still moves relative to the viewport.

Better direction, and what was actually asked for: **do not resize the map at
all.** Make the map fill the window permanently and render the sidebar as an
overlay on top of it. Entering legend view then only hides an overlay — no
resize, no reflow, nothing moves. This also removes the `invalidateSize` call and
the pin re-layout that follows it.

## 4. Legend toggle and close are in different places

"Legend view" sits in the sidebar toolbar; "←" sits top-right of the map. Enter
and exit should be the same control in the same place, so it reads as one toggle.

Follows naturally from 3: with the sidebar as an overlay, one button in a fixed
position can toggle it.

## 5. Fullscreen capture

Final capture is Firefox's screenshot on a fullscreen window. Worth verifying
that nothing in the layout depends on window size in a way that shifts between
the working window and fullscreen — the legend card is positioned in pixels from
the map's top-left, so it should hold, but photo positions are geographic and the
visible framing will differ. Related to 2 and 3.

---

*More to come — this list is expected to grow before any of it is acted on.*
