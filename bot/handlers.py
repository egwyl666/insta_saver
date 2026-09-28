"""Хендлеры Telegram."""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from bot import texts
from bot.queue import enqueue
from common import urls
from db import storage

log = logging.getLogger(__name__)


def _guard(update: Update) -> tuple[bool, int]:
    user = update.effective_user
    if not user:
        return False, 0
    row = storage.get_user(user.id)
    if not row:
        return False, user.id
    if row["status"] == "banned":
        return False, user.id
    # Ник мог поменяться — подтягиваем.
    if user.username and row["username"] != user.username:
        storage.add_user(user.id, user.username, role=row["role"])
    return True, user.id


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed, tg_id = _guard(update)
    if not allowed:
        row = storage.get_user(tg_id)
        await update.message.reply_text(texts.BANNED if row else texts.NOT_ALLOWED)
        storage.log("access_denied", tg_id=tg_id,
                    detail=update.effective_user.username or "")
        return
    await update.message.reply_text(texts.START)


_denied_at: dict[int, float] = {}
DENIED_REPLY_EVERY = 3600  # чужим отвечаем не чаще раза в час, а не на каждое сообщение


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # В группе бот молчит обо всём, кроме ссылок от своих, — иначе он будет
    # отвечать «ссылок нет» на каждую реплику.
    private = update.effective_chat.type == ChatType.PRIVATE

    allowed, tg_id = _guard(update)
    if not allowed:
        now = time.monotonic()
        if private and now - _denied_at.get(tg_id, -DENIED_REPLY_EVERY) >= DENIED_REPLY_EVERY:
            _denied_at[tg_id] = now
            row = storage.get_user(tg_id)
            await update.message.reply_text(texts.BANNED if row else texts.NOT_ALLOWED)
        return

    text = update.message.text or update.message.caption or ""
    found = urls.extract_urls(text)
    parsed = [p for p in (urls.parse(u) for u in found) if p]

    if not parsed:
        if private:
            await update.message.reply_text(texts.NO_LINKS)
        return

    limit = storage.pending_limit(tg_id)
    active_urls = {t["url"] for t in storage.list_user_tasks(tg_id)}

    for link in parsed:
        if link.url in active_urls:
            await update.message.reply_text(texts.DUPLICATE)
            continue

        count = storage.pending_count(tg_id)
        if count >= limit:
            await update.message.reply_text(texts.LIMIT_REACHED.format(count=count, limit=limit))
            break

        task_id = storage.create_task(
            tg_id=tg_id,
            chat_id=update.effective_chat.id,
            url=link.url,
            url_hash=link.url_hash,
            source=link.source,
            media_kind=link.media_kind,
            target_user=link.target_user,
        )
        msg = await update.message.reply_text(f"{texts.ACCEPTED} (#{task_id})")
        storage.set_task_message(task_id, msg.message_id)
        active_urls.add(link.url)
        await enqueue(task_id)
        storage.log("task_created", tg_id=tg_id, task_id=task_id, detail=link.url)


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed, tg_id = _guard(update)
    if not allowed:
        return
    tasks = storage.list_user_tasks(tg_id)
    if not tasks:
        await update.message.reply_text(texts.QUEUE_EMPTY)
        return
    lines = [texts.QUEUE_ROW.format(id=t["id"], status=t["status"], url=t["url"]) for t in tasks]
    await update.message.reply_text(texts.QUEUE_HEADER + "\n".join(lines),
                                    disable_web_page_preview=True)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed, tg_id = _guard(update)
    if not allowed:
        return
    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.message.reply_text(texts.CANCEL_USAGE)
        return
    task_id = int(context.args[0].lstrip("#"))
    if storage.cancel_task(task_id, tg_id=tg_id):
        await update.message.reply_text(texts.CANCELLED.format(id=task_id))
        storage.log("task_cancelled", tg_id=tg_id, task_id=task_id)
    else:
        await update.message.reply_text(texts.CANCEL_FAILED)


# --------------------------- админские ---------------------------


async def cmd_allow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_id = update.effective_user.id
    if not storage.is_admin(tg_id):
        await update.message.reply_text(texts.ADMIN_ONLY)
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(texts.ALLOW_USAGE)
        return
    new_id = int(context.args[0])
    username = context.args[1] if len(context.args) > 1 else None
    storage.add_user(new_id, username, role="user", added_by=tg_id)
    await update.message.reply_text(texts.ALLOWED.format(id=new_id))
    storage.log("user_allowed", tg_id=tg_id, detail=str(new_id))


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_id = update.effective_user.id
    if not storage.is_admin(tg_id):
        await update.message.reply_text(texts.ADMIN_ONLY)
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(texts.BAN_USAGE)
        return
    target = int(context.args[0])
    storage.set_user_status(target, "banned")
    await update.message.reply_text(texts.USER_BANNED.format(id=target))
    storage.log("user_banned", tg_id=tg_id, detail=str(target))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not storage.is_admin(update.effective_user.id):
        await update.message.reply_text(texts.ADMIN_ONLY)
        return
    s = storage.dashboard_stats()
    accounts = ", ".join(f"{k}: {v}" for k, v in s["accounts"].items()) or "нет"
    statuses = ", ".join(f"{k}: {v}" for k, v in s["by_status"].items()) or "нет"
    await update.message.reply_text(
        f"<b>За сутки:</b> {s['tasks_day']}\n"
        f"<b>За неделю:</b> {s['tasks_week']}\n"
        f"<b>Статусы:</b> {statuses}\n"
        f"<b>Аккаунты:</b> {accounts}\n"
        f"<b>Кэш:</b> {s['cache']['items']} записей, {s['cache']['hits']} попаданий",
        parse_mode=ParseMode.HTML,
    )
