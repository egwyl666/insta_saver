"""Алерты владельцу в личку.

С троттлингом: если инста отвалилась, бот не должен слать одно и то же
сообщение каждые пять минут.
"""

from __future__ import annotations

import logging
import time

from common.config import ADMIN_TG_ID
from db import storage

log = logging.getLogger(__name__)

_last_sent: dict[str, float] = {}
DEFAULT_THROTTLE = 3600  # раз в час на один тип алерта


async def alert(bot, key: str, text: str, throttle: int = DEFAULT_THROTTLE) -> None:
    now = time.time()
    if now - _last_sent.get(key, 0) < throttle:
        return
    _last_sent[key] = now

    storage.log("alert", detail=f"{key}: {text}"[:400])
    if not ADMIN_TG_ID:
        log.warning("ADMIN_TG_ID не задан, алерт только в лог: %s", text)
        return
    try:
        await bot.send_message(ADMIN_TG_ID, f"⚠️ {text}")
    except Exception as exc:  # алерт не должен ронять задачу
        log.error("не смог отправить алерт: %s", exc)


async def account_died(bot, account: dict, code: str) -> None:
    await alert(
        bot,
        f"account_{account['id']}_{code}",
        f"Аккаунт «{account['label']}» выбыл ({code}).\n"
        f"Залей свежие cookies (см. README, раздел Cookies).",
    )


async def no_accounts(bot, platform: str = "instagram") -> None:
    await alert(
        bot,
        f"no_accounts_{platform}",
        f"Живых аккаунтов для {platform} не осталось — закрытое, 18+ и сторис не скачиваются.",
    )
