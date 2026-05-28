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
- `agents/<formula>.yml`: Codex enrichment, including confidence/provenance
- `human-override/<formula>.yml`: hand-authored corrections
- `combined/<formula>.yml`: final public output

Precedence is deterministic < agents < human override. Agent confidence and
provenance fields stay in `agents/`; they are not copied into `combined/`.
