"""Добавление аккаунтов в пул — общее для консоли (accounts.add) и админки."""

from __future__ import annotations

from accounts import crypto
from db import storage


def add_or_update(label: str, raw: bytes, platform: str = "instagram") -> tuple[int, bool]:
    """Шифрует cookies и кладёт в пул. Если label уже есть — меняет cookies
    и возвращает аккаунт в пул. Возвращает (id, создан_ли_новый).

    CookiesError — если файл не похож на cookies.txt.
    """
    label = label.strip()
    if not label:
        raise crypto.CookiesError("Не задано имя аккаунта")
    rel = crypto.store(label, raw)
    existing = next((a for a in storage.list_accounts() if a["label"] == label), None)
    if existing:
        storage.update_account_cookies(existing["id"], rel)
        return existing["id"], False
    return storage.add_account(label, rel, platform=platform), True


def remove(account_id: int) -> dict | None:
    account = storage.delete_account(account_id)
    if account:
        crypto.remove(account["cookies_path"])
    return account
