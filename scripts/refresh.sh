#!/usr/bin/env bash
# Refresh today's edition from RSS (and merge On X if raw dumps are present),
# then commit and push only when data files changed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DATE="${1:-$(TZ=America/New_York date +%F)}"
RAW_DIR="data/x-raw/$DATE"

python3 scripts/refresh_feeds.py --date "$DATE"

if [[ -d "$RAW_DIR" ]] && compgen -G "$RAW_DIR/*.json" > /dev/null; then
  python3 scripts/fetch_x.py --date "$DATE" --raw-dir "$RAW_DIR"
else
  echo "refresh.sh: no raw X dumps at $RAW_DIR; skipped fetch_x.py"
fi

git add -- data/index.json "data/${DATE}.json"
if git diff --cached --quiet; then
  echo "refresh.sh: no data changes; nothing to commit or push"
  exit 0
fi

git -c user.name=dys5239 -c user.email=dys5239@users.noreply.github.com \
  commit -m "Refresh edition for ${DATE}"
git push origin main
echo "refresh.sh: pushed refresh for ${DATE}"
