#!/usr/bin/env bash
# Run the page script headlessly against DOM/Leaflet stubs.
#
# The stubs deliberately model two Leaflet behaviours that real bugs hid behind:
# projecting coordinates before the map has a view throws, and marker dragging
# starts on `mousedown`, not `pointerdown`.
set -euo pipefail
cd "$(dirname "$0")/.."
PAGE=$(mktemp -t page).js
python3 -c "
import re,sys
js = re.findall(r'<script>(.*?)</script>', open('build/explore.html').read(), re.S)[0]
open(sys.argv[1], 'w').write(js)
" "$PAGE"
node --check "$PAGE" && echo 'syntax OK'

echo '--- startup: no saved selection ---'
cat tests/harness.js "$PAGE" | node - && echo '  no crash'

echo '--- startup: with a saved selection (needs a view before render) ---'
IDS=$(.venv/bin/python -c "
from turkart.store import Store
from turkart.explore import build_rides
r = build_rides(Store())[:3]
print(','.join(str(x['id']) for x in r))")
SEED=$(mktemp -t seed).js
printf 'localStorage.setItem(\"strava-poster-v2\", JSON.stringify({active:\"S\",sets:{S:{ids:[%s],photos:{},leaders:true,size:72}}}));\n' "$IDS" > "$SEED"
cat tests/harness.js "$SEED" "$PAGE" | node - && echo '  no crash'

echo '--- features ---'
cat tests/harness.js "$PAGE" tests/features.js | node -
rm -f "$PAGE" "$SEED"
