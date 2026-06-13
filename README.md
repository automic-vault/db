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

Precedence is deterministic < agents < human override. Raw Codex JSON is kept
under `cache/` for resumability/debugging; YAML in `agents/` is the committed
agent-curated layer. Confidence and provenance fields stay in the agent stages
and are not copied into `combined/`.

Agent YAML may also include a `geiger` block with the package risk color,
classifier confidence, category, reasons, and signals. This is agent-stage
context and is not copied into `combined/`.

## Codex Automations

Maintenance is designed to be run by Codex cron automations. The repository
script is intentionally one-shot: Codex owns the schedule, and each invocation
runs exactly one task.

```sh
scripts/automation-runner.sh db
scripts/nightly-maintenance.py refresh
scripts/nightly-maintenance.py enrich-new
scripts/nightly-maintenance.py review-stale-updated
```

Defaults:

- `db`: runs the hourly package database refresh through
  `scripts/hourly-maintenance.py`, including isotope fork scans. By default it
  builds and publishes only missing latest upstream isotope releases, then
  regenerates `cache/automic-vault/isotopes.json`. Use
  `scripts/hourly-maintenance.py --skip-isotope-builds` for a summary-only
  isotope refresh. Isotope checkouts default to sibling directories
  `../isotopes` and `../radioisotopes`.
- `refresh`: runs `scripts/build.py --refresh` and commits tracked
  `deterministic/` and `combined/` changes.
- `enrich-new`: runs Codex enrichment for newly observed projects, limited to
  50 projects by default, and commits after each applied batch.
- `review-stale-updated`: reviews stale or upstream-updated projects, limited to
  50 projects by default, and commits after each applied batch.

Each run writes a status file and appended log under
`cache/automation/nightly-maintenance/`. Use
`scripts/nightly-maintenance.py --list` to print the commands, or
`scripts/nightly-maintenance.py <task> --dry-run` to preview one task.
