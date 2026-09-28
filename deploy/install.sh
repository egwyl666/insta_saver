#!/usr/bin/env bash
# Подстановка плейсхолдеров и установка юнитов. Запускать из папки проекта.
set -euo pipefail

USER_NAME="${SUDO_USER:-$USER}"
HOME_DIR="$(eval echo "~$USER_NAME")"

for unit in dl_bot.service dl_bot_update.service dl_bot_update.timer; do
    sed -e "s|REPLACE_USER|$USER_NAME|g" -e "s|REPLACE_HOME|$HOME_DIR|g" \
        "deploy/$unit" | sudo tee "/etc/systemd/system/$unit" > /dev/null
    echo "[ok] $unit"
done

sudo systemctl daemon-reload
sudo systemctl enable --now dl_bot.service
sudo systemctl enable --now dl_bot_update.timer

echo
echo "Статус:  systemctl status dl_bot"
echo "Логи:    journalctl -u dl_bot -f"
