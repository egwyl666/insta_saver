#!/usr/bin/env bash
# Автозапуск через systemd: бот, веб-админка, еженедельное обновление утилит.
# Можно запускать повторно (например, после git pull) — юниты обновятся,
# сервисы перезапустятся на новом коде.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(id -un)}"
cd "$PROJECT_DIR"

if [ ! -x .venv/bin/python ]; then
    echo "[!] нет .venv в $PROJECT_DIR — сначала установка из README" >&2
    exit 1
fi
if [ ! -f .env ]; then
    echo "[!] нет .env — сначала: .venv/bin/python -m db.init_db --setup" >&2
    exit 1
fi

if ! command -v ffmpeg > /dev/null; then
    # yt-dlp склеивает им раздельные видео/аудио дорожки (рилсы, твиттер)
    sudo apt-get install -y ffmpeg
fi

mkdir -p data/cache
[ -n "${SUDO_USER:-}" ] && chown -R "$USER_NAME" data

# Бот и админка, запущенные руками, будут мешать сервисам: два бота с одним
# токеном дерутся за обновления, а порт админки занят.
if ! systemctl is-active --quiet dl_bot 2>/dev/null \
        && pgrep -u "$USER_NAME" -f "python -m (bot\.main|web\.app)" > /dev/null; then
    echo "[!] бот или админка уже запущены вручную — останавливаю их"
    pkill -u "$USER_NAME" -f "python -m (bot\.main|web\.app)" || true
    sleep 2
fi

for unit in dl_bot.service dl_bot_web.service dl_bot_update.service dl_bot_update.timer; do
    sed -e "s|REPLACE_USER|$USER_NAME|g" -e "s|REPLACE_DIR|$PROJECT_DIR|g" \
        "deploy/$unit" | sudo tee "/etc/systemd/system/$unit" > /dev/null
    echo "[ok] $unit"
done

sudo systemctl daemon-reload
sudo systemctl enable dl_bot.service dl_bot_update.timer
sudo systemctl restart dl_bot.service
sudo systemctl start dl_bot_update.timer
if grep -q "^WEB_PASSWORD_HASH=." .env; then
    sudo systemctl enable dl_bot_web.service
    sudo systemctl restart dl_bot_web.service
    WEB_ON=1
else
    WEB_ON=0
    echo "[i] пароль админки не задан — админка не включена."
    echo "    Задать: .venv/bin/python -m db.init_db --web-password, потом запустить этот скрипт ещё раз"
fi

sleep 3
echo
for svc in dl_bot dl_bot_web; do
    [ "$svc" = dl_bot_web ] && [ "$WEB_ON" = 0 ] && continue
    if systemctl is-active --quiet "$svc"; then
        echo "[ok] $svc работает"
    else
        echo "[!] $svc не запустился. Причина: journalctl -u $svc -n 30"
    fi
done
echo
echo "Статус:  systemctl status dl_bot dl_bot_web"
echo "Логи:    journalctl -u dl_bot -f     (админка: -u dl_bot_web)"
if [ "$WEB_ON" = 1 ]; then
    PORT="$(grep -oP '^WEB_PORT=\K.*' .env || true)"
    echo "Админка: http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT:-9000}"
fi
