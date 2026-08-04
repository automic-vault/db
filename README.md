# Package Metadata Database

The metadata and live SQLite origin behind the pkg.so package catalog.

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
search documents, hubs, and generation metadata are compiled into
`cache/pkg.sqlite`. The aggregate export is generated at
`cache/automic-vault/db.json` and served publicly at `https://pkg.so/db.json`.
HTML, CSS, JavaScript, and sitemaps are served by the Rust origin.

## Run the pkg.so origin

```sh
$ AV_WEB_DB_PATH=cache/pkg.sqlite cargo run --release -p av-web
av-web listening on 127.0.0.1:3004
```

`AV_WEB_DB_PATH` selects the SQLite file. The production service defaults to
`/var/lib/automic-vault-web/pkg.sqlite`; origin-header settings remain in
`/etc/automic-vault-web.env` on Atlas.

## Atlas

Deploy code and systemd units directly from the Atlas checkout. The script
builds the current working tree; it does not SSH, fetch, or require a commit:

```sh
$ cd /apps/pkgdb
$ scripts/deploy-atlas.sh
```

Set `PKGDB_REBUILD_SQLITE=true` when renderer, stylesheet, crawler, or source
inputs changed. The deploy generates and validates a new artifact on Atlas and
coordinates its atomic swap with the matching origin binary. The flag form is
preferred; the environment variable remains supported for compatibility:

```sh
$ scripts/deploy-atlas.sh --rebuild-sqlite
```

`pkgdb-maintenance.timer` refreshes metadata nightly, runs bounded Codex
enrichment, generates and validates `pkg.sqlite.next`, generates `db.json` as
the final daily job step, then atomically replaces both live files. `av-web`
opens the artifacts per request, so successful swaps need no restart. Failed
builds leave the previous files serving.

Inspect it with:

```sh
$ systemctl status pkgdb-maintenance.timer automic-vault-web.service
$ journalctl -u pkgdb-maintenance.service -n 100
```

Atlas has no GitHub credentials. From Pangolin, retrieve Atlas metadata commits
and push them with the external synchronization script:

```sh
$ scripts/sync-atlas.sh
```

`pkg.so` uses a dedicated CloudFront distribution in front of the same Atlas
origin. Browser and edge responses are cached for five minutes, then revalidated
with `ETag` or `Last-Modified` so unchanged content does not need to be
retransmitted. CloudFront credentials stay off Atlas; create or update the
distribution from Pangolin with:

```sh
$ AV_WEB_ORIGIN_SECRET=... scripts/deploy-pkg-cloudfront.sh --prepare-only
$ AV_WEB_ORIGIN_SECRET=... scripts/deploy-pkg-cloudfront.sh
```

The deploy requests a DNS-validated ACM certificate in `us-east-1` when one is
not already present. It deploys on the generated `cloudfront.net` hostname
until that certificate is issued, then attaches the `pkg.so` alias on the next
run. The script reports the required ACM CNAME but does not change DNS.

The existing `atomicvault.com/pkg/` CloudFront behaviors stay live during the
migration. Their redirects into the flattened `https://pkg.so/...` catalog are
managed separately in `../av.www`.

## Checks

```sh
$ python3 -m unittest discover -s tests
$ cargo test --workspace
$ scripts/generate-pkg-sqlite.py --check
```

Raw Codex outputs remain under ignored `cache/enrichment/` paths for resumable
or manual controller runs. See `scripts/codex-enrichment-controller.md` when
using that flow instead of Atlas’s direct CLI backend.
