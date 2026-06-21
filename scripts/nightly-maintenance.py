#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bootstrap.lib.common import git_commit_if_changed


STATUS_DIR = ROOT / "cache" / "automation" / "nightly-maintenance"
DEFAULT_ENRICH_LIMIT = int(os.environ.get("AVDB_ENRICH_LIMIT", "50"))
DEFAULT_BATCH_SIZE = int(os.environ.get("AVDB_ENRICH_BATCH_SIZE", "5"))


@dataclass(frozen=True)
class Task:
    name: str
    title: str
    command: list[str]
    commit_paths: list[str] | None = None
    commit_message: str | None = None


def quote_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def status_path(task_name: str) -> Path:
    return STATUS_DIR / f"{task_name}.status.json"


def log_path(task_name: str) -> Path:
    return STATUS_DIR / f"{task_name}.log"


def write_status(task_name: str, status: dict[str, Any]) -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    status_path(task_name).write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_tasks(args: argparse.Namespace) -> dict[str, Task]:
    py = sys.executable
    return {
        "refresh": Task(
            name="refresh",
            title="Refresh deterministic package data",
            command=[py, "scripts/build.py", "--refresh"],
            commit_paths=[] if args.no_commit else ["deterministic", "combined"],
            commit_message="nightly: refresh package data",
        ),
        "enrich-new": Task(
            name="enrich-new",
            title="Prepare newly observed project enrichment batches",
            command=[
                py,
                "scripts/enrich-projects.py",
                "--mode",
                "new",
                "--limit",
                str(args.enrich_limit),
                "--batch-size",
                str(args.batch_size),
                "--backend",
                "external",
                "--phase",
                "prepare",
            ],
        ),
        "review-stale-updated": Task(
            name="review-stale-updated",
            title="Prepare stale or upstream-updated project review batches",
            command=[
                py,
                "scripts/enrich-projects.py",
                "--mode",
                "review-stale-updated",
                "--limit",
                str(args.enrich_limit),
                "--batch-size",
                str(args.batch_size),
                "--backend",
                "external",
                "--phase",
                "prepare",
            ],
        ),
    }


def run_command(task: Task, env: dict[str, str]) -> int:
    path = log_path(task.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    command_text = quote_command(task.command)
    started = now_iso()

    print(f"RUN {task.name}: {command_text}", flush=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{started}] {command_text}\n")
        process = subprocess.Popen(
            task.command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for raw in process.stdout:
            log.write(raw)
            print(raw, end="", flush=True)
        return process.wait()


def run_task(task: Task, *, dry_run: bool = False) -> int:
    started_at = now_iso()
    started_monotonic = datetime.now()
    command_text = quote_command(task.command)
    status: dict[str, Any] = {
        "task": task.name,
        "title": task.title,
        "command": command_text,
        "started_at": started_at,
        "log": str(log_path(task.name).relative_to(ROOT)),
    }
    write_status(task.name, status)

    if dry_run:
        print(f"DRY RUN {task.name}: {command_text}")
        status.update({"finished_at": now_iso(), "exit_code": 0, "dry_run": True})
        write_status(task.name, status)
        return 0

    code = run_command(task, os.environ.copy())
    status["exit_code"] = code

    if code == 0 and task.commit_paths:
        try:
            commit = git_commit_if_changed(task.commit_message or f"nightly: {task.name}", task.commit_paths)
        except subprocess.CalledProcessError as err:
            code = err.returncode or 1
            status["exit_code"] = code
            status["commit_error_at"] = now_iso()
            print(f"COMMIT FAILED {task.name}: exit {code}", file=sys.stderr)
        else:
            status["commit"] = commit
            print(f"COMMIT {task.name} {commit}" if commit else f"COMMIT {task.name} no tracked changes")

    elapsed = (datetime.now() - started_monotonic).total_seconds()
    status["finished_at"] = now_iso()
    status["elapsed_seconds"] = round(elapsed, 3)
    write_status(task.name, status)
    return code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one av.db maintenance task for a Codex cron automation."
    )
    parser.add_argument("task", nargs="?", choices=["refresh", "enrich-new", "review-stale-updated"], help="Task to run.")
    parser.add_argument("--list", action="store_true", help="List available automation tasks.")
    parser.add_argument("--enrich-limit", type=int, default=DEFAULT_ENRICH_LIMIT, help="Maximum projects to send to Codex.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Projects per Codex batch.")
    parser.add_argument("--no-commit", action="store_true", help="Do not commit refresh changes.")
    parser.add_argument("--dry-run", action="store_true", help="Print the command and write status without running it.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.enrich_limit < 1:
        raise SystemExit("--enrich-limit must be at least 1")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")

    tasks = build_tasks(args)
    if args.list:
        for task in tasks.values():
            print(f"{task.name}\t{task.title}\t{quote_command(task.command)}")
        return 0
    if not args.task:
        raise SystemExit("task is required unless --list is used")
    return run_task(tasks[args.task], dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
