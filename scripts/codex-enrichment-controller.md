# Codex Enrichment Controller

Use this workflow from a Codex-hosted automation when curated enrichment needs AI research. Do not call `codex exec` from inside the maintenance scripts for this path.

1. Prepare the run:

```sh
python3 scripts/enrich-projects.py --mode new --limit 50 --batch-size 5 --backend external --phase prepare --run-id "$(date -u +%Y%m%dT%H%M%SZ)"
```

Use `--mode review-stale-updated` for the stale review job. Add `--include-missing-curated-fields` for hourly missing-field refreshes.

2. Read `cache/enrichment/runs/<run-id>/controller-manifest.json`.

3. For each batch with `"status": "pending"`, spawn one sub-agent. Give it only:

- `prompt_path`
- `input_path`
- `output_schema_path`
- `codex_output_path`

The sub-agent must read the prompt and input, research official sources only, and write JSON matching `output_schema_path` to `codex_output_path`. It must not edit repo files.

4. Apply completed outputs:

```sh
python3 scripts/enrich-projects.py --mode new --limit 50 --batch-size 5 --backend external --phase apply --run-id "<run-id>" --commit-after-batch
```

5. If apply reports missing outputs or validation failures, leave completed batch commits in place and rerun only the failed batches with the same `--run-id` after their `codex-output.json` files are fixed.

`scripts/build-isotopes.sh` still has a separate conflict-repair `codex exec` path. That path is out of scope for this controller unless isotope conflict repair starts timing out too.
