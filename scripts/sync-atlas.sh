#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
atlas="${ATLAS_SSH_TARGET:-atlas}"
remote_root="${PKGDB_ATLAS_ROOT:-/apps/pkgdb}"

cd "${repo_root}"
[[ "$(git branch --show-current)" == "main" ]] || { echo "main must be checked out" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "working tree must be clean" >&2; exit 1; }
! ssh "${atlas}" systemctl is-active --quiet pkgdb-maintenance.service || {
  echo "Atlas maintenance is still running" >&2
  exit 1
}

git fetch origin main
git fetch "${atlas}:${remote_root}" HEAD:refs/remotes/atlas/main
git merge-base --is-ancestor origin/main refs/remotes/atlas/main || {
  echo "Atlas and origin/main diverged" >&2
  exit 1
}
git merge --ff-only refs/remotes/atlas/main
git push origin main
ssh "${atlas}" "git -C '${remote_root}' pull --ff-only origin main"

