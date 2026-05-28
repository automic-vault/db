#!/usr/bin/env python3
from __future__ import annotations

import json

from lib.common import ensure_root
from lib.render import publish_stages, validate_stage


def main() -> int:
    ensure_root()
    failures = validate_stage()
    if failures:
        raise SystemExit("\n".join(failures[:20]))
    published = publish_stages()
    print(json.dumps({"ok": True, "published": published}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
