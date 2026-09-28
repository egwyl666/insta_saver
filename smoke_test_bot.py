"""Смоук этапа 2: ядро бота на фейковом Telegram и фейковых скачивалках.

Сети не требует. Проверяет то, что реально ломается: классификация ошибок,
разбивка на медиагруппы, кэш, поведение при смерти аккаунта.

    python smoke_test_bot.py
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from telegram.error import TelegramError

sys.path.insert(0, str(Path(__file__).resolve().parent))

_sandbox = Path(tempfile.mkdtemp())
tmpdb = _sandbox / "smoke2.db"
os.environ["DL_BOT_DB"] = str(tmpdb)
os.environ["DL_BOT_DATA"] = str(_sandbox / "data")  # cookies и бэкапы — не боевые
os.environ["DL_BOT_TMP"] = str(_sandbox / "tmp")

from accounts.crypto import generate_key  # noqa: E402

os.environ["COOKIES_KEY"] = generate_key()
os.environ["ADMIN_TG_ID"] = "111"

import importlib  # noqa: E402

import accounts.crypto as crypto  # noqa: E402
import common.config as config  # noqa: E402

importlib.reload(config)
importlib.reload(crypto)

from db import storage  # noqa: E402

importlib.reload(storage)

from bot import queue as qmod  # noqa: E402
from bot import sender  # noqa: E402
from downloaders import base as dl  # noqa: E402

importlib.reload(sender)
importlib.reload(qmod)

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [ok]   {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


# ------------------------------------------------------------------
# Фейковый Telegram
# ------------------------------------------------------------------
class FakeBot:
    def __init__(self):
        self.sent = []       # отправленные медиа
        self.messages = []   # текстовые сообщения
        self._n = 0

    def _msg(self, kind="photo"):
        self._n += 1
        fid = f"FILE{self._n}"
        base = dict(photo=None, video=None, animation=None, document=None)
        if kind == "photo":
            base["photo"] = [SimpleNamespace(file_id=fid, file_size=1000)]
        else:
            base[kind] = SimpleNamespace(file_id=fid, file_size=2000)
        return SimpleNamespace(**base, message_id=self._n)

    async def send_photo(self, chat_id, f, caption=None, **kw):
        self.sent.append(("photo", chat_id))
        return self._msg("photo")

    async def send_video(self, chat_id, f, caption=None, **kw):
        self.sent.append(("video", chat_id))
        return self._msg("video")

    async def send_animation(self, chat_id, f, caption=None, **kw):
        self.sent.append(("animation", chat_id))
        return self._msg("animation")

    async def send_document(self, chat_id, f, caption=None, **kw):
        self.sent.append(("document", chat_id))
        return self._msg("document")

    async def send_media_group(self, chat_id, media, **kw):
        self.sent.append(("group", chat_id, len(media)))
        return [self._msg("photo") for _ in media]

    async def send_message(self, chat_id, text, **kw):
        self.messages.append(text)
        return self._msg("photo")


def make_files(dirpath: Path, names: list[str], size: int = 1024) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    for n in names:
        (dirpath / n).write_bytes(b"x" * size)


# ------------------------------------------------------------------
print("\n== Классификация ошибок ==")
cases = [
    ("ERROR: This account is private", dl.E_PRIVATE),
    ("Requested content is not available, rate-limit reached (429)", dl.E_RATE),
    ("ERROR: Login required to access this content", dl.E_LOGIN),
    ("challenge_required: checkpoint", dl.E_BLOCKED),
    ("HTTP Error 404: Not Found", dl.E_NOT_FOUND),
    ("ERROR: No video could be found in this tweet", dl.E_EMPTY),
    ("Your cookies are no longer valid", dl.E_AUTH_EXPIRED),
    ("ERROR: unable to download webpage", dl.E_UNKNOWN),
    # протухшие cookies у gallery-dl выглядят как редирект на логин
    ("[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)",
     dl.E_AUTH_EXPIRED),
    ("[instagram][error] Account is private", dl.E_PRIVATE),
    # "age" внутри "message"/"image" — не возрастное ограничение
    ("ERROR: Unable to download image: message truncated", dl.E_UNKNOWN),
    ("HTTP Error 500: page error", dl.E_UNKNOWN),
    ("ERROR: This video is age-restricted", dl.E_LOGIN),
    ("ERROR: [twitter] 1: NSFW tweet requires authentication", dl.E_LOGIN),
]
for text, expected in cases:
    got = dl.classify(text)
    check(f"{text[:42]:42} -> {expected}", got == expected, f"got {got}")

print("\n== Сбор файлов ==")
work = Path(tempfile.mkdtemp())
make_files(work, ["01_a.jpg", "02_b.mp4", "03_c.png", "meta.json", "trash.part"])
items = dl.collect(work)
check("мусор отфильтрован", len(items) == 3, f"got {len(items)}")
check("порядок карусели сохранён",
      [i.path.name for i in items] == ["01_a.jpg", "02_b.mp4", "03_c.png"])
check("типы определены", [i.kind for i in items] == ["photo", "video", "photo"])

big = Path(tempfile.mkdtemp())
make_files(big, ["01_huge.mp4"], size=3 * 1024 * 1024)
try:
    dl.check_sizes(dl.collect(big), max_mb=1)
    check("большой файл отбит", False)
except dl.DownloadError as exc:
    check("большой файл отбит", exc.code == dl.E_TOO_LARGE)

print("\n== Подготовка окружения ==")
storage.init_db()
storage.add_user(111, "simon", role="admin")
storage.add_user(222, "friend")
raw = b"# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc\n"
acc1 = storage.add_account("acc_one", crypto.store("acc_one", raw))
acc2 = storage.add_account("acc_two", crypto.store("acc_two", raw))
check("два аккаунта в пуле", len(storage.list_accounts("instagram")) == 2)


async def main():
    bot = FakeBot()

    print("\n== Отправка и медиагруппы ==")
    src = Path(tempfile.mkdtemp())
    make_files(src, [f"{i:02d}_p.jpg" for i in range(12)])
    sent, skipped = await sender.send_media(bot, 111, dl.collect(src), "hash_group", source="instagram")
    groups = [s for s in bot.sent if s[0] == "group"]
    check("12 фото разбиты на 10 + 2", [g[2] for g in groups] == [10, 2], f"got {[g[2] for g in groups]}")
    check("отправлено 12", sent == 12 and skipped == 0)
    check("кэш записан", len(storage.get_cached("hash_group")) == 12)

    bot.sent.clear()
    served = await sender.send_from_cache(bot, 111, "hash_group")
    check("повтор отдан из кэша", served and len([s for s in bot.sent if s[0] == "group"]) == 2)

    mixed = Path(tempfile.mkdtemp())
    make_files(mixed, ["01_ok.jpg"], size=1024)
    make_files(mixed, ["02_big.mp4"], size=60 * 1024 * 1024)
    bot.sent.clear()
    sent, skipped = await sender.send_media(bot, 111, dl.collect(mixed), "hash_mixed")
    check("огромный файл пропущен, остальное ушло", sent == 1 and skipped == 1)
    check("частичный пост не кэшируется", storage.get_cached("hash_mixed") == [])

    print("\n== Инста без аккаунта ==")
    used_cookies = []

    async def fake_public(url, work_dir, cookies=None, timeout=300):
        used_cookies.append(cookies)
        make_files(work_dir, ["01_pub.jpg"])
        return dl.DownloadResult(items=dl.collect(work_dir))

    qmod.DOWNLOADERS["instagram"] = fake_public
    t_pub = storage.create_task(222, 222, "https://instagram.com/p/PUB", "h_PUB",
                                "instagram", "post", "someone")
    await qmod.process(bot, t_pub)
    row = storage.get_task(t_pub)
    check("публичный пост скачан анонимно", row["status"] == "done" and row["account_id"] is None,
          f"{row['status']} {row['account_id']}")
    check("аккаунт даже не трогали", used_cookies == [None], f"{used_cookies}")

    used_cookies.clear()

    async def fake_gone(url, work_dir, cookies=None, timeout=300):
        used_cookies.append(cookies)
        raise dl.DownloadError(dl.E_NOT_FOUND, "404")

    qmod.DOWNLOADERS["instagram"] = fake_gone
    t_gone = storage.create_task(222, 222, "https://instagram.com/p/GONE", "h_GONE",
                                 "instagram", "post", "someone")
    await qmod.process(bot, t_gone)
    check("удалённый пост: аккаунт не тратим", used_cookies == [None]
          and storage.get_task(t_gone)["status"] == "failed", f"{used_cookies}")

    print("\n== Полный цикл задачи ==")

    async def fake_ok(url, work_dir, cookies=None, timeout=300):
        if cookies is None:  # этот пост анонимно не отдаётся
            raise dl.DownloadError(dl.E_LOGIN, "login required")
        make_files(work_dir, ["01_a.jpg", "02_b.jpg"])
        return dl.DownloadResult(items=dl.collect(work_dir))

    qmod.DOWNLOADERS["instagram"] = fake_ok
    t1 = storage.create_task(222, 222, "https://instagram.com/p/AAA", "h_AAA",
                             "instagram", "post", "someone")
    bot.sent.clear()
    await qmod.process(bot, t1)
    row = storage.get_task(t1)
    check("задача done", row["status"] == "done", row["status"])
    check("файлы отправлены", row["files_sent"] == 2)
    check("аккаунт зафиксирован", row["account_id"] in (acc1, acc2))
    check("tmp подчищен", not (config.TMP_DIR / f"task_{t1}").exists())

    async def fake_never_called(url, work_dir, cookies=None, timeout=300):
        raise AssertionError("качать не должны были — есть кэш")

    qmod.DOWNLOADERS["instagram"] = fake_never_called
    t2 = storage.create_task(111, 111, "https://instagram.com/p/AAA", "h_AAA",
                             "instagram", "post", "someone")
    await qmod.process(bot, t2)
    check("вторая такая же задача взята из кэша", storage.get_task(t2)["status"] == "done")

    print("\n== Ошибки ==")

    async def fake_private(url, work_dir, cookies=None, timeout=300):
        raise dl.DownloadError(dl.E_PRIVATE, "This account is private")

    qmod.DOWNLOADERS["instagram"] = fake_private
    bot.messages.clear()
    t3 = storage.create_task(222, 222, "https://instagram.com/p/PRIV", "h_PRIV",
                             "instagram", "post", "privguy")
    await qmod.process(bot, t3)
    check("приватный -> failed", storage.get_task(t3)["status"] == "failed")
    check("юзеру объяснили причину", any("приватн" in m.lower() for m in bot.messages),
          f"{bot.messages}")

    bot.messages.clear()
    t4 = storage.create_task(222, 222, "https://instagram.com/stories/privguy/1", "h_ST",
                             "instagram", "story", "privguy")
    await qmod.process(bot, t4)
    check("сторис приватного: объясняем, что протухнет",
          any("24 часа" in m for m in bot.messages), f"{bot.messages}")

    print("\n== Смерть аккаунта ==")
    calls = {"n": 0}

    async def fake_expired_once(url, work_dir, cookies=None, timeout=300):
        if cookies is None:
            raise dl.DownloadError(dl.E_LOGIN, "login required")
        calls["n"] += 1
        if calls["n"] == 1:
            raise dl.DownloadError(dl.E_AUTH_EXPIRED, "cookies are no longer valid")
        make_files(work_dir, ["01_a.jpg"])
        return dl.DownloadResult(items=dl.collect(work_dir))

    qmod.DOWNLOADERS["instagram"] = fake_expired_once
    bot.messages.clear()
    t5 = storage.create_task(222, 222, "https://instagram.com/p/EXP", "h_EXP",
                             "instagram", "post", "someone")
    await qmod.process(bot, t5)
    dead = [a for a in storage.list_accounts("instagram") if a["status"] == "expired"]
    check("протухший аккаунт выведен из пула", len(dead) == 1, f"{[a['status'] for a in storage.list_accounts()]}")
    check("владельцу ушёл алерт", any("выбыл" in m for m in bot.messages), f"{bot.messages}")
    check("задача вернулась в очередь", storage.get_task(t5)["status"] == "queued")

    # Забираем её из очереди и доигрываем на втором аккаунте
    queued_id = await qmod.task_queue.get()
    await qmod.process(bot, queued_id)
    check("на втором аккаунте скачалось", storage.get_task(t5)["status"] == "done")

    print("\n== Rate limit ==")

    async def fake_rate(url, work_dir, cookies=None, timeout=300):
        raise dl.DownloadError(dl.E_RATE, "429 Too Many Requests")

    qmod.DOWNLOADERS["instagram"] = fake_rate
    t_rate = storage.create_task(222, 222, "https://instagram.com/p/RATE", "h_RATE",
                                 "instagram", "post", "someone")
    await qmod.process(bot, t_rate)
    resting = [a for a in storage.list_accounts("instagram") if a["status"] == "cooldown"]
    check("аккаунт после rate limit ушёл отдыхать", len(resting) == 1,
          f"{[a['status'] for a in storage.list_accounts()]}")
    check("других нет — задача failed, а не по кругу", storage.get_task(t_rate)["status"] == "failed")

    print("\n== Частичная отправка ==")

    class FlakyBot(FakeBot):
        async def send_media_group(self, chat_id, media, **kw):
            if len(media) == 2:  # второй альбом телега «не приняла»
                raise TelegramError("boom")
            return await super().send_media_group(chat_id, media, **kw)

    async def fake_twelve(url, work_dir, cookies=None, timeout=300):
        make_files(work_dir, [f"{i:02d}_p.jpg" for i in range(12)])
        return dl.DownloadResult(items=dl.collect(work_dir))

    qmod.DOWNLOADERS["twitter"] = fake_twelve
    flaky = FlakyBot()
    t_part = storage.create_task(222, 222, "https://twitter.com/u/status/12", "h_PART",
                                 "twitter", "post", "u")
    await qmod.process(flaky, t_part)
    check("юзеру сказали, что часть не дошла",
          any("не приняла" in m for m in flaky.messages), f"{flaky.messages}")
    check("огрызок не закэширован", storage.get_cached("h_PART") == [])

    print("\n== Нет аккаунтов ==")
    for a in storage.list_accounts("instagram"):
        storage.set_account_status(a["id"], "blocked")
    bot.messages.clear()
    t6 = storage.create_task(222, 222, "https://instagram.com/p/NOACC", "h_NO",
                             "instagram", "post", "someone")
    await qmod.process(bot, t6)
    check("задача не зависла", storage.get_task(t6)["status"] == "failed")
    check("юзеру сказали про отсутствие аккаунта",
          any("аккаунт" in m.lower() for m in bot.messages), f"{bot.messages}")
    check("после rate limit анонимный режим на паузе", qmod._anon_paused_until > time.time())

    qmod._anon_paused_until = 0.0
    qmod.DOWNLOADERS["instagram"] = fake_public
    t_pub2 = storage.create_task(222, 222, "https://instagram.com/p/PUB2", "h_PUB2",
                                 "instagram", "post", "someone")
    await qmod.process(bot, t_pub2)
    check("пул пуст, а публичное качается", storage.get_task(t_pub2)["status"] == "done")

    print("\n== Твиттер без аккаунта ==")

    async def fake_tw(url, work_dir, cookies=None, timeout=300):
        check("твиттеру cookies не обязательны", cookies is None)
        make_files(work_dir, ["01_t.mp4"])
        return dl.DownloadResult(items=dl.collect(work_dir))

    qmod.DOWNLOADERS["twitter"] = fake_tw
    t7 = storage.create_task(222, 222, "https://twitter.com/u/status/1", "h_TW",
                             "twitter", "post", "u")
    await qmod.process(bot, t7)
    check("твит скачался без аккаунта", storage.get_task(t7)["status"] == "done")

    print("\n== Метёлка по tmp ==")
    stale = config.TMP_DIR / "task_9999"
    make_files(stale, ["leftover.jpg"])
    check("мусор от упавшей задачи убран", qmod.purge_tmp(0) >= 1 and not stale.exists())


asyncio.run(main())
print(f"\n=== итого: {ok} ok, {fail} fail ===")
sys.exit(1 if fail else 0)
