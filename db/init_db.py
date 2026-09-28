"""Разовая инициализация.

    python -m db.init_db            # создать/обновить схему
    python -m db.init_db --key      # сгенерить COOKIES_KEY для .env
    python -m db.init_db --admin 123456789 --username simon
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from accounts.crypto import generate_key  # noqa: E402
from common.config import ADMIN_TG_ID, DB_PATH  # noqa: E402
from db import storage  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", action="store_true", help="сгенерить Fernet-ключ и выйти")
    ap.add_argument("--admin", type=int, help="tg_id админа")
    ap.add_argument("--username", type=str, help="ник админа для красоты")
    args = ap.parse_args()

    if args.key:
        print(generate_key())
        return

    storage.init_db()
    print(f"[ok] схема применена: {DB_PATH}")

    admin_id = args.admin or ADMIN_TG_ID
    if admin_id:
        storage.add_user(admin_id, args.username, role="admin")
        print(f"[ok] админ добавлен: {admin_id}")
    else:
        print("[!] админ не задан — укажи --admin <tg_id> или ADMIN_TG_ID в .env")

    print("[i] настройки по умолчанию:")
    for k, v in sorted(storage.all_settings().items()):
        print(f"    {k} = {v}")


if __name__ == "__main__":
    main()
