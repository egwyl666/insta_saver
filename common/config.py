"""Пути и переменные окружения. Ничего секретного в коде не лежит."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Переопределяются тестами, чтобы не трогать боевые cookies и бэкапы.
DATA_DIR = Path(os.getenv("DL_BOT_DATA", BASE_DIR / "data"))
COOKIES_DIR = DATA_DIR / "cookies"
BACKUP_DIR = DATA_DIR / "backups"
TMP_DIR = Path(os.getenv("DL_BOT_TMP", BASE_DIR / "tmp"))
DB_PATH = Path(os.getenv("DL_BOT_DB", DATA_DIR / "bot.db"))
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"


def _load_env(path: Path = BASE_DIR / ".env") -> None:
    """Минимальный .env-парсер, чтобы не тащить зависимость."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "0") or 0)
COOKIES_KEY = os.getenv("COOKIES_KEY", "")          # Fernet-ключ для шифрования cookies
WEB_SECRET_KEY = os.getenv("WEB_SECRET_KEY", "")
WEB_USER = os.getenv("WEB_USER", "admin")
WEB_PASSWORD_HASH = os.getenv("WEB_PASSWORD_HASH", "")
WEB_PORT = int(os.getenv("WEB_PORT", "9000") or 9000)
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")  # 127.0.0.1 — только с самой машины

for _d in (DATA_DIR, COOKIES_DIR, BACKUP_DIR, TMP_DIR):
    _d.mkdir(parents=True, exist_ok=True)
