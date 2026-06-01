#!/usr/bin/env python3
from __future__ import annotations

import json

from lib.authority import AUTOMIC_VAULT_DB_PATH, write_automic_vault_db
from lib.common import ensure_root


def main() -> int:
    ensure_root()
    db = write_automic_vault_db()
    print(json.dumps({
        "ok": True,
        "path": str(AUTOMIC_VAULT_DB_PATH),
        "entries": len(db["entries"]),
        "formulas": len(db["formulas"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
