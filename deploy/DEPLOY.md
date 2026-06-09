# Деплой на сервер (Ubuntu/Debian)

Боту нужен только исходящий HTTPS — подойдёт самый дешёвый VPS
(1 vCPU, 512 МБ–1 ГБ RAM). Открытые порты, домен, Docker не требуются.

## 1. Код на сервер

```bash
# локально (один раз): приватный репозиторий
gh repo create polymarket-bot --private --source . --push

# на сервере
git clone git@github.com:<аккаунт>/polymarket-bot.git ~/polymarket-bot
cd ~/polymarket-bot
```

`.env` в git не попадает — скопировать отдельно:

```bash
# локально
scp .env user@server:~/polymarket-bot/.env
```

`data/polymarket.db` копировать не обязательно — скаут соберёт китов заново.

## 2. Окружение

```bash
sudo apt update && sudo apt install -y python3-venv
cd ~/polymarket-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# первый сбор китов + проверка, что API и Telegram работают
PYTHONPATH=. .venv/bin/python3 -m src.scout
```

## 3. systemd-сервисы

```bash
sudo bash deploy/install.sh
```

Скрипт сам подставит пути/пользователя, проверит venv и `.env`
(предупредит, если `PAPER_MODE=false`!) и включит:

- `polymarket-tracker.service` — трекер 24/7, авторестарт при падении
  и после ребута сервера;
- `polymarket-scout.timer` — пересбор китов ежедневно в 03:00 UTC.

Перезапуск трекера после скаута не нужен: он перечитывает БД китов
каждые 10 минут.

## 4. Эксплуатация

```bash
journalctl -u polymarket-tracker -f          # живые логи
systemctl status polymarket-tracker          # статус
sudo systemctl restart polymarket-tracker    # перезапуск (после git pull)
sudo systemctl start polymarket-scout        # скаут вручную
systemctl list-timers polymarket-scout.timer # когда следующий прогон скаута
```

Обновление кода:

```bash
cd ~/polymarket-bot && git pull && sudo systemctl restart polymarket-tracker
```

## Безопасность

- **`PAPER_MODE=true`** — пока идёт набор статистики (Фаза 4), не меняй.
- Пока работаешь в paper-режиме, `POLY_PRIVATE_KEY` на сервер можно
  вообще не копировать — он нужен только для LIVE.
- `install.sh` ставит на `.env` права `600` (читает только владелец).
- Стандартная гигиена VPS: вход по SSH-ключу, `sudo apt install
  unattended-upgrades`, fail2ban по желанию.

## Локальный запуск (macOS) — без изменений

На маке всё работает как раньше: `./run.sh` + cron на `update_daily.sh`.
Папка `deploy/` — только для Linux-сервера.
