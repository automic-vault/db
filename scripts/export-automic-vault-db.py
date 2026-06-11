#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    py = sys.executable
    run([py, "scripts/bootstrap/05-export-automic-vault-db.py"])
    run([py, "scripts/build-combined-json.py"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
