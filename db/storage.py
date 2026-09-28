"""Единственное место, где мы трогаем SQLite.

Все запросы параметризованные. Бэкап делается перед разрушающими
операциями из админки (правка настроек, смена статусов аккаунтов).
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from common.config import BACKUP_DIR, DB_PATH, SCHEMA_PATH

# --------------------------------------------------------------------
# Подключение
# --------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _in(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def backup(tag: str = "manual") -> str:
    """Таймстемпленный бэкап. Дёргаем перед записью из админки."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"bot_{stamp}_{tag}.db"
    with db() as conn:
        target = sqlite3.connect(dest)
        try:
            conn.backup(target)
        finally:
            target.close()
    return str(dest)


def _rows(conn, sql: str, params: Iterable = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def _row(conn, sql: str, params: Iterable = ()) -> dict | None:
    r = conn.execute(sql, tuple(params)).fetchone()
    return dict(r) if r else None


# --------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------


def get_setting(key: str, default: Any = None) -> Any:
    with db() as conn:
        row = _row(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def get_int(key: str, default: int) -> int:
    try:
        return int(get_setting(key, default))
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    return str(get_setting(key, "1" if default else "0")) in ("1", "true", "True", "on")


def all_settings() -> dict[str, str]:
    with db() as conn:
        return {r["key"]: r["value"] for r in _rows(conn, "SELECT key, value FROM settings")}


def set_setting(key: str, value: Any) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, str(value), _now()),
        )


# --------------------------------------------------------------------
# Пользователи
# --------------------------------------------------------------------


def get_user(tg_id: int) -> dict | None:
    with db() as conn:
        return _row(conn, "SELECT * FROM users WHERE tg_id = ?", (tg_id,))


def is_allowed(tg_id: int) -> bool:
    user = get_user(tg_id)
    return bool(user and user["status"] == "active")


def is_admin(tg_id: int) -> bool:
    user = get_user(tg_id)
    return bool(user and user["role"] == "admin" and user["status"] == "active")


def add_user(tg_id: int, username: str | None = None, role: str = "user",
             added_by: int | None = None, note: str | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO users (tg_id, username, role, added_by, note) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET username = COALESCE(excluded.username, users.username), "
            "status = 'active'",
            (tg_id, username, role, added_by, note),
        )


def set_user_status(tg_id: int, status: str) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET status = ? WHERE tg_id = ?", (status, tg_id))


def list_users() -> list[dict]:
    with db() as conn:
        return _rows(conn, "SELECT * FROM users ORDER BY added_at DESC")


def pending_count(tg_id: int) -> int:
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE tg_id = ? AND status IN ('queued', 'running', 'pending_access')",
            (tg_id,),
        ).fetchone()
    return row["n"]


def pending_limit(tg_id: int) -> int:
    user = get_user(tg_id)
    if user and user["max_pending"]:
        return int(user["max_pending"])
    return get_int("max_pending_per_user", 5)


# --------------------------------------------------------------------
# Аккаунты
# --------------------------------------------------------------------


def add_account(label: str, cookies_path: str, platform: str = "instagram",
                note: str | None = None) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO accounts (label, platform, cookies_path, note) VALUES (?, ?, ?, ?)",
            (label, platform, cookies_path, note),
        )
        return cur.lastrowid


def get_account(account_id: int) -> dict | None:
    with db() as conn:
        return _row(conn, "SELECT * FROM accounts WHERE id = ?", (account_id,))


def list_accounts(platform: str | None = None) -> list[dict]:
    sql = "SELECT * FROM accounts"
    params: tuple = ()
    if platform:
        sql += " WHERE platform = ?"
        params = (platform,)
    sql += " ORDER BY status, label"
    with db() as conn:
        return _rows(conn, sql, params)


