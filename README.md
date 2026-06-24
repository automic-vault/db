# Comprehensive Database for All Open Source Packages with CLIs

We’re using agents to fill this out but humans are welcome to do fixes and
improve the scripts.

> [!IMPORTANT]
> CLIs only. No transitive or library-only deps go here.

## Data We Collect

Only rarely changing data, not eg. popularity, download counts or rankings.

## Build

Run the deterministic pipeline:

```sh
scripts/build.py
```

Use `scripts/build.py --refresh` for daily source refreshes. The pipeline writes
remote and intermediate data to `cache/`, then publishes committed YAML stages:

- `deterministic/<formula>.yml`: source-backed generator output
- `agents/<formula>.yml`: schema-validated Codex enrichment, including confidence/provenance
- `human-override/<formula>.yml`: hand-authored corrections
- `combined/<formula>.yml`: final public output

It also derives Homebrew executable indexes from this repository’s own published
YAML:

- `cache/brew/executables.json`: `formula -> [executables]`
- `cache/brew/executable-entries.json`: `executable -> formula`
- `cache/brew/cask-entries.json`: supported binary casks and `cask:<token>` executable entries
- `cache/automic-vault/db.json`: Automic Vault-compatible Homebrew authority DB

The pipeline also builds `cache/cratesio/index.json` from the crates.io daily
database dump. That index is used for Cargo/crates.io package pages only; Cargo
crates are not exported into the Automic Vault authority DB.

Precedence is deterministic < agents < human override. Raw Codex JSON is kept
under `cache/` for resumability/debugging; YAML in `agents/` is the committed
agent-curated layer. Confidence and provenance fields stay in the agent stages
and are not copied into `combined/`.

Agent YAML may also include a `geiger` block with the package risk color,
classifier confidence, category, reasons, and signals. This is agent-stage
context and is not copied into `combined/`.

## Codex Automations

Maintenance is designed to be run by Codex cron automations. Codex owns the
schedule; repo scripts are one-shot entrypoints and each invocation runs exactly
one task.

```sh
scripts/automation-runner.sh db
scripts/automation-runner.sh npm-full-scan
```

Runner jobs:

- `db`: runs the hourly package database refresh through
  `scripts/hourly-maintenance.py`. This refreshes source data, prepares hourly
  missing-curated-field enrichment if no older prepared run is waiting, builds
  isotope summaries/releases, exports and health-checks
  `cache/automic-vault/db.json`, publishes the public DB, rebuilds package-page
  derived data, and commits tracked stable outputs as `hourly: refresh package
  database`.
- `npm-full-scan`: runs `scripts/build-db.py --refresh --npm-full-scan` followed
  by `scripts/build-combined-json.py`.

Each runner job writes `cache/automation/<job>.status.json` and appends
`cache/automation/<job>.log`. Use `scripts/codex-automation-status.sh` to inspect
both jobs, recent logs, active maintenance processes, and public DB freshness.
Runner invocations are lock-protected; if a job is already running, another
invocation reports that condition and exits cleanly.

AI enrichment automations must use the controller flow in
`scripts/codex-enrichment-controller.md`. Scheduled Python scripts only prepare
`codex-output.json` targets; they do not shell out to nested `codex exec` by
default. Apply completed controller runs with the command emitted by:

```sh
python3 scripts/enrichment-controller.py next-run --json
```

Manual nightly helpers still exist for one-off Codex cron tasks:

```sh
scripts/nightly-maintenance.py --list
scripts/nightly-maintenance.py <task> --dry-run
```
