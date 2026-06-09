#!/bin/bash
# Установка Polymarket-бота как systemd-сервисов (Linux-сервер).
# Запускать на сервере ИЗ КОРНЯ ПРОЕКТА:
#   sudo bash deploy/install.sh
#
# Скрипт подставляет реальные пути/пользователя в юниты, кладёт их
# в /etc/systemd/system и включает автозапуск.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Нужен root: sudo bash deploy/install.sh" >&2
    exit 1
fi

BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BOT_USER="${SUDO_USER:-root}"

echo "Проект:      $BOT_DIR"
echo "Пользователь: $BOT_USER"

# --- Проверки окружения ---
if [ ! -x "$BOT_DIR/.venv/bin/python3" ]; then
    echo "❌ Нет venv. Сначала:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi
if [ ! -f "$BOT_DIR/.env" ]; then
    echo "❌ Нет .env (Telegram-токены и настройки). Скопируй его на сервер: scp .env user@server:$BOT_DIR/" >&2
    exit 1
fi
chmod 600 "$BOT_DIR/.env"

PAPER=$(grep -E "^PAPER_MODE" "$BOT_DIR/.env" | tail -1 | cut -d= -f2 | tr -d ' ' || true)
if [ "${PAPER,,}" = "false" ]; then
    echo "⚠️  ВНИМАНИЕ: в .env PAPER_MODE=false — бот будет отправлять РЕАЛЬНЫЕ ордера!"
    read -r -p "Продолжить? [y/N] " ans
    [ "${ans,,}" = "y" ] || exit 1
fi

# --- Установка юнитов ---
for f in polymarket-tracker.service polymarket-scout.service polymarket-scout.timer; do
    sed -e "s|__BOT_DIR__|$BOT_DIR|g" -e "s|__BOT_USER__|$BOT_USER|g" \
        "$BOT_DIR/deploy/$f" > "/etc/systemd/system/$f"
    echo "  установлен /etc/systemd/system/$f"
done

systemctl daemon-reload
systemctl enable --now polymarket-tracker.service polymarket-scout.timer

echo
echo "✅ Готово."
echo "   Статус трекера:   systemctl status polymarket-tracker"
echo "   Живые логи:       journalctl -u polymarket-tracker -f"
echo "   Таймер скаута:    systemctl list-timers polymarket-scout.timer"
echo "   Скаут вручную:    sudo systemctl start polymarket-scout.service"
