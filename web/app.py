"""Веб-админка.

    python -m web.app          # порт из WEB_PORT (по умолчанию 9000)

Вход по логину/паролю из .env (WEB_USER / WEB_PASSWORD_HASH). Работает
отдельным процессом от бота: общая у них только база, поэтому всё, что
меняется здесь (аккаунты, юзеры, владельцы, настройки), бот видит сразу.

Это HTTP без шифрования: держи порт в домашней сети или за VPN,
в интернет напрямую не выставляй.
"""

from __future__ import annotations

import logging
import secrets
import sys
import time
from functools import wraps
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import (  # noqa: E402
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash  # noqa: E402

from accounts import crypto, pool  # noqa: E402
from common import config  # noqa: E402
from db import storage  # noqa: E402

log = logging.getLogger("dl_bot.web")

TASK_STATUSES = ("queued", "running", "done", "failed", "pending_access", "expired", "cancelled")
ACCOUNT_STATUSES = ("active", "cooldown", "expired", "blocked", "disabled")
MAX_LOGIN_FAILS = 5
LOCKOUT_SEC = 300

SETTING_HELP = {
    "max_pending_per_user": "Сколько задач одновременно у одного юзера",
    "max_file_mb": "Лимит размера файла, МБ (облачный Bot API — до 50)",
    "account_cooldown_min": "Отдых аккаунта после серии задач, мин",
    "account_tasks_burst": "Задач подряд до отдыха",
    "account_rate_cooldown_min": "Отдых аккаунта после rate limit, мин",
    "cache_enabled": "Кэш file_id: 1 — вкл, 0 — выкл",
    "instagram_anon_first": "Инста сначала без аккаунта: 1 — да, 0 — сразу с аккаунтом",
    "tmp_keep_minutes": "Через сколько минут чистить брошенные файлы в tmp",
    "pending_ttl_days": "Этап 3: сколько дней ждать принятия подписки",
    "stories_for_private": "Этап 3: ставить ли сторис приватных в ожидание",
    "check_interval_1_min": "Этап 3: интервал проверки подписки, первые сутки",
    "check_interval_2_min": "Этап 3: интервал проверки подписки, вторые сутки",
    "check_interval_3_min": "Этап 3: интервал проверки подписки, дальше",
}


def create_app(**overrides) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=config.WEB_SECRET_KEY,
        WEB_USER=config.WEB_USER,
        WEB_PASSWORD_HASH=config.WEB_PASSWORD_HASH,
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,   # cookies.txt — это килобайты
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=7 * 24 * 3600,
    )
    app.config.update(overrides)
    if not app.config["SECRET_KEY"]:
        raise SystemExit("WEB_SECRET_KEY не задан — запусти python -m db.init_db --setup")

    storage.init_db()
    login_fails: dict[str, tuple[int, float]] = {}

    # ------------------------------------------------------------ защита

    @app.before_request
    def _guard():
        if request.endpoint in ("login", "static"):
            return None
        if not session.get("auth"):
            return redirect(url_for("login", next=request.path))
        if request.method == "POST" and request.form.get("csrf") != session.get("csrf"):
            abort(400, "CSRF")
        return None

    @app.after_request
    def _headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        return resp

    @app.context_processor
    def _ctx():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf": session["csrf"], "admins": set(storage.admin_ids())}

    def _backup(tag: str) -> None:
        try:
            storage.backup(tag)
        except Exception as exc:  # бэкап не должен блокировать действие
            log.warning("бэкап не удался: %s", exc)

    def _redirect_back(default: str):
        return redirect(request.referrer or url_for(default))

    # ------------------------------------------------------------ вход

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not app.config["WEB_PASSWORD_HASH"]:
            return render_template("login.html", no_password=True)
        ip = request.remote_addr or "?"
        fails, until = login_fails.get(ip, (0, 0.0))
        if request.method == "POST":
            if time.time() < until:
                flash(f"Слишком много попыток. Подожди {int(until - time.time()) // 60 + 1} мин.", "err")
                return render_template("login.html"), 429
            ok = (secrets.compare_digest(request.form.get("user", ""), app.config["WEB_USER"])
                  and check_password_hash(app.config["WEB_PASSWORD_HASH"],
                                          request.form.get("password", "")))
            if ok:
                login_fails.pop(ip, None)
                session.clear()
                session.permanent = True
                session["auth"] = True
                session["csrf"] = secrets.token_hex(16)
                storage.log("web_login", detail=ip)
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
            fails += 1
            # После MAX_LOGIN_FAILS промахов — пауза, потом счёт заново.
            login_fails[ip] = ((0, time.time() + LOCKOUT_SEC) if fails >= MAX_LOGIN_FAILS
                               else (fails, 0.0))
            storage.log("web_login_failed", detail=ip)
            flash("Неверный логин или пароль", "err")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------ сводка

    @app.route("/")
    def dashboard():
        return render_template(
            "dashboard.html",
            s=storage.dashboard_stats(),
            failed=storage.list_tasks("failed", limit=10),
            accounts=storage.list_accounts(),
        )

    # ------------------------------------------------------------ аккаунты

    @app.route("/accounts")
    def accounts():
        return render_template("accounts.html", accounts=storage.list_accounts(),
                               statuses=ACCOUNT_STATUSES)

    @app.post("/accounts/upload")
    def accounts_upload():
        file = request.files.get("cookies")
        label = request.form.get("label", "").strip()
        platform = request.form.get("platform", "instagram")
        if platform not in ("instagram", "twitter"):
            abort(400)
        if not file or not file.filename:
            flash("Выбери файл cookies.txt", "err")
            return redirect(url_for("accounts"))
        try:
            account_id, created = pool.add_or_update(label or Path(file.filename).stem,
                                                     file.read(), platform)
        except crypto.CookiesError as exc:
            flash(str(exc), "err")
            return redirect(url_for("accounts"))
        storage.log("web_account_" + ("added" if created else "refreshed"), account_id=account_id)
        flash("Аккаунт добавлен в пул" if created else "Cookies обновлены, аккаунт снова в пуле", "ok")
        return redirect(url_for("accounts"))

    @app.post("/accounts/<int:account_id>/status")
    def accounts_status(account_id: int):
        status = request.form.get("status", "")
        if status not in ("active", "disabled"):
            abort(400)
        _backup("account_status")
        storage.set_account_status(account_id, status, note="из админки")
        storage.log("web_account_status", account_id=account_id, detail=status)
        flash("Статус аккаунта изменён", "ok")
        return redirect(url_for("accounts"))

    @app.post("/accounts/<int:account_id>/delete")
    def accounts_delete(account_id: int):
        _backup("account_delete")
        account = pool.remove(account_id)
        if account:
            storage.log("web_account_deleted", detail=account["label"])
            flash(f"Аккаунт «{account['label']}» удалён", "ok")
        return redirect(url_for("accounts"))

    # ------------------------------------------------------------ пользователи

    @app.route("/users")
    def users():
        return render_template("users.html", users=storage.list_users())

    @app.post("/users/add")
    def users_add():
        raw = request.form.get("tg_id", "").strip()
        if not raw.isdigit():
            flash("Telegram ID — это число. Узнать можно у @userinfobot", "err")
            return redirect(url_for("users"))
        tg_id = int(raw)
        username = request.form.get("username", "").strip().lstrip("@") or None
        if request.form.get("role") == "admin":
            storage.make_admin(tg_id, username)
        else:
            storage.add_user(tg_id, username, role="user")
        storage.log("web_user_added", tg_id=tg_id)
        flash(f"Пользователь {tg_id} добавлен", "ok")
        return redirect(url_for("users"))

    @app.post("/users/<int:tg_id>/<action>")
    def users_action(tg_id: int, action: str):
        try:
            if action == "ban":
                storage.ban_user(tg_id)
            elif action == "unban":
                storage.set_user_status(tg_id, "active")
            elif action == "make_admin":
                storage.make_admin(tg_id)
            elif action == "demote":
                storage.demote_admin(tg_id)
            else:
                abort(404)
        except storage.LastAdminError as exc:
            flash(str(exc), "err")
            return redirect(url_for("users"))
        storage.log("web_user_" + action, tg_id=tg_id)
        flash("Готово", "ok")
        return redirect(url_for("users"))

    # ------------------------------------------------------------ задачи

    @app.route("/tasks")
    def tasks():
        status = request.args.get("status") or None
        if status and status not in TASK_STATUSES:
            abort(400)
        return render_template("tasks.html", tasks=storage.list_tasks(status, limit=200),
                               status=status, statuses=TASK_STATUSES)

    @app.post("/tasks/<int:task_id>/cancel")
    def tasks_cancel(task_id: int):
        if storage.cancel_task(task_id):
            storage.log("web_task_cancelled", task_id=task_id)
            flash(f"Задача #{task_id} снята", "ok")
        else:
            flash("Снять можно только задачу в очереди", "err")
        return _redirect_back("tasks")

    # ------------------------------------------------------------ настройки

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        current = storage.all_settings()
        if request.method == "POST":
            changed = {}
            for key, old in current.items():
                new = request.form.get(key, old).strip()
                if new == old:
                    continue
                if old.lstrip("-").isdigit() and not new.lstrip("-").isdigit():
                    flash(f"{key}: нужно целое число", "err")
                    return redirect(url_for("settings"))
                changed[key] = new
            if changed:
                _backup("settings")
                for key, value in changed.items():
                    storage.set_setting(key, value)
                storage.log("web_settings", detail=", ".join(f"{k}={v}" for k, v in changed.items()))
                flash(f"Сохранено: {', '.join(changed)}", "ok")
            return redirect(url_for("settings"))
        return render_template("settings.html", settings=sorted(current.items()), help=SETTING_HELP)

    # ------------------------------------------------------------ лог

    @app.route("/logs")
    def logs():
        event = request.args.get("event") or None
        return render_template("logs.html", logs=storage.list_logs(event, limit=300), event=event)

    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)-8s %(name)s: %(message)s", level=logging.INFO)
    if not config.WEB_PASSWORD_HASH:
        raise SystemExit("Пароль админки не задан: python -m db.init_db --web-password")
    app = create_app()

    from waitress import serve
    log.info("админка: http://%s:%s", config.WEB_HOST, config.WEB_PORT)
    serve(app, host=config.WEB_HOST, port=config.WEB_PORT, threads=4)


if __name__ == "__main__":
    main()
