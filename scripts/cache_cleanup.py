from __future__ import annotations

import shutil
import time
from pathlib import Path


DEFAULT_COMPLETED_RUNS_TO_KEEP = 3
DEFAULT_STALE_TEMP_AGE_SECONDS = 24 * 60 * 60


def prune_completed_enrichment_runs(runs_dir: Path, *, keep: int = DEFAULT_COMPLETED_RUNS_TO_KEEP) -> list[Path]:
    """Remove old applied runs while preserving resumable and recent runs."""
    if keep < 0:
        raise ValueError("keep must be non-negative")
    if not runs_dir.is_dir():
        return []

    completed = sorted(
        (
            run_dir
            for run_dir in runs_dir.iterdir()
            if run_dir.is_dir()
            and (run_dir / "controller-manifest.json").is_file()
            and (run_dir / "apply-summary.json").is_file()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    removed = completed[keep:]
    for run_dir in removed:
        shutil.rmtree(run_dir)
    return removed


def remove_stale_atomic_temp_files(
    cache_dir: Path,
    *,
    min_age_seconds: int = DEFAULT_STALE_TEMP_AGE_SECONDS,
    now: float | None = None,
) -> list[Path]:
    """Remove abandoned atomic-write files without touching reusable cache data."""
    if min_age_seconds < 0:
        raise ValueError("min_age_seconds must be non-negative")
    if not cache_dir.is_dir():
        return []

    cutoff = (time.time() if now is None else now) - min_age_seconds
    removed: list[Path] = []
    for path in cache_dir.rglob(".*.tmp"):
        if path.is_file() and path.stat().st_mtime <= cutoff:
            path.unlink()
            removed.append(path)
    return removed


def cleanup_cache(cache_dir: Path, *, completed_runs_to_keep: int = DEFAULT_COMPLETED_RUNS_TO_KEEP) -> list[Path]:
    removed = remove_stale_atomic_temp_files(cache_dir)
    removed.extend(
        prune_completed_enrichment_runs(
            cache_dir / "enrichment" / "runs",
            keep=completed_runs_to_keep,
        )
    )
    return removed
