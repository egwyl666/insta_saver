"""Смоук веб-админки: вход, CSRF, аккаунты, владельцы, настройки.

Сети не требует, работает на временной базе и временной папке data.

    python smoke_test_web.py
"""

import os
import re
import sys
import tempfile
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_sandbox = Path(tempfile.mkdtemp())
os.environ["DL_BOT_DB"] = str(_sandbox / "web.db")
os.environ["DL_BOT_DATA"] = str(_sandbox / "data")
os.environ["DL_BOT_TMP"] = str(_sandbox / "tmp")

from accounts.crypto import generate_key  # noqa: E402

os.environ["COOKIES_KEY"] = generate_key()

import importlib  # noqa: E402

import accounts.crypto as crypto  # noqa: E402
import common.config as config  # noqa: E402

importlib.reload(config)
importlib.reload(crypto)

from db import storage  # noqa: E402

importlib.reload(storage)

from werkzeug.security import generate_password_hash  # noqa: E402

import accounts.pool as pool  # noqa: E402

importlib.reload(pool)

from web import app as webapp  # noqa: E402

importlib.reload(webapp)

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [ok]   {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


def csrf_of(html: str) -> str:
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    return m.group(1) if m else ""


app = webapp.create_app(SECRET_KEY="test", WEB_USER="admin",
                        WEB_PASSWORD_HASH=generate_password_hash("secret"), TESTING=True)
c = app.test_client()

print("\n== Вход ==")
r = c.get("/")
check("без входа — редирект на логин", r.status_code == 302 and "/login" in r.location)
r = c.post("/login", data={"user": "admin", "password": "wrong"})
check("неверный пароль не пускает", "Неверный" in r.get_data(as_text=True))
for _ in range(5):
    c.post("/login", data={"user": "admin", "password": "wrong"}, environ_base={"REMOTE_ADDR": "9.9.9.9"})
r = c.post("/login", data={"user": "admin", "password": "secret"}, environ_base={"REMOTE_ADDR": "9.9.9.9"})
check("после 5 промахов — пауза даже с верным паролем", r.status_code == 429)
r = c.post("/login?next=//evil.com", data={"user": "admin", "password": "secret"})
check("верный пароль пускает, open redirect отбит", r.status_code == 302 and r.location == "/", r.location)

for path in ("/", "/accounts", "/users", "/tasks", "/settings", "/logs", "/tasks?status=failed"):
    r = c.get(path)
    check(f"страница {path} открывается", r.status_code == 200, r.status_code)

token = csrf_of(c.get("/users").get_data(as_text=True))
check("CSRF-токен в формах", bool(token))

print("\n== CSRF ==")
r = c.post("/users/add", data={"tg_id": "555"})
check("POST без токена отбит", r.status_code == 400)
check("и ничего не сделал", storage.get_user(555) is None)

print("\n== Владельцы ==")
c.post("/users/add", data={"tg_id": "100", "role": "admin", "csrf": token})
c.post("/users/add", data={"tg_id": "200", "username": "@friend", "csrf": token})
check("владелец добавлен", storage.admin_ids() == [100])
check("юзер добавлен, @ срезан", storage.get_user(200)["username"] == "friend")
r = c.post("/users/100/demote", data={"csrf": token}, follow_redirects=True)
check("последнего владельца не понизить", storage.admin_ids() == [100]
      and "последний" in r.get_data(as_text=True))
c.post("/users/100/ban", data={"csrf": token})
check("и не забанить", storage.get_user(100)["status"] == "active")
c.post("/users/200/make_admin", data={"csrf": token})
c.post("/users/100/demote", data={"csrf": token})
check("смена владельца: новый назначен, старый понижен", storage.admin_ids() == [200])
check("старый владелец остался пользователем", storage.is_allowed(100) and not storage.is_admin(100))
c.post("/users/100/ban", data={"csrf": token})
c.post("/users/100/unban", data={"csrf": token})
check("бан/разбан", storage.get_user(100)["status"] == "active")

print("\n== Аккаунты ==")
raw = b"# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc\n"
r = c.post("/accounts/upload", data={"csrf": token, "label": "acc_web", "platform": "instagram",
                                     "cookies": (BytesIO(raw), "cookies.txt")},
           content_type="multipart/form-data", follow_redirects=True)
accs = storage.list_accounts()
check("cookies загружены через веб", len(accs) == 1 and accs[0]["label"] == "acc_web")
check("на диске зашифрованы", crypto.load(accs[0]["cookies_path"]) == raw)
r = c.post("/accounts/upload", data={"csrf": token, "label": "junk",
                                     "cookies": (BytesIO(b"not cookies"), "x.txt")},
           content_type="multipart/form-data", follow_redirects=True)
check("мусорный файл отбит с понятной ошибкой", "Netscape" in r.get_data(as_text=True)
      and len(storage.list_accounts()) == 1)
acc_id = accs[0]["id"]
storage.set_account_status(acc_id, "expired")
c.post("/accounts/upload", data={"csrf": token, "label": "acc_web",
                                 "cookies": (BytesIO(raw), "c.txt")}, content_type="multipart/form-data")
check("повторная загрузка возвращает выбывший в пул", storage.get_account(acc_id)["status"] == "active")
c.post(f"/accounts/{acc_id}/status", data={"csrf": token, "status": "disabled"})
check("выключение", storage.get_account(acc_id)["status"] == "disabled")
path = Path(config.DATA_DIR.parent) / accs[0]["cookies_path"]
c.post(f"/accounts/{acc_id}/delete", data={"csrf": token})
check("удаление: записи нет", storage.get_account(acc_id) is None)
check("удаление: файл cookies стёрт", not path.exists())

print("\n== Настройки ==")
c.post("/settings", data={"csrf": token, "max_file_mb": "abc"})
check("не число не сохраняется", storage.get_int("max_file_mb", 0) == 49)
c.post("/settings", data={"csrf": token, "max_file_mb": "45"})
check("число сохраняется", storage.get_int("max_file_mb", 0) == 45)
check("перед сохранением бэкап", any(config.BACKUP_DIR.glob("*_settings.db")))

print("\n== Задачи ==")
t = storage.create_task(200, 200, "https://instagram.com/p/X", "hx", "instagram", "post", None)
r = c.get("/tasks")
check("задача видна", "instagram.com/p/X" in r.get_data(as_text=True))
c.post(f"/tasks/{t}/cancel", data={"csrf": token})
check("задачу можно снять", storage.get_task(t)["status"] == "cancelled")

print("\n== Выход ==")
c.post("/logout", data={"csrf": token})
check("после выхода снова логин", c.get("/").status_code == 302)

print(f"\n=== итого: {ok} ok, {fail} fail ===")
sys.exit(1 if fail else 0)
