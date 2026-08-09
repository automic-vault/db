#!/usr/bin/env bash

# Atomically publish the repository's validated Discover feed to the av-web
# state directory. Run this on Atlas after updating the pkg.so working tree.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
feed_source="${PKG_DISCOVER_FEED_SOURCE:-${repo_root}/www/feed}"
state_root="${PKG_DISCOVER_FEED_STATE_ROOT:-/var/lib/automic-vault-web/feed}"
releases_root="${state_root}/releases"
current_link="${state_root}/current"
owner="${PKG_DISCOVER_FEED_OWNER:-automic-vault-web}"
group="${PKG_DISCOVER_FEED_GROUP:-automic-vault-web}"

usage() {
  cat <<'EOF'
Usage: scripts/publish-discover-feed-atlas.sh

Validate and atomically publish www/feed to av-web's Discover feed state.
Run this script on Atlas; it does not restart av-web or alter the package DB.

Optional environment:
  PKG_DISCOVER_FEED_SOURCE      Feed source (default: <repo>/www/feed)
  PKG_DISCOVER_FEED_STATE_ROOT  State root (default: /var/lib/automic-vault-web/feed)
  PKG_DISCOVER_FEED_OWNER       Published-file owner (default: automic-vault-web)
  PKG_DISCOVER_FEED_GROUP       Published-file group (default: automic-vault-web)
EOF
}

case "${1:-}" in
  "") ;;
  --help|-h) usage; exit 0 ;;
  *) echo "error: unknown argument: $1" >&2; usage >&2; exit 1 ;;
esac

for command in python3 sudo; do
  command -v "${command}" >/dev/null 2>&1 || {
    echo "error: missing required command: ${command}" >&2
    exit 1
  }
done
[[ -f "${feed_source}/v1.json" && -f "${feed_source}/v2.json" ]] || {
  echo "error: feed source must contain v1.json and v2.json: ${feed_source}" >&2
  exit 1
}
[[ -x "${repo_root}/scripts/update-discover-feed" ]] || {
  echo "error: Discover feed validator is missing: ${repo_root}/scripts/update-discover-feed" >&2
  exit 1
}

cd "${repo_root}"
"${repo_root}/scripts/update-discover-feed" --check

stamp="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)"
release="${releases_root}/${stamp}"
tmp_release="${releases_root}/.${stamp}.staging"
previous="$(sudo readlink -f "${current_link}" 2>/dev/null || true)"

cleanup() {
  sudo rm -rf -- "${tmp_release}"
}
trap cleanup EXIT

sudo install -d -o "${owner}" -g "${group}" -m 0755 "${releases_root}"
sudo rm -rf -- "${tmp_release}"
sudo install -d -o "${owner}" -g "${group}" -m 0755 "${tmp_release}"
sudo cp -a --no-preserve=ownership "${feed_source}/." "${tmp_release}/"
sudo chown -R "${owner}:${group}" "${tmp_release}"
sudo find "${tmp_release}" -type d -exec chmod 0755 {} +
sudo find "${tmp_release}" -type f -exec chmod 0644 {} +

# A second check catches a release that changed between the source validation
# and the copy (for example, a concurrent generator invocation).
sudo test -f "${tmp_release}/v1.json"
sudo test -f "${tmp_release}/v2.json"
sudo mv -- "${tmp_release}" "${release}"
sudo ln -sfn "${release}" "${current_link}"

# Keep exactly the current release and its immediate predecessor. Cleanup is
# deliberately after the symlink swap, so a failed staging never affects live
# content and the previous release remains available for a complete publish.
if [[ -n "${previous}" && "${previous}" != "${release}" && -d "${previous}" ]]; then
  sudo find "${releases_root}" -mindepth 1 -maxdepth 1 -type d \
    ! -path "${release}" ! -path "${previous}" -exec rm -rf -- {} +
fi

trap - EXIT
printf 'Discover feed published: %s\n' "${release}"
