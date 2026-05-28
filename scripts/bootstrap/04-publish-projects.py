#!/usr/bin/env python3
from __future__ import annotations

import json

from lib.common import PROJECTS_DIR, STAGE_DIR, ensure_root, sync_tree
from lib.render import validate_stage


def main() -> int:
    ensure_root()
    failures = validate_stage()
    if failures:
        raise SystemExit("\n".join(failures[:20]))
    sync_tree(STAGE_DIR / "projects", PROJECTS_DIR)
    print(json.dumps({"ok": True, "published": str(PROJECTS_DIR)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
