#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "cache" / "nightly-maintenance"
STATE_PATH = CACHE_DIR / "state.json"
LOG_DIR = CACHE_DIR / "logs"
WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


@dataclass(frozen=True)
class Job:
    key: str
    title: str
    cadence: str
    at: clock_time
    command: list[str]
    weekday: int | None = None


class Palette:
    def __init__(self, enabled: bool, ascii_only: bool) -> None:
        self.enabled = enabled
        self.ascii_only = ascii_only

    def color(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    @property
    def accent(self) -> str:
        return "36"

    @property
    def dim(self) -> str:
        return "2"

    @property
    def good(self) -> str:
        return "32"

    @property
    def bad(self) -> str:
        return "31"

    @property
    def warn(self) -> str:
        return "33"

    @property
    def glyphs(self) -> dict[str, str]:
        if self.ascii_only:
            return {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|", "ok": "OK", "fail": "!!", "wait": "..", "run": ">>"}
        return {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│", "ok": "✓", "fail": "✗", "wait": "◇", "run": "◆"}


def parse_time(value: str) -> clock_time:
    try:
        hour, minute = value.split(":", 1)
        return clock_time(hour=int(hour), minute=int(minute))
    except ValueError as err:
        raise argparse.ArgumentTypeError("expected HH:MM") from err


def parse_weekday(value: str) -> int:
    key = value.strip().lower()
    if key not in WEEKDAYS:
        raise argparse.ArgumentTypeError("expected weekday name, e.g. sunday")
    return WEEKDAYS[key]


def supports_color(choice: str) -> bool:
    if choice == "always":
        return True
    if choice == "never":
        return False
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def occurrence_key(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M")


def datetime_for(day: date, at: clock_time) -> datetime:
    return datetime.combine(day, at)


def previous_occurrence(job: Job, now: datetime) -> datetime:
    today = now.date()
    if job.cadence == "daily":
        candidate = datetime_for(today, job.at)
        return candidate if candidate <= now else candidate - timedelta(days=1)

    days_since = (today.weekday() - int(job.weekday)) % 7
    candidate_day = today - timedelta(days=days_since)
    candidate = datetime_for(candidate_day, job.at)
    if candidate > now:
        candidate -= timedelta(days=7)
    return candidate


def next_occurrence(job: Job, now: datetime) -> datetime:
    today = now.date()
    if job.cadence == "daily":
        candidate = datetime_for(today, job.at)
        return candidate if candidate > now else candidate + timedelta(days=1)

    days_until = (int(job.weekday) - today.weekday()) % 7
    candidate_day = today + timedelta(days=days_until)
    candidate = datetime_for(candidate_day, job.at)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def is_due(job: Job, state: dict[str, Any], now: datetime, catch_up: timedelta) -> tuple[bool, datetime]:
    scheduled = previous_occurrence(job, now)
    job_state = state.get(job.key, {})
    attempted = job_state.get("last_attempted_occurrence") if isinstance(job_state, dict) else None
    due = scheduled <= now <= scheduled + catch_up and attempted != occurrence_key(scheduled)
    return due, scheduled


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 24 * 60 * 60)
    hours, rem = divmod(rem, 60 * 60)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{secs}s")
    return " ".join(parts[:2])


def line(palette: Palette, text: str = "") -> None:
    print(text, flush=True)


def banner(palette: Palette, title: str, subtitle: str) -> None:
    g = palette.glyphs
    width = max(58, min(96, os.get_terminal_size().columns if sys.stdout.isatty() else 72))
    top = f"{g['tl']}{g['h']} {title} " + g["h"] * max(0, width - len(title) - 4) + g["tr"]
    bottom = g["bl"] + g["h"] * (width - 2) + g["br"]
    line(palette, palette.color(palette.accent, top))
    line(palette, f"{palette.color(palette.accent, g['v'])} {subtitle.ljust(width - 4)} {palette.color(palette.accent, g['v'])}")
    line(palette, palette.color(palette.accent, bottom))


def status(palette: Palette, symbol: str, color: str, title: str, detail: str = "") -> None:
    prefix = palette.color(color, palette.glyphs[symbol])
    if detail:
        line(palette, f"{prefix} {title} {palette.color(palette.dim, detail)}")
    else:
        line(palette, f"{prefix} {title}")


def log_path(job: Job, started: datetime) -> Path:
    return LOG_DIR / f"{started.strftime('%Y-%m-%d')}-{job.key}.log"


def run_job(job: Job, state: dict[str, Any], scheduled: datetime, palette: Palette, env: dict[str, str]) -> int:
    started = datetime.now()
    path = log_path(job, started)
    path.parent.mkdir(parents=True, exist_ok=True)
    command_text = " ".join(shlex.quote(part) for part in job.command)
    status(palette, "run", palette.accent, job.title, command_text)

    job_state = dict(state.get(job.key, {}))
    job_state["last_attempted_occurrence"] = occurrence_key(scheduled)
    job_state["last_attempted_at"] = started.isoformat(timespec="seconds")
    state[job.key] = job_state
    write_state(state)

    with path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{started.isoformat(timespec='seconds')}] {command_text}\n")
        process = subprocess.Popen(
            job.command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for raw in process.stdout:
            text = raw.rstrip("\n")
            log.write(raw)
            line(palette, f"  {palette.color(palette.dim, job.key):18} {text}")
        code = process.wait()

    elapsed = human_duration((datetime.now() - started).total_seconds())
    job_state["last_exit_code"] = code
    job_state["last_finished_at"] = datetime.now().isoformat(timespec="seconds")
    if code == 0:
        job_state["last_successful_occurrence"] = occurrence_key(scheduled)
        job_state["last_success_at"] = datetime.now().isoformat(timespec="seconds")
        status(palette, "ok", palette.good, f"{job.title} finished", f"{elapsed}; log {path.relative_to(ROOT)}")
    else:
        status(palette, "fail", palette.bad, f"{job.title} failed", f"exit {code}; log {path.relative_to(ROOT)}")
    state[job.key] = job_state
    write_state(state)
    return code


def build_jobs(args: argparse.Namespace) -> list[Job]:
    py = sys.executable
    return [
        Job(
            key="build-refresh",
            title="Daily source refresh",
            cadence="daily",
            at=args.build_time,
            command=[py, "scripts/build.py", "--refresh"],
        ),
        Job(
            key="enrich-new",
            title="Weekly new-project enrichment",
            cadence="weekly",
            weekday=args.weekly_day,
            at=args.enrich_new_time,
            command=[
                py,
                "scripts/enrich-projects.py",
                "--mode",
                "new",
                "--limit",
                str(args.enrich_limit),
                "--batch-size",
                str(args.batch_size),
            ],
        ),
        Job(
            key="enrich-stale-updated",
            title="Weekly stale/updated review",
            cadence="weekly",
            weekday=args.weekly_day,
            at=args.enrich_stale_time,
            command=[
                py,
                "scripts/enrich-projects.py",
                "--mode",
                "review-stale-updated",
                "--limit",
                str(args.enrich_limit),
                "--batch-size",
                str(args.batch_size),
            ],
        ),
    ]


def print_schedule(jobs: list[Job], now: datetime, palette: Palette) -> None:
    status(palette, "wait", palette.accent, "Schedule")
    for job in jobs:
        upcoming = next_occurrence(job, now)
        line(palette, f"  {job.key:22} {upcoming.strftime('%a %Y-%m-%d %H:%M')}")


def due_jobs(jobs: list[Job], state: dict[str, Any], now: datetime, catch_up: timedelta) -> list[tuple[Job, datetime]]:
    result = []
    for job in jobs:
        due, scheduled = is_due(job, state, now, catch_up)
        if due:
            result.append((job, scheduled))
    return result


def wait_or_stop(stop_requested: threading.Event, seconds: float) -> bool:
    return stop_requested.wait(max(0.0, seconds))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keep av.db fresh with nightly source refreshes and weekly Codex enrichment.")
    parser.add_argument("--build-time", type=parse_time, default=parse_time("02:15"), help="Daily build refresh time, local HH:MM.")
    parser.add_argument("--weekly-day", type=parse_weekday, default=parse_weekday("sunday"), help="Weekday for enrichment jobs.")
    parser.add_argument("--enrich-new-time", type=parse_time, default=parse_time("03:15"), help="Weekly new-project enrichment time, local HH:MM.")
    parser.add_argument("--enrich-stale-time", type=parse_time, default=parse_time("04:15"), help="Weekly stale/updated enrichment time, local HH:MM.")
    parser.add_argument("--enrich-limit", type=int, default=50, help="Maximum projects to send to Codex per enrichment mode.")
    parser.add_argument("--batch-size", type=int, default=10, help="Projects per Codex batch.")
    parser.add_argument("--catch-up-hours", type=float, default=6.0, help="Run missed jobs only within this many hours of their scheduled time.")
    parser.add_argument("--between-jobs-minutes", type=float, default=5.0, help="Pause between immediately due jobs.")
    parser.add_argument("--poll-minutes", type=float, default=15.0, help="Maximum sleep before rechecking the schedule.")
    parser.add_argument("--once", action="store_true", help="Run currently due jobs, print the next schedule, then exit.")
    parser.add_argument("--run-now", action="store_true", help="Run all jobs immediately, then continue unless --once is also set.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without executing commands or writing state.")
    parser.add_argument("--color", choices=["auto", "always", "never"], default="auto", help="Color output policy.")
    parser.add_argument("--ascii", action="store_true", help="Use ASCII borders and status markers.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.enrich_limit < 1:
        raise SystemExit("--enrich-limit must be at least 1")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")

    palette = Palette(supports_color(args.color), args.ascii)
    jobs = build_jobs(args)
    state = load_state()
    env = os.environ.copy()
    stop_requested = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        if not stop_requested.is_set():
            status(palette, "wait", palette.warn, f"Received signal {signum}; stopping after current work")
        stop_requested.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    banner(palette, "av.db nightly keeper", f"cwd {ROOT}")
    print_schedule(jobs, datetime.now(), palette)

    while not stop_requested.is_set():
        now = datetime.now()
        catch_up = timedelta(hours=args.catch_up_hours)
        if args.run_now:
            ready = [(job, now) for job in jobs]
            args.run_now = False
        else:
            ready = due_jobs(jobs, state, now, catch_up)

        if ready:
            for index, (job, scheduled) in enumerate(ready):
                if stop_requested.is_set():
                    break
                if args.dry_run:
                    command_text = " ".join(shlex.quote(part) for part in job.command)
                    status(palette, "run", palette.accent, f"Would run {job.title}", command_text)
                else:
                    run_job(job, state, scheduled, palette, env)
                if index != len(ready) - 1 and not stop_requested.is_set():
                    pause = max(0.0, args.between_jobs_minutes * 60)
                    if args.dry_run:
                        status(palette, "wait", palette.accent, "Would cool down before next job", human_duration(pause))
                    else:
                        status(palette, "wait", palette.accent, "Cooling down before next job", human_duration(pause))
                        wait_or_stop(stop_requested, pause)
            if args.once or args.dry_run:
                break
            continue

        next_run = min(next_occurrence(job, now) for job in jobs)
        sleep_seconds = min(max(1.0, args.poll_minutes * 60), max(1.0, (next_run - now).total_seconds()))
        status(palette, "wait", palette.accent, "Sleeping", f"next check in {human_duration(sleep_seconds)}; next job {next_run.strftime('%a %H:%M')}")
        if args.once:
            break
        wait_or_stop(stop_requested, sleep_seconds)

    status(palette, "ok", palette.good, "Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
