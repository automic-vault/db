#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rebuild_sqlite="${PKGDB_REBUILD_SQLITE:-false}"
release_root="/apps/automic-vault-web/releases"
current_release="/apps/automic-vault-web/current"
state_dir="/var/lib/automic-vault-web"
live_db="${state_dir}/pkg.sqlite"
next_db="${live_db}.next"
previous_db="${live_db}.previous"
service="automic-vault-web.service"
timer="pkgdb-maintenance.timer"

usage() {
  cat <<'EOF'
Usage: scripts/deploy-atlas.sh [--rebuild-sqlite]

Build and deploy the pkg.so origin from the current Atlas working tree.
Run this script on Atlas; it does not use SSH or fetch code from another host.

Options:
  --rebuild-sqlite  Generate, validate, and atomically install a new pkg.sqlite
  --help, -h        Show this help

PKGDB_REBUILD_SQLITE=true remains supported for compatibility.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild-sqlite)
      rebuild_sqlite=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "${rebuild_sqlite}" != "true" && "${rebuild_sqlite}" != "false" ]]; then
  echo "PKGDB_REBUILD_SQLITE must be true or false" >&2
  exit 1
fi

for command in cargo curl git sudo systemctl; do
  command -v "${command}" >/dev/null 2>&1 || {
    printf 'error: missing required command: %s\n' "${command}" >&2
    exit 1
  }
done
if [[ "${rebuild_sqlite}" == "true" ]]; then
  command -v sqlite3 >/dev/null 2>&1 || {
    echo "error: missing required command: sqlite3" >&2
    exit 1
  }
fi
[[ -f /etc/automic-vault-web.env ]] || {
  echo "error: /etc/automic-vault-web.env is missing; run this deployment on Atlas" >&2
  exit 1
}
systemctl cat "${service}" >/dev/null 2>&1 || {
  printf 'error: %s is not installed; run this deployment on Atlas\n' "${service}" >&2
  exit 1
}

cd "${repo_root}"
revision="$(git rev-parse --short HEAD)"
if [[ -n "$(git status --porcelain)" ]]; then
  revision="${revision}+working-tree"
fi
printf 'Deploying Atlas origin from %s (%s)\n' "${repo_root}" "${revision}"

tmp_dir="$(mktemp -d /var/tmp/pkgdb-deploy.XXXXXX)"
cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

cargo build --release --manifest-path "${repo_root}/Cargo.toml" -p av-web

if [[ "${rebuild_sqlite}" == "true" ]]; then
  candidate_db="${tmp_dir}/pkg.sqlite"
  "${repo_root}/scripts/generate-pkg-sqlite.py" --output "${candidate_db}"
  "${repo_root}/scripts/generate-pkg-sqlite.py" --check --output "${candidate_db}"
  [[ "$(sqlite3 "${candidate_db}" 'PRAGMA integrity_check;')" == "ok" ]] || {
    echo "error: generated SQLite integrity check failed" >&2
    exit 1
  }
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
release="${release_root}/${stamp}"
previous_release="$(readlink -f "${current_release}" 2>/dev/null || true)"
release_swapped=false
database_swapped=false

rollback() {
  local exit_code=$?
  trap - ERR
  set +e
  echo "deployment failed; rolling back Atlas origin" >&2
  sudo systemctl stop "${service}"
  if [[ "${release_swapped}" == "true" && -n "${previous_release}" ]]; then
    sudo ln -sfn "${previous_release}" "${current_release}"
  fi
  if [[ "${database_swapped}" == "true" && -f "${previous_db}" ]]; then
    sudo cp --reflink=auto --preserve=mode,ownership,timestamps "${previous_db}" "${live_db}"
  fi
  sudo systemctl start "${service}"
  exit "${exit_code}"
}
trap rollback ERR

sudo install -d -o root -g root -m 0755 "${release}"
sudo install -o root -g root -m 0755 \
  "${repo_root}/target/release/av-web" "${release}/av-web"
sudo install -o root -g root -m 0644 \
  "${repo_root}/systemd/automic-vault-web.service" /etc/systemd/system/automic-vault-web.service
sudo install -o root -g root -m 0644 \
  "${repo_root}/systemd/pkgdb-maintenance.service" /etc/systemd/system/pkgdb-maintenance.service
sudo install -o root -g root -m 0644 \
  "${repo_root}/systemd/pkgdb-maintenance.timer" /etc/systemd/system/pkgdb-maintenance.timer
sudo install -d -o root -g automic-vault-web -m 2770 "${state_dir}"

if [[ "${rebuild_sqlite}" == "true" ]]; then
  sudo install -o automic-vault-web -g automic-vault-web -m 0640 \
    "${candidate_db}" "${next_db}"
  if [[ -f "${live_db}" ]]; then
    sudo cp --reflink=auto --preserve=mode,ownership,timestamps "${live_db}" "${previous_db}"
  fi
fi

sudo systemctl daemon-reload
sudo systemctl enable "${service}" "${timer}" >/dev/null
sudo systemctl stop "${service}"
sudo ln -sfn "${release}" "${current_release}"
release_swapped=true
if [[ "${rebuild_sqlite}" == "true" ]]; then
  sudo mv -f "${next_db}" "${live_db}"
  database_swapped=true
fi
sudo systemctl start "${service}"
sudo systemctl start "${timer}"

for _attempt in {1..20}; do
  if curl -fsS http://127.0.0.1:3004/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
curl -fsS http://127.0.0.1:3004/healthz >/dev/null
sudo bash -c '
  set -a
  source /etc/automic-vault-web.env
  header_name="${AV_WEB_ORIGIN_HEADER:-X-Automic-Vault-Origin}"
  curl -fsS -H "${header_name}: ${AV_WEB_ORIGIN_SECRET}" http://127.0.0.1:3004/pkg/ >/dev/null
'

trap - ERR
printf 'Atlas package origin deployed: %s\n' "${release}"
if [[ "${rebuild_sqlite}" == "true" ]]; then
  printf 'SQLite installed: %s (previous copy: %s)\n' "${live_db}" "${previous_db}"
fi
