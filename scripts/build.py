#!/usr/bin/env -S uv run --python 3.10
from __future__ import annotations

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    bootstrap = Path(__file__).resolve().parent / "bootstrap"
    sys.path.insert(0, str(bootstrap))
    runpy.run_path(str(bootstrap / "build.py"), run_name="__main__")
