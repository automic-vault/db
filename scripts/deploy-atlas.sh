#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
atlas="${ATLAS_SSH_TARGET:-atlas}"
remote_root="${PKGDB_ATLAS_ROOT:-/apps/pkgdb}"
bundle="$(mktemp -t pkgdb.XXXXXX.bundle)"
trap 'rm -f "${bundle}"' EXIT

cd "${repo_root}"
[[ -z "$(git status --porcelain)" ]] || { echo "working tree must be clean" >&2; exit 1; }
git bundle create "${bundle}" HEAD
scp "${bundle}" "${atlas}:/var/tmp/pkgdb.bundle"

ssh "${atlas}" bash -s -- "${remote_root}" <<'REMOTE'
set -euo pipefail
remote_root="$1"

sudo dnf install -y bubblewrap sqlite >/dev/null
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh -o /var/tmp/install-uv.sh
  sudo env UV_INSTALL_DIR=/usr/local/bin sh /var/tmp/install-uv.sh >/dev/null
  rm -f /var/tmp/install-uv.sh
fi
if ! command -v codex >/dev/null; then
  sudo npm install --global @openai/codex >/dev/null
fi

if [[ ! -d "${remote_root}/.git" ]]; then
  sudo mkdir -p "${remote_root}"
  sudo chown ec2-user:ec2-user "${remote_root}"
  git clone /var/tmp/pkgdb.bundle "${remote_root}"
else
  [[ -z "$(git -C "${remote_root}" status --porcelain)" ]] || {
    echo "Atlas pkgdb checkout is dirty" >&2
    exit 1
  }
  git -C "${remote_root}" fetch /var/tmp/pkgdb.bundle HEAD
  git -C "${remote_root}" merge --ff-only FETCH_HEAD
fi
rm -f /var/tmp/pkgdb.bundle

git -C "${remote_root}" switch -C main
git -C "${remote_root}" remote set-url origin https://github.com/automic-vault/db.git
git -C "${remote_root}" config user.name "Codex on Atlas"
git -C "${remote_root}" config user.email "codex@atlas"
cargo build --release --manifest-path "${remote_root}/Cargo.toml" -p av-web

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
release="/apps/automic-vault-web/releases/${stamp}"
sudo install -d -o root -g root -m 0755 "${release}"
sudo install -o root -g root -m 0755 "${remote_root}/target/release/av-web" "${release}/av-web"
sudo ln -sfn "${release}" /apps/automic-vault-web/current
sudo install -o root -g root -m 0644 "${remote_root}/systemd/automic-vault-web.service" /etc/systemd/system/automic-vault-web.service
sudo install -o root -g root -m 0644 "${remote_root}/systemd/pkgdb-maintenance.service" /etc/systemd/system/pkgdb-maintenance.service
sudo install -o root -g root -m 0644 "${remote_root}/systemd/pkgdb-maintenance.timer" /etc/systemd/system/pkgdb-maintenance.timer
sudo install -d -o root -g automic-vault-web -m 2770 /var/lib/automic-vault-web
sudo systemctl daemon-reload
sudo systemctl enable automic-vault-web.service pkgdb-maintenance.timer >/dev/null
sudo systemctl restart automic-vault-web.service
sudo systemctl start pkgdb-maintenance.timer
curl -fsS http://127.0.0.1:3004/healthz >/dev/null
REMOTE

echo "Atlas package origin deployed. Run 'ssh ${atlas} codex login --device-auth' before the first maintenance job."
