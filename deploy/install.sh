#!/usr/bin/env bash
# Подстановка плейсхолдеров и установка юнитов. Запускать из папки проекта.
set -euo pipefail

USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(eval echo "~$USER_NAME")"

if ! command -v ffmpeg > /dev/null; then
    # yt-dlp склеивает им раздельные видео/аудио дорожки (рилсы, твиттер)
    sudo apt-get install -y ffmpeg
fi

for unit in dl_bot.service dl_bot_web.service dl_bot_update.service dl_bot_update.timer; do
    sed -e "s|REPLACE_USER|$USER_NAME|g" -e "s|REPLACE_HOME|$HOME_DIR|g" \
        "deploy/$unit" | sudo tee "/etc/systemd/system/$unit" > /dev/null
    echo "[ok] $unit"
done

sudo systemctl daemon-reload
sudo systemctl enable --now dl_bot.service
sudo systemctl enable --now dl_bot_update.timer
if grep -q "^WEB_PASSWORD_HASH=." .env 2>/dev/null; then
    sudo systemctl enable --now dl_bot_web.service
else
    echo "[i] пароль админки не задан — dl_bot_web не включён (.venv/bin/python -m db.init_db --web-password)"
fi

echo
echo "Статус:  systemctl status dl_bot"
echo "Логи:    journalctl -u dl_bot -f"
echo "Админка: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$(grep -oP '^WEB_PORT=\K.*' .env 2>/dev/null || echo 9000)"
