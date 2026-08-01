# Package Metadata Database

The metadata and live SQLite origin behind Automic Vault’s `/pkg/` catalog.

> [!IMPORTANT]
> CLIs only. Library-only and transitive dependencies don’t belong here.

## Build

```sh
$ scripts/build.py --refresh
$ scripts/build-db.py --refresh --npm-full-scan-parts=7
$ scripts/generate-pkg-sqlite.py
```

Downloaded and intermediate data stays in `cache/`. The committed YAML stages
are:

- `deterministic/`: source-backed generator output
- `agents/`: schema-validated Codex enrichment
- `human-override/`: hand-authored corrections
- `combined/`: the final merged metadata

Precedence is deterministic < agents < human override. Package-page data,
search documents, hubs, localized responses, and sitemaps are compiled into
`cache/pkg.sqlite`. There is no public `db.json` export.

## Run the `/pkg/` origin

```sh
$ AV_WEB_DB_PATH=cache/pkg.sqlite cargo run --release -p av-web
av-web listening on 127.0.0.1:3004
```

`AV_WEB_DB_PATH` selects the SQLite file. The production service defaults to
`/var/lib/automic-vault-web/pkg.sqlite`; origin-header settings remain in
`/etc/automic-vault-web.env` on Atlas.

## Atlas

Deploy code and systemd units from Pangolin:

```sh
$ scripts/deploy-atlas.sh
$ ssh atlas codex login --device-auth
```

`pkgdb-maintenance.timer` refreshes metadata nightly, runs bounded Codex
enrichment, generates and validates `pkg.sqlite.next`, then atomically replaces
the live database. `av-web` opens SQLite per request, so successful swaps need
no restart. Failed builds leave the previous database serving.

Inspect it with:

```sh
$ ssh atlas systemctl status pkgdb-maintenance.timer automic-vault-web.service
$ ssh atlas journalctl -u pkgdb-maintenance.service -n 100
```

Atlas has no GitHub credentials. Retrieve its metadata commits and push them
from Pangolin:

```sh
$ scripts/sync-atlas.sh
```

The static website and CloudFront `/pkg/` behaviors remain in `../av.www`.

## Checks

```sh
$ python3 -m unittest discover
$ cargo test --workspace
$ scripts/generate-pkg-sqlite.py --check
```

Raw Codex outputs remain under ignored `cache/enrichment/` paths for resumable
or manual controller runs. See `scripts/codex-enrichment-controller.md` when
using that flow instead of Atlas’s direct CLI backend.
