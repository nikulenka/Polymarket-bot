#!/usr/bin/env python3
"""
QUICK START: Запуск оптимизированного Polymarket-bot

Этот скрипт показывает как использовать новые QUICK WINS оптимизации.
"""

import sys
import os

# Добавим путь к src
sys.path.insert(0, os.path.dirname(__file__))

print("=" * 70)
print("  🚀 POLYMARKET BOT — QUICK WINS OPTIMIZATION")
print("=" * 70)

# 1. Проверим что все модули загружаются
print("\n1️⃣  Проверка импортов...")
try:
    from src.config import CONFIG
    from src.cache import TelegramBatcher, PriceCacheManager
    from src.logger import setup_logging
    print("   ✅ Все модули успешно загружены")
except ImportError as e:
    print(f"   ❌ Ошибка импорта: {e}")
    sys.exit(1)

# 2. Покажем текущую конфигурацию
print("\n2️⃣  Текущая конфигурация:")
print(f"   • Poll interval: {CONFIG.monitor.poll_interval}s")
print(f"   • Trade amount: ${CONFIG.trading.trade_amount_usd}")
print(f"   • Max price: {CONFIG.trading.max_price} ({CONFIG.trading.max_price*100:.0f}%)")
print(f"   • Position hold: {CONFIG.trading.position_hold_hours}h")
print(f"   • Market filter: {len(CONFIG.market_filter.skip_patterns)} паттернов")
print(f"   • Telegram batch: {CONFIG.telegram.batch_interval_sec}s, max {CONFIG.telegram.max_batch_size} msgs")
print(f"   • Price cache TTL: {CONFIG.cache.price_cache_ttl_sec}s")

# 3. Примеры использования
print("\n3️⃣  Примеры использования:")

# Пример 1: Использование конфигурации
print("\n   📝 Пример 1 — Доступ к параметрам:")
print(f"      CONFIG.monitor.poll_interval = {CONFIG.monitor.poll_interval}")
print(f"      CONFIG.api.data_api = {CONFIG.api.data_api}")

# Пример 2: Фильтр рынков
print("\n   📝 Пример 2 — Фильтр рынков (O(1) вместо O(N)):")
test_markets = [
    "Will Bitcoin reach $100k?",
    "NBA Finals 2024",
    "Trump approval rating", 
    "UFC 305 Main Event"
]
for market in test_markets:
    skip = CONFIG.market_filter.should_skip(market)
    status = "❌ SKIP" if skip else "✅ TRADE"
    print(f"      {status}: {market}")

# Пример 3: Кэширование цен
print("\n   📝 Пример 3 — Кэширование цен (LRU, TTL=30s):")
print(f"      price_cache.get_price(token_id, fetch_fn)")
print(f"      • Первый запрос → идет в API")
print(f"      • Следующие 30s → берут из кэша (-60% API calls)")

# Пример 4: Telegram batching
print("\n   📝 Пример 4 — Batch Telegram (-90% HTTP запросов):")
print(f"      telegram_batcher.add_message('signal')")
print(f"      • Сообщения накапливаются в очереди")
print(f"      • Отправляются раз в {CONFIG.telegram.batch_interval_sec}s")
print(f"      • Или сразу если {CONFIG.telegram.max_batch_size} сообщений")

# 4. Запуск тестов
print("\n4️⃣  Запуск тестов:")
import subprocess
result = subprocess.run([sys.executable, "test_quick_wins.py"], 
                       capture_output=True, text=True)
if result.returncode == 0:
    print("   ✅ Все тесты прошли успешно!")
    print("   🎉 Бот готов к запуску")
else:
    print("   ❌ Некоторые тесты не прошли")
    print(result.stdout)
    sys.exit(1)

# 5. Следующие шаги
print("\n5️⃣  Следующие шаги:")
print("""
   📋 Документация:
      • QUICK_WINS_OPTIMIZATION.md — детальное описание оптимизаций
      • OPTIMIZATION_REPORT.md — полный отчет о результатах
   
   🚀 Запуск бота:
      ./run.sh
   
   🔧 Конфигурация:
      Отредактировать .env файл с нужными параметрами
      (TELEGRAM_TOKEN, OPENROUTER_API_KEY, POLL_INTERVAL и т.д.)
   
   💻 Мониторинг логов:
      tail -f logs/signals.log
      
   🧪 Тестирование:
      python3 test_quick_wins.py
""")

print("\n" + "=" * 70)
print("  ✅ Quick Wins оптимизации готовы к production!")
print("=" * 70)
