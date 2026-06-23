# Codex Enrichment Controller

Use this workflow from a Codex-hosted automation when curated enrichment needs AI research. Do not call `codex exec` from inside the maintenance scripts for this path.

To inspect the next unresolved prepared run, use:

```sh
python3 scripts/enrichment-controller.py next-run --json
```

It emits the oldest unresolved run plus the exact `apply_command` for that run.

1. Prepare the run.

For nightly newly observed projects:

```sh
python3 scripts/enrich-projects.py --mode new --limit 50 --batch-size 5 --backend external --phase prepare --run-id "$(date -u +%Y%m%dT%H%M%SZ)"
```

For nightly stale or upstream-updated projects:

```sh
python3 scripts/enrich-projects.py --mode review-stale-updated --limit 50 --batch-size 5 --backend external --phase prepare --run-id "$(date -u +%Y%m%dT%H%M%SZ)"
```

For hourly missing curated fields:

```sh
python3 scripts/enrich-projects.py --mode new --include-missing-curated-fields --limit 10 --batch-size 3 --backend external --phase prepare --run-id "$(date -u +%Y%m%dT%H%M%SZ)"
```

2. Read `cache/enrichment/runs/<run-id>/controller-manifest.json`.

3. For each batch with `"status": "pending"`, spawn one sub-agent. Give it only:

- `prompt_path`
- `input_path`
- `output_schema_path`
- `codex_output_path`

The sub-agent must read the prompt and input, research official sources only, and write JSON matching `output_schema_path` to `codex_output_path`. It must not edit repo files.

4. Apply completed outputs.

You can use the helper's emitted `apply_command`, or run the equivalent command manually.

Use the same mode, limits, and batch size as the prepare command. For nightly newly observed projects:

```sh
python3 scripts/enrich-projects.py --mode new --limit 50 --batch-size 5 --backend external --phase apply --run-id "<run-id>" --commit-after-batch
```

For stale review, use `--mode review-stale-updated`. For hourly missing curated fields, include `--include-missing-curated-fields --limit 10 --batch-size 3`; omit `--commit-after-batch` when the surrounding hourly refresh will commit all stable source changes at the end.

5. If apply reports missing outputs or validation failures, leave completed batch commits in place and rerun only the failed batches with the same `--run-id` after their `codex-output.json` files are fixed.

`scripts/build-isotopes.sh` still has a separate conflict-repair `codex exec` path. That path is out of scope for this controller unless isotope conflict repair starts timing out too.