def pick_account(platform: str = "instagram") -> dict | None:
    """Наименее нагруженный живой аккаунт. Cooldown снимаем по времени."""
    with db() as conn:
        conn.execute(
            "UPDATE accounts SET status = 'active', cooldown_until = NULL "
            "WHERE status = 'cooldown' AND (cooldown_until IS NULL OR cooldown_until <= ?)",
            (_now(),),
        )
        return _row(
            conn,
            "SELECT * FROM accounts WHERE platform = ? AND status = 'active' "
            "ORDER BY COALESCE(last_used_at, '') ASC, tasks_total ASC LIMIT 1",
            (platform,),
        )


def touch_account(account_id: int, ok: bool = True) -> None:
    """Отмечаем использование и при необходимости уводим в cooldown."""
    burst = get_int("account_tasks_burst", 10)
    cooldown = get_int("account_cooldown_min", 5)
    with db() as conn:
        conn.execute(
            "UPDATE accounts SET last_used_at = ?, last_checked_at = ?, "
            "last_ok_at = CASE WHEN ? THEN ? ELSE last_ok_at END, "
            "tasks_total = tasks_total + 1 WHERE id = ?",
            (_now(), _now(), 1 if ok else 0, _now(), account_id),
        )
        row = conn.execute("SELECT tasks_total FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row and burst and row["tasks_total"] % burst == 0:
            conn.execute(
                "UPDATE accounts SET status = 'cooldown', cooldown_until = ? WHERE id = ? AND status = 'active'",
                (_in(cooldown), account_id),
            )


def cooldown_account(account_id: int, minutes: int) -> None:
    """Принудительный отдых: инста ответила rate limit."""
    with db() as conn:
        conn.execute(
            "UPDATE accounts SET status = 'cooldown', cooldown_until = ?, last_checked_at = ? "
            "WHERE id = ? AND status IN ('active', 'cooldown')",
            (_in(minutes), _now(), account_id),
        )


def set_account_status(account_id: int, status: str, note: str | None = None) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE accounts SET status = ?, last_checked_at = ?, note = COALESCE(?, note) WHERE id = ?",
            (status, _now(), note, account_id),
        )


def update_account_cookies(account_id: int, cookies_path: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE accounts SET cookies_path = ?, status = 'active', last_ok_at = NULL, "
            "cooldown_until = NULL WHERE id = ?",
            (cookies_path, account_id),
        )


def has_active_accounts(platform: str = "instagram") -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM accounts WHERE platform = ? AND status IN ('active', 'cooldown')",
            (platform,),
        ).fetchone()
    return row["n"] > 0


# --------------------------------------------------------------------
# Задачи
# --------------------------------------------------------------------


def create_task(tg_id: int, chat_id: int, url: str, url_hash: str, source: str,
                media_kind: str, target_user: str | None) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (tg_id, chat_id, url, url_hash, source, media_kind, target_user) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tg_id, chat_id, url, url_hash, source, media_kind, target_user),
        )
        return cur.lastrowid


def get_task(task_id: int) -> dict | None:
    with db() as conn:
        return _row(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))


def set_task_status(task_id: int, status: str, *, account_id: int | None = None,
                    error_code: str | None = None, error_detail: str | None = None,
                    files_sent: int | None = None) -> None:
    finished = status in ("done", "failed", "expired", "cancelled")
    with db() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, "
            "account_id = COALESCE(?, account_id), "
            "error_code = ?, error_detail = ?, "
            "files_sent = COALESCE(?, files_sent), "
            "started_at = CASE WHEN ? = 'running' THEN ? ELSE started_at END, "
            "finished_at = CASE WHEN ? THEN ? ELSE finished_at END "
            "WHERE id = ?",
            (status, account_id, error_code, error_detail, files_sent,
             status, _now(), 1 if finished else 0, _now(), task_id),
        )


def set_task_message(task_id: int, message_id: int) -> None:
    with db() as conn:
        conn.execute("UPDATE tasks SET message_id = ? WHERE id = ?", (message_id, task_id))


def bump_attempts(task_id: int) -> None:
    with db() as conn:
        conn.execute("UPDATE tasks SET attempts = attempts + 1 WHERE id = ?", (task_id,))


