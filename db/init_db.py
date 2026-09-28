"""Разовая инициализация.

    python -m db.init_db            # создать/обновить схему
    python -m db.init_db --key      # сгенерить COOKIES_KEY для .env
    python -m db.init_db --admin 123456789 --username simon
    python -m db.init_db --setup    # всё сразу: .env, ключ, токен, схема, админ
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from accounts.crypto import generate_key  # noqa: E402
from common.config import ADMIN_TG_ID, BASE_DIR, DB_PATH  # noqa: E402
from db import storage  # noqa: E402

ENV_PATH = BASE_DIR / ".env"
PLACEHOLDERS = {"", "0", "123456:AA..."}


def _env_get(text: str, key: str) -> str:
    m = re.search(rf"^{key}=(.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _env_set(text: str, key: str, value: str) -> str:
    line = f"{key}={value}"
    if re.search(rf"^{key}=", text, re.MULTILINE):
        return re.sub(rf"^{key}=.*$", lambda _: line, text, flags=re.MULTILINE)
    return text.rstrip("\n") + f"\n{line}\n"


def _ask(prompt: str) -> str:
    if not sys.stdin.isatty():
        return ""
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def setup() -> None:
    """Первичная настройка одной командой. Повторный запуск ничего не ломает:
    заполненные значения не трогает, спрашивает только недостающие."""
    if not ENV_PATH.exists():
        shutil.copy(BASE_DIR / ".env.example", ENV_PATH)
        print(f"[ok] создан {ENV_PATH}")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass

    env = ENV_PATH.read_text(encoding="utf-8")
    if not _env_get(env, "COOKIES_KEY"):
        env = _env_set(env, "COOKIES_KEY", generate_key())
        print("[ok] COOKIES_KEY сгенерирован")

    if _env_get(env, "BOT_TOKEN") in PLACEHOLDERS:
        token = _ask("BOT_TOKEN (от @BotFather, Enter — пропустить): ")
        if token:
            env = _env_set(env, "BOT_TOKEN", token)

    admin = _env_get(env, "ADMIN_TG_ID")
    if admin in PLACEHOLDERS:
        admin = _ask("Твой Telegram ID (узнать у @userinfobot, Enter — пропустить): ")
        if admin.isdigit():
            env = _env_set(env, "ADMIN_TG_ID", admin)

    ENV_PATH.write_text(env, encoding="utf-8")

    storage.init_db()
    print(f"[ok] схема применена: {DB_PATH}")
    if admin.isdigit() and admin != "0":
        storage.add_user(int(admin), role="admin")
        print(f"[ok] админ добавлен: {admin}")

    missing = [k for k in ("BOT_TOKEN", "ADMIN_TG_ID") if _env_get(env, k) in PLACEHOLDERS]
    if missing:
        print(f"[!] заполни в .env: {', '.join(missing)} и запусти --setup ещё раз")
    else:
        # Полный путь к интерпретатору: голый "python" — системный, без зависимостей из .venv.
        print(f"[ok] готово. Запуск: {sys.executable} -m bot.main")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", action="store_true", help="сгенерить Fernet-ключ и выйти")
    ap.add_argument("--admin", type=int, help="tg_id админа")
    ap.add_argument("--username", type=str, help="ник админа для красоты")
    ap.add_argument("--setup", action="store_true", help="первичная настройка одной командой")
    args = ap.parse_args()

    if args.key:
        print(generate_key())
        return

    if args.setup:
        setup()
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
