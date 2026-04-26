# Polymarket Insider Bot 🤖

Бот для отслеживания "умных денег" (Smart Money) на Polymarket. Он анализирует сделки топ-кошельков в реальном времени, находит консенсус и валидирует сигналы с помощью ИИ (Claude 3.5 Haiku via OpenRouter).

## Основные функции
- **Real-time Monitoring**: Следит за сделками на Polymarket через Data API.
- **Smart Money Filtering**: Анализирует только сделки кошельков из заранее подготовленного списка "лучших".
- **Consensus Logic**: Генерирует сигнал только если количество кошельков на одной стороне превышает противоположную в 2 раза.
- **AI Validation**: Проверяет каждый сигнал через Claude 3.5 Haiku на предмет логичности и отсутствия ошибок.
- **Persistent Anti-duplicate**: Запоминает отправленные сигналы в `sent_signals.json`, чтобы не спамить дублями (кулдаун 2 часа).
- **Auto-restart**: Запускается в фоне с автоматическим перезапуском при сбоях.

## Установка

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/your-username/Polymarket-bot.git
   cd Polymarket-bot
   ```

2. Установите зависимости:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Настройте переменные окружения:
   Скопируйте `.env.example` в `.env` и впишите свои ключи:
   ```bash
   cp .env.example .env
   ```

## Использование

### 1. Подготовка списка кошельков
Сначала нужно собрать список прибыльных кошельков (например, через аналитику):
```bash
python3 src/rank_wallets.py  # Если скрипт готов
```
*Примечание: Бот ожидает файл `data/top_wallets.csv` с колонкой `wallet`.*

### 2. Запуск мониторинга
Для запуска бота в фоновом режиме используйте:
```bash
chmod +x run.sh
./run.sh
```

Бот будет писать логи в `logs/monitor.log` и `logs/signals.log`.

## Структура проекта
- `src/monitor.py` — основное ядро бота.
- `run.sh` — скрипт для запуска в фоне.
- `data/` — папка для данных (топ-кошельки, кулдаун).
- `logs/` — папка для логов.

## Лицензия
MIT
