"""Отправка файлов в Telegram + кэш file_id.

Кэш — главная экономия на Pi: второй раз тот же рилс не качается вообще,
телега отдаёт его по file_id мгновенно.
"""

from __future__ import annotations

import logging
from pathlib import Path

from telegram import InputMediaAnimation, InputMediaPhoto, InputMediaVideo
from telegram.error import BadRequest, TelegramError

from db import storage
from downloaders.base import MediaItem

log = logging.getLogger(__name__)

MEDIA_GROUP_LIMIT = 10  # ограничение Telegram на альбом

_INPUT_MEDIA = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "animation": InputMediaAnimation,
}


def _chunks(seq: list, size: int) -> list[list]:
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def _file_id_of(message) -> tuple[str, str, int] | None:
    """Достаёт (file_id, тип, размер) из отправленного сообщения."""
    if message.photo:
        biggest = message.photo[-1]
        return biggest.file_id, "photo", biggest.file_size or 0
    if message.video:
        return message.video.file_id, "video", message.video.file_size or 0
    if message.animation:
        return message.animation.file_id, "animation", message.animation.file_size or 0
    if message.document:
        return message.document.file_id, "document", message.document.file_size or 0
    return None


async def send_from_cache(bot, chat_id: int, url_hash: str, caption: str | None = None) -> bool:
    """Пробует отдать из кэша. False — если кэша нет или file_id протух."""
    cached = storage.get_cached(url_hash)
    if not cached:
        return False

    try:
        for batch in _chunks(cached, MEDIA_GROUP_LIMIT):
            if len(batch) == 1:
                item = batch[0]
                await _send_single_by_id(bot, chat_id, item["file_id"], item["file_type"], caption)
            else:
                media = []
                for i, item in enumerate(batch):
                    cls = _INPUT_MEDIA.get(item["file_type"], InputMediaPhoto)
                    media.append(cls(media=item["file_id"],
                                     caption=caption if i == 0 else None))
                await bot.send_media_group(chat_id=chat_id, media=media)
        return True
    except BadRequest as exc:
        # Протухший file_id — редко, но бывает. Чистим и качаем заново.
        log.warning("кэш не сработал для %s: %s", url_hash, exc)
        storage.log("cache_miss", detail=f"{url_hash}: {exc}")
        return False


async def _send_single_by_id(bot, chat_id: int, file_id: str, file_type: str, caption: str | None):
    if file_type == "photo":
        return await bot.send_photo(chat_id, file_id, caption=caption)
    if file_type == "video":
        return await bot.send_video(chat_id, file_id, caption=caption, supports_streaming=True)
    if file_type == "animation":
        return await bot.send_animation(chat_id, file_id, caption=caption)
    return await bot.send_document(chat_id, file_id, caption=caption)


async def _send_single_file(bot, chat_id: int, item: MediaItem, caption: str | None):
    with item.path.open("rb") as fh:
        if item.kind == "photo":
            return await bot.send_photo(chat_id, fh, caption=caption)
        if item.kind == "video":
            return await bot.send_video(chat_id, fh, caption=caption, supports_streaming=True)
        if item.kind == "animation":
            return await bot.send_animation(chat_id, fh, caption=caption)
        return await bot.send_document(chat_id, fh, caption=caption)


async def send_media(bot, chat_id: int, items: list[MediaItem], url_hash: str,
                     caption: str | None = None, source: str | None = None) -> tuple[int, int]:
    """Отправляет файлы и кладёт file_id в кэш.

    Возвращает (сколько отправлено, сколько пропущено по размеру).
    """
    max_mb = storage.get_int("max_file_mb", 49)
    limit = max_mb * 1024 * 1024

    sendable = [i for i in items if i.size <= limit]
    skipped = len(items) - len(sendable)
    if not sendable:
        return 0, skipped

    cache_rows: list[dict] = []
    sent = 0

    for batch in _chunks(sendable, MEDIA_GROUP_LIMIT):
        cap = caption if sent == 0 else None
        try:
            if len(batch) == 1:
                msg = await _send_single_file(bot, chat_id, batch[0], cap)
                messages = [msg]
            else:
                media = []
                opened = []
                for i, item in enumerate(batch):
                    fh = item.path.open("rb")
                    opened.append(fh)
                    cls = _INPUT_MEDIA.get(item.kind, InputMediaPhoto)
                    media.append(cls(media=fh, caption=cap if i == 0 else None))
                try:
                    messages = await bot.send_media_group(chat_id=chat_id, media=media)
                finally:
                    for fh in opened:
                        fh.close()
        except TelegramError as exc:
            log.error("не смог отправить batch: %s", exc)
            storage.log("send_failed", detail=str(exc)[:400])
            continue

        sent += len(messages)
        for msg in messages:
            info = _file_id_of(msg)
            if info:
                file_id, file_type, size = info
                cache_rows.append({"file_id": file_id, "file_type": file_type, "size": size})

    # Кэшируем только целиком собранный пост — иначе при повторе отдадим огрызок.
    if cache_rows and len(cache_rows) == len(items):
        storage.put_cached(url_hash, cache_rows, source=source)

    return sent, skipped


def cleanup(work_dir: Path) -> None:
    import shutil
    shutil.rmtree(work_dir, ignore_errors=True)
