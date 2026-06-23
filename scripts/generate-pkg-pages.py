#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


AV_DB_ROOT = Path(__file__).resolve().parents[1]
AV_WWW_ROOT = Path(os.environ.get("AV_WWW_ROOT", AV_DB_ROOT.parent / "av.www")).resolve()
MOVED_SCRIPT = AV_WWW_ROOT / "scripts" / "generate-pkg-pages.py"


def _load_moved_module():
    if not MOVED_SCRIPT.is_file():
        raise RuntimeError(f"missing moved package renderer: {MOVED_SCRIPT}")
    spec = importlib.util.spec_from_file_location("av_www_generate_pkg_pages", MOVED_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load moved package renderer: {MOVED_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_moved_module = _load_moved_module()

for _name, _value in vars(_moved_module).items():
    if _name.startswith("__") and _name not in {"__doc__", "__all__"}:
        continue
    globals()[_name] = _value


if __name__ == "__main__":
    raise SystemExit(_moved_module.main())
