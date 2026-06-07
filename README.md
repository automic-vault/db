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

## Nightly Maintenance

Run the long-lived keeper when you want the refresh and enrichment cadence to
look after itself:

```sh
scripts/nightly-maintenance.py
```

Defaults:

- daily source refresh at 02:15 local time
- weekly new-project enrichment on Sunday at 03:15, limited to 50 projects
- weekly stale/updated review on Sunday at 04:15, limited to 50 projects

Use `scripts/nightly-maintenance.py --help` for schedule knobs and
`scripts/nightly-maintenance.py --once --dry-run` to preview the next action.
