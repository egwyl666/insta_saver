"""Добавить или обновить аккаунт из cookies.txt (пока нет админки).

    python -m accounts.add cookies.txt              # label = имя файла
    python -m accounts.add cookies.txt --label acc_one
    python -m accounts.add --list

Если аккаунт с таким label уже есть — cookies заменяются, и аккаунт
возвращается в пул (так чинится выбывший по AUTH_EXPIRED).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from accounts import crypto  # noqa: E402
from db import storage  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cookies", nargs="?", type=Path, help="cookies.txt в формате Netscape")
    ap.add_argument("--label", help="имя аккаунта в пуле (по умолчанию — имя файла)")
    ap.add_argument("--platform", default="instagram", choices=("instagram", "twitter"))
    ap.add_argument("--list", action="store_true", help="показать аккаунты в пуле")
    args = ap.parse_args()

    storage.init_db()

    if args.list or not args.cookies:
        accounts = storage.list_accounts()
        if not accounts:
            print("пул пуст")
        for a in accounts:
            print(f"#{a['id']}  {a['label']:20} {a['platform']:10} {a['status']:9} {a['note'] or ''}")
        return

    if not args.cookies.is_file():
        raise SystemExit(f"файл не найден: {args.cookies}")

    label = args.label or args.cookies.stem
    try:
        rel = crypto.store(label, args.cookies.read_bytes())
    except crypto.CookiesError as exc:
        raise SystemExit(f"[!] {exc}")

    existing = next((a for a in storage.list_accounts() if a["label"] == label), None)
    if existing:
        storage.update_account_cookies(existing["id"], rel)
        print(f"[ok] cookies аккаунта «{label}» обновлены, он снова в пуле (#{existing['id']})")
    else:
        account_id = storage.add_account(label, rel, platform=args.platform)
        print(f"[ok] аккаунт «{label}» добавлен в пул (#{account_id})")

    print(f"[i] cookies зашифрованы. Исходный файл лучше удалить: rm {args.cookies}")


if __name__ == "__main__":
    main()
