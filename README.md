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
  regenerates `cache/automic-vault/isotopes.json`. It also checks exported
  `cache/automic-vault/db.json` pulse metadata so empty New / Updated coverage
  is reported as automation health failure after the DB has been written. Use
  `scripts/hourly-maintenance.py --skip-isotope-builds` for a summary-only
  isotope refresh. Isotope checkouts default to sibling directories
  `../isotopes` and `../radioisotopes`.
- `refresh`: runs `scripts/build.py --refresh` and commits tracked
  `deterministic/` and `combined/` changes.
- `enrich-new`: prepares external-controller enrichment batches for newly
  observed projects, limited to 50 projects by default. A Codex-hosted
  controller then spawns sub-agents and runs the apply step with
  `--commit-after-batch`.
- `review-stale-updated`: prepares external-controller review batches for
  stale or upstream-updated projects, limited to 50 projects by default. A
  Codex-hosted controller then spawns sub-agents and runs the apply step with
  `--commit-after-batch`.

Each run writes a status file and appended log under
`cache/automation/nightly-maintenance/`. Use
`scripts/nightly-maintenance.py --list` to print the commands, or
`scripts/nightly-maintenance.py <task> --dry-run` to preview one task.
Concurrent `scripts/automation-runner.sh` invocations are lock-protected; if a
job is already running, a second invocation reports that condition and exits
cleanly instead of failing the caller.

AI enrichment automations must use the controller flow in
`scripts/codex-enrichment-controller.md`. The scheduled Python scripts only
prepare `codex-output.json` targets; they no longer shell out to nested
`codex exec` by default.

To inspect unresolved prepared controller runs, use:

```sh
python3 scripts/enrichment-controller.py pending --json
python3 scripts/enrichment-controller.py next-run --json
```

The helper prints the oldest unresolved run and the exact
`scripts/enrich-projects.py --phase apply ...` command needed to consume it.
