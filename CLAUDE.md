# turkart — working notes

Rules earned by getting them wrong. The reasoning is in `docs/RETROSPECTIVE.md`;
this is the short version.

## Verifying UI changes

Most of this project is a generated web page, and most of its defects have been
visual. `node --check` and HTTP status codes do not see them.

**After any change to `turkart/explore.py`'s template, look at the page:**

```bash
.venv/bin/python -m turkart explore --serve 8137 &   # if not already serving
tests/shot.sh /tmp/shot.png                          # then read the image
```

Then run the suite: `./tests/run.sh`.

A screenshot is cheap and has already found a defect that months of status-code
checks did not. Take one before saying a UI change works.

## Claims and checks

- **Check the layer the claim is about.** A tile returning `200` is not a tile
  that renders — CARTO served a watermarked image at `200` with a plausible byte
  count. A process that started is not a port that bound.
- **Never pipe a command whose failure matters through `head`/`tail`/`grep`.**
  SIGPIPE has already hidden a traceback and turned a crash into a reported
  success.
- **Measure rather than estimate** when the data is on disk. The per-user API
  cost was counted, not guessed, and the real number changed the conclusion.

## Tests

- **A regression test must be shown to fail without the fix.** Revert, confirm
  red, restore. Two tests here passed with their bug reintroduced before this was
  enforced.
- **Stubs must model what the real dependency refuses to do**, not just its happy
  path — what throws, what returns null, which event it actually binds. See
  `tests/harness.js`: it deliberately throws when projecting before the map has a
  view, and treats marker drags as `mousedown`, because both hid real bugs.
- **Async suites in `tests/features.js` are queued and awaited in order.** They
  share `RIDES`, `state` and the fetch stub; interleaving them produces false
  results. Await anything you assert on — the fetch helpers de-duplicate
  in-flight ids and will silently no-op.
- Keep test infrastructure in `tests/`, never in a scratch directory. A suite was
  lost that way.

## Strava

- **Do not state Strava's limits, terms or endpoints from memory** — check
  developers.strava.com at the time of use. Getting this wrong inverted a whole
  analysis. Nothing may be concluded from an unverified number.
- Limits are **per application**: 100/15min and **1,000/day** non-upload, which is
  all turkart makes. The internal endpoints send **no rate-limit headers**, so
  `turkart usage` is our own count.
- **Fetch lazily.** Tracks and photos cost one request per activity. Fetch for
  the rides actually selected, never the whole history.
- When first parsing a scraped payload, **dump the full field set and choose
  deliberately**. Photo coordinates were discarded on the first pass and cost a
  full re-scan.
- Heuristics over ride data (merge detection, clustering) get **run against real
  rides and every match read** before their rationale is written down.

## Committing

- Stage by explicit path. Never `git add -A` or `.`.
- **Scan for secrets before every commit** — there is no automated check:
  ```bash
  grep -rIn "_strava4_session=\|cb1_\|CloudFront-Signature" <paths being committed>
  ```
- `.secrets/`, `data/`, `build/` and `LOCAL/` are ignored and hold cookies, GPS
  traces, photos and the CARTO key. `build/explore.html` embeds both traces and
  the key — never commit it.
- **Read any binary before committing it.** GPS traces are home addresses; a
  poster from an Oslo ride is a home address in a shareable file.
