"""Правка .env на месте: комментарии и порядок строк сохраняются."""

from __future__ import annotations

import os
import re

from common.config import BASE_DIR

ENV_PATH = BASE_DIR / ".env"


def get(text: str, key: str) -> str:
    m = re.search(rf"^{key}=(.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def put(text: str, key: str, value: str) -> str:
    line = f"{key}={value}"
    if re.search(rf"^{key}=", text, re.MULTILINE):
        return re.sub(rf"^{key}=.*$", lambda _: line, text, flags=re.MULTILINE)
    return text.rstrip("\n") + f"\n{line}\n"


def update(key: str, value: str) -> None:
    """Меняет один ключ в .env (если файла нет — создаёт)."""
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    ENV_PATH.write_text(put(text, key, value), encoding="utf-8")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass
