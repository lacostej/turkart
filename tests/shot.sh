#!/usr/bin/env bash
# Screenshot the built page, so UI changes can be looked at rather than inferred.
#
# Most defects in this project's history were visual -- a control behind a panel,
# a watermarked tile, a blank label -- and none of them are visible to
# `node --check` or an HTTP status code. This exists so that is no longer an
# excuse. See docs/RETROSPECTIVE.md.
#
#   tests/shot.sh [output.png] [url] [WIDTHxHEIGHT]
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-/tmp/turkart-shot.png}"
URL="${2:-http://localhost:8137/explore.html}"
SIZE="${3:-1600,1000}"
SIZE="${SIZE/x/,}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

[ -x "$CHROME" ] || { echo "Chrome not found at $CHROME" >&2; exit 1; }

if ! curl -sf -o /dev/null "$URL"; then
  echo "nothing serving $URL -- start it with:" >&2
  echo "  .venv/bin/python -m turkart explore --serve 8137" >&2
  exit 1
fi

# virtual-time-budget lets tiles and photos load before the frame is captured.
"$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --virtual-time-budget=6000 --window-size="$SIZE" \
  --screenshot="$OUT" "$URL" 2>/dev/null || true

[ -s "$OUT" ] || { echo "screenshot failed" >&2; exit 1; }
echo "$OUT  ($(du -h "$OUT" | cut -f1), ${SIZE/,/x})"