def park_pending(task_id: int, account_id: int, target_user: str) -> None:
    """Задача уходит в ожидание доступа к приватному профилю."""
    ttl_days = get_int("pending_ttl_days", 7)
    first = get_int("check_interval_1_min", 60)
    with db() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'pending_access', account_id = ?, target_user = ?, "
            "next_check_at = ?, expires_at = ? WHERE id = ?",
            (account_id, target_user, _in(first),
             _in(ttl_days * 24 * 60), task_id),
        )


def reschedule_pending(task_id: int, attempts: int) -> None:
    """Бэкофф: час → 3 часа → 6 часов. На четвёртый день никто уже не примет."""
    if attempts < 24:
        minutes = get_int("check_interval_1_min", 60)
    elif attempts < 32:
        minutes = get_int("check_interval_2_min", 180)
    else:
        minutes = get_int("check_interval_3_min", 360)
    with db() as conn:
        conn.execute("UPDATE tasks SET next_check_at = ? WHERE id = ?", (_in(minutes), task_id))


def due_pending_tasks(limit: int = 50) -> list[dict]:
    with db() as conn:
        return _rows(
            conn,
            "SELECT * FROM tasks WHERE status = 'pending_access' "
            "AND (next_check_at IS NULL OR next_check_at <= ?) ORDER BY next_check_at LIMIT ?",
            (_now(), limit),
        )


def expire_old_tasks() -> list[dict]:
    """Возвращает протухшие — чтобы бот успел сказать юзеру."""
    with db() as conn:
        stale = _rows(
            conn,
            "SELECT * FROM tasks WHERE status = 'pending_access' AND expires_at IS NOT NULL AND expires_at <= ?",
            (_now(),),
        )
        if stale:
            conn.executemany(
                "UPDATE tasks SET status = 'expired', finished_at = ? WHERE id = ?",
                [(_now(), t["id"]) for t in stale],
            )
    return stale


def list_user_tasks(tg_id: int, statuses: tuple[str, ...] = ("queued", "running", "pending_access")) -> list[dict]:
    placeholders = ",".join("?" * len(statuses))
    with db() as conn:
        return _rows(
            conn,
            f"SELECT * FROM tasks WHERE tg_id = ? AND status IN ({placeholders}) ORDER BY created_at",
            (tg_id, *statuses),
        )


def list_tasks(status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM tasks"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with db() as conn:
        return _rows(conn, sql, params)


def cancel_task(task_id: int, tg_id: int | None = None) -> bool:
    """tg_id задаём, когда отменяет юзер — чтобы не отменил чужое."""
    sql = ("UPDATE tasks SET status = 'cancelled', finished_at = ? "
           "WHERE id = ? AND status IN ('queued', 'pending_access')")
    params: list = [_now(), task_id]
    if tg_id is not None:
        sql += " AND tg_id = ?"
        params.append(tg_id)
    with db() as conn:
        cur = conn.execute(sql, tuple(params))
        return cur.rowcount > 0


def requeue_stuck_tasks() -> int:
    """На старте: всё, что осталось в running после падения, возвращаем в очередь."""
    with db() as conn:
        cur = conn.execute("UPDATE tasks SET status = 'queued' WHERE status = 'running'")
        return cur.rowcount


def queued_tasks(limit: int = 50) -> list[dict]:
    with db() as conn:
        return _rows(conn, "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at LIMIT ?", (limit,))


# --------------------------------------------------------------------
# Кэш медиа
# --------------------------------------------------------------------


def get_cached(url_hash: str) -> list[dict]:
    if not get_bool("cache_enabled", True):
        return []
    with db() as conn:
        rows = _rows(
            conn,
            "SELECT * FROM media_cache WHERE url_hash = ? ORDER BY position",
            (url_hash,),
        )
        if rows:
            conn.execute("UPDATE media_cache SET hits = hits + 1 WHERE url_hash = ?", (url_hash,))
    return rows


def put_cached(url_hash: str, items: list[dict], source: str | None = None) -> None:
    """items: [{'file_id':..., 'file_type':..., 'size':...}, ...] в порядке карусели."""
    with db() as conn:
        conn.executemany(
            "INSERT INTO media_cache (url_hash, position, file_id, file_type, size, source) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(url_hash, position) DO UPDATE SET file_id = excluded.file_id",
            [(url_hash, i, it["file_id"], it["file_type"], it.get("size"), source)
             for i, it in enumerate(items)],
        )


def cache_stats() -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS items, COALESCE(SUM(hits), 0) AS hits, "
            "COALESCE(SUM(size), 0) AS bytes FROM media_cache"
        ).fetchone()
    return dict(row)


