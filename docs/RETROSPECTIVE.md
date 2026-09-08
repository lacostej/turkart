# Retrospective — building turkart

Written after 24 commits, looking at where time actually went. The point is not
to list bugs; it is to find the few causes that produced most of them, and turn
those into rules worth keeping.

## The numbers

| | |
|---|---|
| commits | 24 |
| commits that repaired something an earlier commit shipped | **9** |
| distinct defects | 16 |
| **found by the user** | **11** |
| found by me | 5 |

Nearly every defect the user found was **visual or interaction** — a watermark on
a tile, a control behind a panel, a cursor, a snap, a stacked pin, a scroll
reset. Nearly every defect I found myself was **logic or state** — an index
poisoned, a blank label, a shadowed stub.

That split is the whole story. I could see logic. I could not see the page.

---

## 1. I could have seen the page the entire time

Browser automation was declined early on. I accepted that and carried on, quietly
substituting weaker checks: HTTP status codes, byte counts, `node --check`, DOM
stubs. I never asked whether there was another way to look at the output.

There was. Chrome is installed on this machine and takes screenshots from the
shell:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --hide-scrollbars \
  --virtual-time-budget=6000 --window-size=1600,1000 \
  --screenshot=out.png http://localhost:8137/explore.html
```

Ten seconds. The first screenshot I ever took of this page immediately showed a
defect **neither of us had noticed**: the *With kids only* / *Rides only*
checkboxes wrap into stacked uppercase fragments, because `.tog` inherits the
`text-transform` and sizing of `.filters label`. It had looked like that since
the day it was added.

Of the eleven defects the user reported, a screenshot after each UI change would
plausibly have caught seven or eight before they ever saw them.

> **Rule.** When the natural way to verify something is unavailable, find a
> substitute *before* building more on top of it. Do not silently downgrade to a
> weaker proxy and keep going. A declined tool is a gap to close, not a
> constraint to accept.

## 2. Verify at the layer where the failure would show

The recurring shape: I checked a layer *below* the one that could fail.

| what I checked | what was actually wrong |
|---|---|
| tile returned `200`, plausible byte count | the image said **API KEY REQUIRED** across it |
| the serve command started | it never bound the port; `head -3` had eaten the traceback |
| `node --check` passed | the zoom control was rendering behind the sidebar |
| the button existed in the DOM | it rendered blank until first clicked |

Each of those checks was true. None of them was the claim I was making.

> **Rule.** State the claim, then pick the check that could falsify *that claim*.
> If the deliverable is an image, look at the image. If it is a listening socket,
> connect to it. A `200` is not a picture; a started process is not a served port.

> **Rule.** Never pipe a command whose failure you need to see through `head`,
> `tail`, or `grep`. SIGPIPE truncated a traceback and I reported success on a
> crash.

## 3. A regression test must be shown to fail without the fix

Twice I wrote a test for a bug I had just fixed, watched it pass, and moved on.
Both times it was worthless:

- **The resize snap.** The test re-implemented what the handler does instead of
  calling it. Reintroducing the bug left it green.
- **The map shift.** The Leaflet stub declared `invalidateSize` twice, and the
  later no-op shadowed the version that models panning. Reintroducing the bug
  left it green.

Both only became real tests when I deliberately reverted the fix and watched
them go red.

> **Rule.** A regression test is not finished when it passes. Revert the fix,
> confirm it **fails**, restore. A test that has never failed has proved nothing.

## 4. Stubs must model what the real thing refuses to do

The test harness was a source of false confidence, not confidence:

- `latLngToLayerPoint` never threw, so it missed Leaflet's *"Set map center and
  zoom first"* — a crash on every load with a saved selection.
- `getElement()` returned `null`, so the resize grip never existed in tests, and
  the resize bug sailed through.
- A duplicate key in an object literal silently shadowed the useful stub.

A stub that cannot fail cannot catch a failure.

> **Rule.** When stubbing a dependency, write down what the real one *refuses* to
> do — what throws, what returns undefined, which event it actually listens to —
> and make the stub refuse the same way. Model the constraints, not the happy
> path.

The Leaflet lesson generalises: `mousedown` vs `pointerdown`, `cursor: grab`
inherited by children, projection requiring a view. **Read the library's source
for the specific behaviour rather than assuming its API is the obvious one.**
Every time I did read it, it took two minutes and settled the question.

## 5. Do not state third-party facts from memory

I wrote Strava's rate limits from memory and computed conclusions from them. Both
numbers were wrong in ways that mattered: the real non-upload budget is **half**
what I said, and there is a **10-athlete cap** on self-service access that I did
not know existed — which inverted the hosting conclusion from "engineering
problem" to "needs Strava's approval first".

I did mark them ⚠️, which was right. But I still reasoned on top of them.

> **Rule.** Third-party limits, terms and endpoints get verified at the time of
> use. If a number is unverified, no conclusion may be computed from it — say
> what needs checking and stop there.

## 6. Run a heuristic on real data before writing down why it works

The merge detector's docstring claimed its design distinguished a split recording
from two separate rides. It did not: a ride ending at home followed by one
starting at home has the same tiny junction gap. Running it produced two
confident false positives, including pairing a ride *with kids* to a later *solo*
ride.

I had written the rationale before reading the output.

> **Rule.** For anything heuristic, print every match and read them **before**
> documenting why the approach works. The rationale is a conclusion, not a
> premise.

## 7. Smaller ones, same shape

- **Decide what to keep when first parsing an external payload.** I discarded
  photo `lat`/`lng` and dimensions on the first pass and had to re-scan every
  activity to get them back. Dump the full field set once, then choose.
- **Distinguish "known about" from "have".** `--scan-only` wrote the index, so
  the real sync afterwards skipped everything and reported success while
  downloading nothing. A skip condition must test the resource you actually
  need.
- **Don't build durable infrastructure in scratch.** An entire early test suite
  was lost hand-copying from a scratch directory into the repo, leaving set
  independence and key migration uncovered for several commits. The moment a
  script is worth running twice, it belongs in the repo.
- **Await what you assert on.** Twice, tests asserted on fire-and-forget async
  work that had not settled, hitting an in-flight de-duplication guard and
  silently no-opping.

---

## What worked, and should keep happening

Not everything needs changing. These paid off repeatedly:

- **Reproduce before fixing.** The startup crash was reproduced in the harness
  first, so the fix was aimed at a demonstrated failure rather than a guess.
- **Measure instead of estimating.** "~128 API calls to onboard one user" came
  from counting this account's real data. It changed the hosting conclusion.
- **Scan for secrets before every commit**, by explicit path — and say plainly
  that it is manual, rather than implying a guarantee that does not exist.
- **Look at any binary before committing it.** Reading `sample1.png` before
  adding it confirmed it was the Bali ride, not an Oslo one that would have put a
  home address in a shareable file.
- **Correct the record when a number turns out wrong**, including in the commit
  message, rather than quietly fixing it.

## The loop

This file is the mechanism, not a one-off. When something breaks:

1. Write down the **claim** that turned out false, not just the symptom.
2. Ask which check would have falsified it, and why it was not run.
3. If the answer generalises, add a rule here. If it is specific to this project,
   add it to `CLAUDE.md`.
4. If the rule would have caught two or more past defects, it is worth having.
   One is a story; two is a pattern.
