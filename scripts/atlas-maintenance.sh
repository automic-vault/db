#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
live_db="/var/lib/automic-vault-web/pkg.sqlite"
next_db="${live_db}.next"
live_json="/var/lib/automic-vault-web/db.json"
next_json="${live_json}.next"

cd "${repo_root}"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || {
  echo "refusing maintenance with tracked changes" >&2
  exit 1
}

git pull --ff-only origin main
AVDB_ENRICH_BACKEND=codex-cli \
  scripts/hourly-maintenance.py \
  --enrich-limit "${AVDB_ENRICH_LIMIT:-50}" \
  --enrich-batch-size "${AVDB_ENRICH_BATCH_SIZE:-5}" \
  --sqlite-output "${next_db}" \
  --db-json-output "${next_json}"

scripts/generate-pkg-sqlite.py --check --output "${next_db}"
[[ "$(sqlite3 "${next_db}" 'PRAGMA integrity_check;')" == "ok" ]]
chmod 0640 "${next_db}"
chmod 0640 "${next_json}"
mv -f "${next_db}" "${live_db}"
mv -f "${next_json}" "${live_json}"
curl -fsS http://127.0.0.1:3004/healthz >/dev/null
scripts/nightly-discover-feed.sh