# --------------------------------------------------------------------
# Приватные профили
# --------------------------------------------------------------------


def upsert_private_target(username: str, account_id: int, state: str = "none") -> dict:
    username = username.lower().lstrip("@")
    with db() as conn:
        conn.execute(
            "INSERT INTO private_targets (username, account_id, access_state) VALUES (?, ?, ?) "
            "ON CONFLICT(username, account_id) DO NOTHING",
            (username, account_id, state),
        )
        return _row(
            conn,
            "SELECT * FROM private_targets WHERE username = ? AND account_id = ?",
            (username, account_id),
        )


def set_access_state(username: str, account_id: int, state: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE private_targets SET access_state = ?, last_checked_at = ?, checks = checks + 1 "
            "WHERE username = ? AND account_id = ?",
            (state, _now(), username.lower().lstrip("@"), account_id),
        )


def get_access_state(username: str, account_id: int) -> str | None:
    with db() as conn:
        row = _row(
            conn,
            "SELECT access_state FROM private_targets WHERE username = ? AND account_id = ?",
            (username.lower().lstrip("@"), account_id),
        )
    return row["access_state"] if row else None


def tasks_waiting_for(username: str, account_id: int) -> list[dict]:
    """Все задачи по этому профилю — дедуп: проверяем профиль один раз."""
    with db() as conn:
        return _rows(
            conn,
            "SELECT * FROM tasks WHERE status = 'pending_access' AND account_id = ? "
            "AND lower(target_user) = ?",
            (account_id, username.lower().lstrip("@")),
        )


# --------------------------------------------------------------------
# Логи и статистика
# --------------------------------------------------------------------


def log(event: str, *, tg_id: int | None = None, task_id: int | None = None,
        account_id: int | None = None, detail: str | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO logs (tg_id, task_id, account_id, event, detail) VALUES (?, ?, ?, ?, ?)",
            (tg_id, task_id, account_id, event, detail),
        )


def list_logs(event: str | None = None, limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM logs"
    params: list = []
    if event:
        sql += " WHERE event = ?"
        params.append(event)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with db() as conn:
        return _rows(conn, sql, params)


def dashboard_stats() -> dict:
    with db() as conn:
        day = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE created_at >= datetime('now', '-1 day')"
        ).fetchone()["n"]
        week = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE created_at >= datetime('now', '-7 day')"
        ).fetchone()["n"]
        by_status = {r["status"]: r["n"] for r in _rows(
            conn, "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
        top = _rows(
            conn,
            "SELECT t.tg_id, u.username, COUNT(*) AS n FROM tasks t "
            "LEFT JOIN users u ON u.tg_id = t.tg_id "
            "WHERE t.created_at >= datetime('now', '-7 day') "
            "GROUP BY t.tg_id ORDER BY n DESC LIMIT 5",
        )
        accounts = {r["status"]: r["n"] for r in _rows(
            conn, "SELECT status, COUNT(*) AS n FROM accounts GROUP BY status")}
    return {
        "tasks_day": day,
        "tasks_week": week,
        "by_status": by_status,
        "top_users": top,
        "accounts": accounts,
        "cache": cache_stats(),
    }
