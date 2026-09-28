"""Быстрая проверка слоя хранения. Не тест-сьют, а именно смоук.

    python smoke_test.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_sandbox = Path(tempfile.mkdtemp())
tmpdb = _sandbox / "smoke.db"
os.environ["DL_BOT_DB"] = str(tmpdb)
os.environ["DL_BOT_DATA"] = str(_sandbox / "data")  # cookies и бэкапы — не боевые
os.environ["DL_BOT_TMP"] = str(_sandbox / "tmp")
os.environ.setdefault("COOKIES_KEY", "")

from accounts.crypto import generate_key  # noqa: E402

os.environ["COOKIES_KEY"] = generate_key()

import importlib  # noqa: E402

import accounts.crypto as crypto  # noqa: E402
import common.config as config  # noqa: E402

importlib.reload(config)
importlib.reload(crypto)

from common import urls  # noqa: E402
from db import storage  # noqa: E402

importlib.reload(storage)

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [ok]   {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


print("\n== URL parsing ==")
cases = [
    ("https://www.instagram.com/reel/Cxyz123/?igsh=MzRl", "instagram", "reel"),
    ("https://instagram.com/p/Cabc/", "instagram", "post"),
    ("https://ddinstagram.com/p/Cabc/", "instagram", "post"),
    ("https://x.com/user/status/1234567?s=20", "twitter", "post"),
    ("https://vxtwitter.com/user/status/999", "twitter", "post"),
    ("https://www.instagram.com/stories/someuser/333/", "instagram", "story"),
    ("https://instagram.com/stories/highlights/777/", "instagram", "highlight"),
]
for raw, src, kind in cases:
    p = urls.parse(raw)
    check(f"{raw[:45]:45} -> {src}/{kind}", p and p.source == src and p.media_kind == kind,
          f"got {p and (p.source, p.media_kind)}")

check("youtube отсеивается", urls.parse("https://youtube.com/watch?v=1") is None)
check("мусорные параметры вычищены",
      urls.normalize("https://instagram.com/p/A/?igsh=X&utm_source=y") == "https://instagram.com/p/A")
check("зеркало и оригинал дают один хэш",
      urls.parse("https://vxtwitter.com/u/status/5").url_hash
      == urls.parse("https://x.com/u/status/5").url_hash)
check("ссылки вытаскиваются из текста",
      len(urls.extract_urls("смотри https://x.com/a/status/1 и https://instagram.com/p/B/")) == 2)

print("\n== Schema / users ==")
storage.init_db()
storage.add_user(111, "simon", role="admin")
storage.add_user(222, "friend")
check("админ распознан", storage.is_admin(111))
check("обычный юзер не админ", not storage.is_admin(222) and storage.is_allowed(222))
check("чужой не в whitelist", not storage.is_allowed(333))
storage.set_user_status(222, "banned")
check("бан работает", not storage.is_allowed(222))
storage.set_user_status(222, "active")

print("\n== Cookies crypto ==")
raw = b"# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc123\n"
rel = crypto.store("test_acc", raw)
check("cookies зашифрованы на диске", (config.COOKIES_DIR.parent.parent / rel).read_bytes() != raw)
check("расшифровка возвращает оригинал", crypto.load(rel) == raw)
with crypto.materialized(rel) as p:
    check("materialized отдаёт живой файл", p.read_bytes() == raw)
    tmp_path = p
check("временный файл подчищен", not tmp_path.exists())
rel_upd = crypto.store("test_upd", raw)
fresh = raw + b".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tnew\n"
with crypto.materialized(rel_upd, save_back=True) as p:
    p.write_bytes(fresh)
check("обновлённые утилитой cookies сохранены", crypto.load(rel_upd) == fresh)
with crypto.materialized(rel_upd, save_back=True) as p:
    p.write_bytes(b"garbage")
check("мусор вместо cookies не сохраняется", crypto.load(rel_upd) == fresh)
try:
    crypto.store("bad", b"definitely not cookies")
    check("мусорный файл отбит", False)
except crypto.CookiesError:
    check("мусорный файл отбит", True)

print("\n== Accounts pool ==")
a1 = storage.add_account("acc_one", rel)
a2 = storage.add_account("acc_two", crypto.store("test_acc2", raw))
picked = storage.pick_account()
check("аккаунт выбирается", picked is not None)
storage.touch_account(picked["id"])
second = storage.pick_account()
check("нагрузка размазывается по пулу", second["id"] != picked["id"], f"{second['id']} vs {picked['id']}")
storage.set_account_status(a1, "expired")
storage.set_account_status(a2, "expired")
check("нет живых аккаунтов -> None", storage.pick_account() is None)
check("has_active_accounts False", not storage.has_active_accounts())
storage.set_account_status(a1, "active")
storage.set_account_status(a2, "active")

print("\n== Tasks ==")
p = urls.parse("https://instagram.com/p/Cabc/")
t1 = storage.create_task(111, 111, p.url, p.url_hash, p.source, p.media_kind, "someuser")
check("задача создана", storage.get_task(t1)["status"] == "queued")
storage.set_task_status(t1, "running")
check("started_at проставлен", storage.get_task(t1)["started_at"] is not None)
storage.set_task_status(t1, "done", files_sent=3)
row = storage.get_task(t1)
check("done + finished_at + files_sent", row["finished_at"] and row["files_sent"] == 3)

t2 = storage.create_task(222, 222, "https://instagram.com/p/Cdef", "hash2", "instagram", "post", "priv_user")
storage.park_pending(t2, a1, "priv_user")
row = storage.get_task(t2)
check("pending_access + TTL + next_check", row["status"] == "pending_access"
      and row["expires_at"] and row["next_check_at"])
check("аккаунт подписки зафиксирован", row["account_id"] == a1)

check("лимит pending считается", storage.pending_count(222) == 1)
check("дефолтный лимит из settings", storage.pending_limit(222) == 5)

check("чужую задачу не отменить", not storage.cancel_task(t2, tg_id=111))
check("свою отменить можно", storage.cancel_task(t2, tg_id=222))

t3 = storage.create_task(111, 111, "https://instagram.com/p/Cghi", "hash3", "instagram", "post", "priv_user")
storage.park_pending(t3, a1, "priv_user")
storage.set_setting("pending_ttl_days", "0")
t4 = storage.create_task(111, 111, "https://instagram.com/p/Cjkl", "hash4", "instagram", "post", "x")
storage.park_pending(t4, a1, "x")
expired = storage.expire_old_tasks()
check("TTL протухание работает", any(t["id"] == t4 for t in expired))
check("статус expired проставлен", storage.get_task(t4)["status"] == "expired")
storage.set_setting("pending_ttl_days", "7")

storage.set_task_status(t1, "running")
check("зависшие running возвращаются в очередь", storage.requeue_stuck_tasks() == 1
      and storage.get_task(t1)["status"] == "queued")

print("\n== Private targets (дедуп) ==")
storage.upsert_private_target("priv_user", a1)
storage.upsert_private_target("priv_user", a1)  # повторно — не дублируется
check("состояние доступа none", storage.get_access_state("priv_user", a1) == "none")
storage.set_access_state("PRIV_USER", a1, "requested")
check("регистр ника не важен", storage.get_access_state("priv_user", a1) == "requested")
waiting = storage.tasks_waiting_for("priv_user", a1)
check("одна проверка покрывает все задачи профиля", len(waiting) == 1, f"got {len(waiting)}")

print("\n== Cache ==")
storage.put_cached("hash_c", [
    {"file_id": "AAA", "file_type": "photo", "size": 100},
    {"file_id": "BBB", "file_type": "video", "size": 200},
], source="instagram")
cached = storage.get_cached("hash_c")
check("порядок карусели сохранён", [c["file_id"] for c in cached] == ["AAA", "BBB"])
storage.get_cached("hash_c")
check("hits считаются", storage.get_cached("hash_c")[0]["hits"] >= 2)
storage.set_setting("cache_enabled", "0")
check("кэш можно вырубить из админки", storage.get_cached("hash_c") == [])
storage.set_setting("cache_enabled", "1")

print("\n== Backup / stats ==")
dest = storage.backup("smoke")
check("бэкап создан", Path(dest).exists() and Path(dest).stat().st_size > 0)
stats = storage.dashboard_stats()
check("статистика собирается", stats["tasks_day"] >= 4 and "by_status" in stats)
storage.log("test_event", tg_id=111, detail="hello")
check("лог пишется", storage.list_logs("test_event")[0]["detail"] == "hello")

print(f"\n=== итого: {ok} ok, {fail} fail ===")
sys.exit(1 if fail else 0)
