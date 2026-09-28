"""Разовая инициализация.

    python -m db.init_db                  # создать/обновить схему
    python -m db.init_db --key            # сгенерить COOKIES_KEY для .env
    python -m db.init_db --admin 123456789 --username simon
    python -m db.init_db --setup          # всё сразу: .env, ключи, токен, схема, админ, веб
    python -m db.init_db --add-owner 987654     # ещё один владелец (админ, получает алерты)
    python -m db.init_db --remove-owner 123456  # понизить до обычного юзера
    python -m db.init_db --web-password   # задать/сменить пароль веб-админки
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash  # noqa: E402

from accounts.crypto import generate_key  # noqa: E402
from common import envfile  # noqa: E402
from common.config import ADMIN_TG_ID, BASE_DIR, DB_PATH  # noqa: E402
from db import storage  # noqa: E402

ENV_PATH = envfile.ENV_PATH
PLACEHOLDERS = {"", "0", "123456:AA..."}


def _ask(prompt: str, secret: bool = False) -> str:
    if not sys.stdin.isatty():
        return ""
    try:
        return (getpass.getpass(prompt) if secret else input(prompt)).strip()
    except EOFError:
        return ""


def _ask_password() -> str:
    """Пароль дважды; пустой — пропустить. Возвращает хэш или ''."""
    first = _ask("Пароль для веб-админки (Enter — пропустить): ", secret=True)
    if not first:
        return ""
    if _ask("Ещё раз: ", secret=True) != first:
        print("[!] пароли не совпали, пропускаю")
        return ""
    return generate_password_hash(first)


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
    if not envfile.get(env, "COOKIES_KEY"):
        env = envfile.put(env, "COOKIES_KEY", generate_key())
        print("[ok] COOKIES_KEY сгенерирован")
    if not envfile.get(env, "WEB_SECRET_KEY"):
        env = envfile.put(env, "WEB_SECRET_KEY", secrets.token_hex(32))
        print("[ok] WEB_SECRET_KEY сгенерирован")

    if envfile.get(env, "BOT_TOKEN") in PLACEHOLDERS:
        token = _ask("BOT_TOKEN (от @BotFather, Enter — пропустить): ")
        if token:
            env = envfile.put(env, "BOT_TOKEN", token)

    admin = envfile.get(env, "ADMIN_TG_ID")
    if admin in PLACEHOLDERS:
        admin = _ask("Твой Telegram ID (узнать у @userinfobot, Enter — пропустить): ")
        if admin.isdigit():
            env = envfile.put(env, "ADMIN_TG_ID", admin)

    if not envfile.get(env, "WEB_PASSWORD_HASH"):
        hashed = _ask_password()
        if hashed:
            env = envfile.put(env, "WEB_PASSWORD_HASH", hashed)

    ENV_PATH.write_text(env, encoding="utf-8")

    storage.init_db()
    print(f"[ok] схема применена: {DB_PATH}")
    if admin.isdigit() and admin != "0":
        storage.make_admin(int(admin))
        print(f"[ok] админ добавлен: {admin}")

    missing = [k for k in ("BOT_TOKEN", "ADMIN_TG_ID") if envfile.get(env, k) in PLACEHOLDERS]
    if missing:
        print(f"[!] заполни в .env: {', '.join(missing)} и запусти --setup ещё раз")
    else:
        # Полный путь к интерпретатору: голый "python" — системный, без зависимостей из .venv.
        print(f"[ok] готово. Бот:      {sys.executable} -m bot.main")
    if envfile.get(env, "WEB_PASSWORD_HASH"):
        port = envfile.get(env, "WEB_PORT") or "9000"
        print(f"[ok]         Админка:  {sys.executable} -m web.app  (порт {port})")
    else:
        print("[i] пароль админки не задан — веб не запустится. Задать: "
              f"{sys.executable} -m db.init_db --web-password")


def add_owner(tg_id: int) -> None:
    storage.init_db()
    storage.make_admin(tg_id)
    print(f"[ok] {tg_id} теперь владелец (админ). Владельцы: {storage.admin_ids()}")
    print("[i] рестарт не нужен")


def remove_owner(tg_id: int) -> None:
    storage.init_db()
    try:
        storage.demote_admin(tg_id)
    except storage.LastAdminError as exc:
        raise SystemExit(f"[!] {exc}")
    print(f"[ok] {tg_id} больше не владелец (доступ к боту остался). Владельцы: {storage.admin_ids()}")


def change_web_password() -> None:
    hashed = _ask_password()
    if not hashed:
        raise SystemExit("[!] пароль не задан")
    envfile.update("WEB_PASSWORD_HASH", hashed)
    print("[ok] пароль админки обновлён. Перезапусти админку, чтобы он вступил в силу")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", action="store_true", help="сгенерить Fernet-ключ и выйти")
    ap.add_argument("--admin", type=int, help="tg_id админа")
    ap.add_argument("--username", type=str, help="ник админа для красоты")
    ap.add_argument("--setup", action="store_true", help="первичная настройка одной командой")
    ap.add_argument("--add-owner", type=int, metavar="TG_ID", help="добавить владельца (админа)")
    ap.add_argument("--remove-owner", type=int, metavar="TG_ID", help="понизить владельца до юзера")
    ap.add_argument("--web-password", action="store_true", help="задать пароль веб-админки")
    args = ap.parse_args()

    if args.key:
        print(generate_key())
        return
    if args.setup:
        setup()
        return
    if args.add_owner:
        add_owner(args.add_owner)
        return
    if args.remove_owner:
        remove_owner(args.remove_owner)
        return
    if args.web_password:
        change_web_password()
        return

    storage.init_db()
    print(f"[ok] схема применена: {DB_PATH}")

    admin_id = args.admin or ADMIN_TG_ID
    if admin_id:
        storage.make_admin(admin_id, args.username)
        print(f"[ok] админ добавлен: {admin_id}")
    else:
        print("[!] админ не задан — укажи --admin <tg_id> или ADMIN_TG_ID в .env")

    print("[i] настройки по умолчанию:")
    for k, v in sorted(storage.all_settings().items()):
        print(f"    {k} = {v}")


if __name__ == "__main__":
    main()
