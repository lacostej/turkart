# turkart — working notes

Project-specific only. The general engineering rules live in the global
`CLAUDE.md`; the reasoning behind both is in `docs/RETROSPECTIVE.md`.

## Verifying UI changes

Most of this project is a generated web page, and most of its defects have been
visual. `node --check` and HTTP status codes do not see them. **The strong check
here is a screenshot** — take one before saying a UI change works:

```bash
.venv/bin/python -m turkart explore --serve 8137 &   # if not already serving
tests/shot.sh /tmp/shot.png                          # then read the image
./tests/run.sh                                       # 115 checks
```

This has already found two defects that CSS reasoning alone got wrong — the
second only appeared *after* the first "fix".

## This repo's test harness

- `tests/harness.js` deliberately throws when projecting coordinates before the
  map has a view, and treats marker drags as `mousedown` rather than
  `pointerdown`. Both model real Leaflet behaviour that hid real bugs; do not
  "simplify" them back to no-ops.
- Async suites in `tests/features.js` are queued in `SUITES` and awaited in
  order. They share `RIDES`, `state` and the fetch stub, so interleaving them
  produces false results. Await anything you assert on — the fetch helpers
  de-duplicate in-flight ids and will silently no-op otherwise.
- Test infrastructure lives in `tests/`, never in a scratch directory. A whole
  suite was lost hand-copying it in.

## Strava

- Limits are **per application**: 100/15min and **1,000/day** non-upload, which
  is all turkart makes. The internal endpoints send **no rate-limit headers**, so
  `turkart usage` is our own count. Verify anything else at
  developers.strava.com rather than recalling it.
- **Fetch lazily.** Tracks and photos cost one request per activity. Fetch for
  the rides actually selected — from the editor or `--selection` — never the
  whole history. Say what a bulk fetch will cost before running one.
- **Build user-facing capability in the editor page first**, with the CLI
  mirroring it. Filtering, photo fetching and track fetching were each shipped
  CLI-only and each had to be added to the page afterwards.
- When first parsing a scraped payload, **dump the full field set and choose
  deliberately**. Photo coordinates were discarded on the first pass and cost a
  full re-scan of every activity.
- Heuristics over ride data (merge detection, clustering) get **run against real
  rides and every match read** before their rationale is written down. The merge
  detector's first docstring claimed a property the code did not have.

## Committing

- **Scan for secrets before every commit — there is no automated check:**
  ```bash
  grep -rInE "_strava4_session=[a-z0-9]{16}|cb1_[a-z0-9_]{16}|CloudFront-Signature=[A-Za-z0-9]{16}" \
       <paths being committed>
  ```
  The pattern matches *values*, not prefixes, so it does not flag this file for
  documenting itself. An earlier version did, twice — a check that cries wolf
  stops being read.
- `.secrets/`, `data/`, `build/` and `LOCAL/` are ignored and hold cookies, GPS
  traces, photos and the CARTO key. `build/explore.html` embeds both the traces
  and the key — never commit it.
- **Read any binary before committing it.** GPS traces are home addresses: a
  poster made from an Oslo ride puts one in a shareable file.
