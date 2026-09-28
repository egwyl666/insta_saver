"""Точка входа бота.

    python -m bot.main
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram.ext import (  # noqa: E402
    AIORateLimiter,
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import handlers  # noqa: E402
from bot.queue import purge_tmp, start_workers  # noqa: E402
from common.config import ADMIN_TG_ID, BOT_TOKEN  # noqa: E402
from db import storage  # noqa: E402

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("dl_bot")


async def _on_start(app: Application) -> None:
    app.bot_data["workers"] = await start_workers(app.bot, count=2)
    log.info("воркеры подняты")


async def _on_stop(app: Application) -> None:
    for task in app.bot_data.get("workers", []):
        task.cancel()
    log.info("воркеры остановлены")


async def _periodic_cleanup(context) -> None:
    keep = storage.get_int("tmp_keep_minutes", 30)
    removed = purge_tmp(keep)
    if removed:
        log.info("подчистил tmp: %s папок", removed)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан в .env")

    storage.init_db()
    # Только первый запуск: дальше админами рулят из админки и /admin,
    # и рестарт не должен возвращать права (или снимать бан) тому, кого понизили.
    if ADMIN_TG_ID and not storage.get_user(ADMIN_TG_ID):
        storage.add_user(ADMIN_TG_ID, role="admin")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .rate_limiter(AIORateLimiter())
        .post_init(_on_start)
        .post_shutdown(_on_stop)
        .build()
    )

    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("help", handlers.cmd_start))
    app.add_handler(CommandHandler("queue", handlers.cmd_queue))
    app.add_handler(CommandHandler("cancel", handlers.cmd_cancel))
    app.add_handler(CommandHandler("allow", handlers.cmd_allow))
    app.add_handler(CommandHandler("ban", handlers.cmd_ban))
    app.add_handler(CommandHandler("stats", handlers.cmd_stats))
    app.add_handler(CommandHandler("owner", handlers.cmd_owner))
    app.add_handler(CommandHandler("unowner", handlers.cmd_unowner))
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handlers.on_message))

    if app.job_queue:
        app.job_queue.run_repeating(_periodic_cleanup, interval=1800, first=600)

    log.info("бот стартует")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
