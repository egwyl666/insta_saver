"""Шифрование cookies.

Файлы cookies — это фактически доступ к аккаунту, поэтому на диске лежат
зашифрованными (Fernet), а расшифровываются во временный файл только на
время самой загрузки, после чего он удаляется.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from common.config import COOKIES_DIR, COOKIES_KEY


class CookiesError(RuntimeError):
    pass


def generate_key() -> str:
    """Разово: python -c 'from accounts.crypto import generate_key; print(generate_key())'"""
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    if not COOKIES_KEY:
        raise CookiesError("COOKIES_KEY не задан в .env")
    try:
        return Fernet(COOKIES_KEY.encode())
    except (ValueError, TypeError) as exc:
        raise CookiesError(f"COOKIES_KEY кривой: {exc}") from exc


def _validate_netscape(raw: bytes) -> None:
    """Грубая проверка, что это правда cookies.txt, а не случайный файл."""
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CookiesError("Файл не в UTF-8 — это точно cookies.txt?") from exc
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    if not lines:
        raise CookiesError("В файле нет ни одной cookie")
    if not any(len(l.split("\t")) >= 6 for l in lines):
        raise CookiesError("Формат не похож на Netscape cookies.txt (нужны табы)")


def store(label: str, raw: bytes) -> str:
    """Шифрует и кладёт в data/cookies/. Возвращает путь для БД."""
    _validate_netscape(raw)
    safe = "".join(c for c in label if c.isalnum() or c in "-_")[:40] or "account"
    path = COOKIES_DIR / f"{safe}.cookies.enc"
    path.write_bytes(_fernet().encrypt(raw))
    os.chmod(path, 0o600)
    return str(path.relative_to(COOKIES_DIR.parent.parent))


def load(rel_path: str) -> bytes:
    path = COOKIES_DIR.parent.parent / rel_path
    if not path.is_file():
        raise CookiesError(f"Файл cookies не найден: {rel_path}")
    try:
        return _fernet().decrypt(path.read_bytes())
    except InvalidToken as exc:
        raise CookiesError("Не удалось расшифровать cookies — сменился COOKIES_KEY?") from exc


@contextmanager
def materialized(rel_path: str):
    """Временно кладёт расшифрованные cookies на диск для yt-dlp / gallery-dl.

    Нужен именно файл: обе утилиты не умеют принимать cookies потоком.
    """
    fd, tmp = tempfile.mkstemp(prefix="ck_", suffix=".txt")
    try:
        os.write(fd, load(rel_path))
        os.close(fd)
        os.chmod(tmp, 0o600)
        yield Path(tmp)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def remove(rel_path: str) -> None:
    path = COOKIES_DIR.parent.parent / rel_path
    try:
        path.unlink()
    except FileNotFoundError:
        pass
