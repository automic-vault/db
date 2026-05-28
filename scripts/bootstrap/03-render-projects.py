#!/usr/bin/env python3
from __future__ import annotations

import json

from lib.common import ensure_root
from lib.render import read_formula_cache, read_manager_cache, render_stage, validate_stage


def main() -> int:
    ensure_root()
    count = render_stage(read_formula_cache(), read_manager_cache())
    failures = validate_stage()
    if failures:
        raise SystemExit("\n".join(failures[:20]))
    print(json.dumps({"ok": True, "staged_projects": count}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
