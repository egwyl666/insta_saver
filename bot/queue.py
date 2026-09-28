"""Очередь задач и воркеры.

Один воркер = одна параллельная закачка. На Pi больше двух смысла нет:
упрёмся в сеть и SD-карту, а инста ещё и обидится на частые запросы.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from accounts import crypto
from bot import alerts, texts
from bot.sender import cleanup, send_from_cache, send_media
from common.config import TMP_DIR
from db import storage
from downloaders import instagram, twitter
from downloaders.base import ACCOUNT_FATAL, E_PRIVATE, DownloadError, check_sizes

log = logging.getLogger(__name__)

task_queue: asyncio.Queue[int] = asyncio.Queue()

DOWNLOADERS = {"instagram": instagram.download, "twitter": twitter.download}


async def enqueue(task_id: int) -> None:
    await task_queue.put(task_id)


def purge_tmp(max_age_min: int = 30) -> int:
    """Метёлка по tmp: и на старте, и периодически."""
    removed = 0
    cutoff = time.time() - max_age_min * 60
    for path in TMP_DIR.glob("task_*"):
        try:
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except FileNotFoundError:
            continue
    return removed


async def _resolve_account(bot, source: str) -> dict | None:
    """Инсте аккаунт нужен, твиттеру — по желанию."""
    account = storage.pick_account(platform=source)
    if account is None and source == "instagram":
        if not storage.has_active_accounts("instagram"):
            await alerts.no_accounts(bot, "instagram")
    return account


async def process(bot, task_id: int) -> None:
    task = storage.get_task(task_id)
    if not task or task["status"] not in ("queued", "running"):
        return

    chat_id = task["chat_id"]
    url = task["url"]
    caption = f"<a href=\"{url}\">источник</a>"

    storage.set_task_status(task_id, "running")

    # --- кэш ---
    if await send_from_cache(bot, chat_id, task["url_hash"], caption=None):
        storage.set_task_status(task_id, "done", files_sent=0)
        storage.log("served_from_cache", tg_id=task["tg_id"], task_id=task_id)
        return

    # --- аккаунт ---
    account = await _resolve_account(bot, task["source"])
    if account is None and task["source"] == "instagram":
        storage.set_task_status(task_id, "failed", error_code="NO_ACCOUNT",
                                error_detail="нет живого аккаунта")
        await bot.send_message(chat_id, "Сейчас нет рабочего аккаунта инсты. Владелец уже знает.")
        return

    work_dir = TMP_DIR / f"task_{task_id}"
    download = DOWNLOADERS[task["source"]]

    try:
        if account:
            with crypto.materialized(account["cookies_path"]) as cookies:
                result = await download(url, work_dir, cookies=cookies)
        else:
            result = await download(url, work_dir, cookies=None)

        check_sizes(result.items, storage.get_int("max_file_mb", 49))

        sent, skipped = await send_media(
            bot, chat_id, result.items, task["url_hash"],
            caption=caption if len(result.items) == 1 else None,
            source=task["source"],
        )

        if sent == 0:
            raise DownloadError("SEND_FAILED", "ничего не удалось отправить")

        if skipped:
            await bot.send_message(chat_id, texts.PARTIAL_TOO_BIG.format(n=skipped))

        storage.set_task_status(task_id, "done", account_id=account["id"] if account else None,
                                files_sent=sent)
        if account:
            storage.touch_account(account["id"], ok=True)
        storage.log("downloaded", tg_id=task["tg_id"], task_id=task_id,
                    account_id=account["id"] if account else None,
                    detail=f"{sent} файл(ов)")

    except DownloadError as exc:
        await _handle_failure(bot, task, account, exc)
    except Exception as exc:  # неожиданное — не роняем воркер
        log.exception("задача %s упала", task_id)
        storage.set_task_status(task_id, "failed", error_code="CRASH", error_detail=str(exc)[:500])
        await bot.send_message(chat_id, texts.error_text("UNKNOWN"))
        await alerts.alert(bot, "crash", f"Задача #{task_id} упала: {exc}"[:300])
    finally:
        cleanup(work_dir)


async def _handle_failure(bot, task: dict, account: dict | None, exc: DownloadError) -> None:
    task_id, chat_id = task["id"], task["chat_id"]
    code = exc.code

    # Аккаунт умер — выводим из пула, задачу возвращаем в очередь на другой аккаунт.
    if account and code in ACCOUNT_FATAL:
        storage.set_account_status(account["id"], "expired" if code == "AUTH_EXPIRED" else "blocked",
                                   note=exc.detail[:200])
        await alerts.account_died(bot, account, code)
        if storage.pick_account(task["source"]):
            storage.set_task_status(task_id, "queued")
            await enqueue(task_id)
            storage.log("retry_other_account", task_id=task_id, account_id=account["id"])
            return

    # Приватный профиль — материал для этапа 3 (ожидание подписки).
    # Пока честно объясняем, почему не смогли.
    if code == E_PRIVATE:
        if task["media_kind"] == "story":
            await bot.send_message(chat_id, texts.STORY_PRIVATE)
        else:
            await bot.send_message(chat_id, texts.error_text(code))

    else:
        await bot.send_message(chat_id, texts.error_text(code, exc.detail[:80]))

    storage.set_task_status(task_id, "failed", account_id=account["id"] if account else None,
                            error_code=code, error_detail=exc.detail)
    storage.log("download_failed", tg_id=task["tg_id"], task_id=task_id,
                account_id=account["id"] if account else None, detail=code)

    if code == "UNKNOWN":
        await alerts.alert(bot, "unknown_error",
                           f"Непонятная ошибка на задаче #{task_id}:\n{exc.detail[:300]}")


async def worker(bot, index: int) -> None:
    log.info("воркер %s стартовал", index)
    while True:
        task_id = await task_queue.get()
        try:
            await process(bot, task_id)
        except Exception:
            log.exception("воркер %s: необработанное на задаче %s", index, task_id)
        finally:
            task_queue.task_done()


async def start_workers(bot, count: int = 2) -> list[asyncio.Task]:
    # Подбираем всё, что зависло после падения.
    requeued = storage.requeue_stuck_tasks()
    if requeued:
        log.info("вернул в очередь зависших задач: %s", requeued)
    for task in storage.queued_tasks(limit=100):
        await enqueue(task["id"])

    purged = purge_tmp(0)
    if purged:
        log.info("подчистил tmp: %s папок", purged)

    return [asyncio.create_task(worker(bot, i + 1)) for i in range(count)]
