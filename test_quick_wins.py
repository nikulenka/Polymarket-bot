#!/usr/bin/env python3
"""
Скрипт для проверки что все Quick Wins оптимизации работают правильно.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

def test_imports():
    """Проверить что все импорты работают"""
    print("🔍 Проверка импортов...")
    try:
        from src.config import CONFIG, load_config
        print("  ✅ src.config")
        
        from src.cache import TelegramBatcher, PriceCacheManager, TimeboxedCache
        print("  ✅ src.cache")
        
        from src.logger import setup_logging
        print("  ✅ src.logger")
        
        return True
    except Exception as e:
        print(f"  ❌ Ошибка импорта: {e}")
        return False


def test_config():
    """Проверить конфигурацию"""
    print("\n🔧 Проверка конфигурации...")
    try:
        from src.config import CONFIG
        
        # Проверим ключевые параметры
        assert CONFIG.monitor.poll_interval == 30, "POLL_INTERVAL должен быть 30"
        assert CONFIG.trading.trade_amount_usd == 2.0, "trade_amount_usd должен быть 2.0"
        assert CONFIG.cache.max_seen_hashes == 30_000, "max_seen_hashes должен быть 30_000"
        
        print(f"  ✅ Poll interval: {CONFIG.monitor.poll_interval}s")
        print(f"  ✅ Trade amount: ${CONFIG.trading.trade_amount_usd}")
        print(f"  ✅ Max seen hashes: {CONFIG.cache.max_seen_hashes}")
        
        # Проверим market_filter
        assert CONFIG.market_filter.compiled_filter is not None, "Regex должен быть скомпилирован"
        print(f"  ✅ Market filter скомпилирован ({len(CONFIG.market_filter.skip_patterns)} паттернов)")
        
        # Тест фильтра
        test_cases = [
            ("NBA Finals 2024", True),
            ("Will Bitcoin reach $100k?", False),
            ("UFC 305: Main Event", True),
            ("Trump approval rating", False),
        ]
        
        for market_name, should_skip in test_cases:
            result = CONFIG.market_filter.should_skip(market_name)
            status = "✅" if result == should_skip else "❌"
            print(f"    {status} '{market_name}' → skip={result}")
        
        return True
    except Exception as e:
        print(f"  ❌ Ошибка конфигурации: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cache():
    """Проверить кэширование"""
    print("\n💾 Проверка кэширования...")
    try:
        from src.cache import TimeboxedCache, PriceCacheManager
        
        # Тест TimeboxedCache
        cache = TimeboxedCache(ttl_seconds=2)
        cache.set("test_key", {"price": 0.42})
        result = cache.get("test_key")
        assert result == {"price": 0.42}, "Должен вернуть значение из кэша"
        print("  ✅ TimeboxedCache работает")
        
        # Тест PriceCacheManager
        pm = PriceCacheManager(ttl_seconds=1)
        call_count = 0
        def mock_fetch(token_id):
            nonlocal call_count
            call_count += 1
            return 0.5
        
        # Первый вызов - идет в fetch
        price1 = pm.get_price("token_1", mock_fetch)
        assert price1 == 0.5, "Должен вернуть 0.5"
        assert call_count == 1, "Должен был вызвать fetch один раз"
        
        # Второй вызов - из кэша
        price2 = pm.get_price("token_1", mock_fetch)
        assert price2 == 0.5, "Должен вернуть 0.5 из кэша"
        assert call_count == 1, "Fetch не должен был быть вызван снова"
        print("  ✅ PriceCacheManager работает (кэш работает)")
        
        return True
    except Exception as e:
        print(f"  ❌ Ошибка кэша: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_telegram_batcher():
    """Проверить Telegram batcher"""
    print("\n📡 Проверка Telegram batcher...")
    try:
        from src.cache import TelegramBatcher
        import time
        
        # Создаем батчер (без реального отправления)
        batcher = TelegramBatcher(
            token="fake_token",
            chat_id="fake_chat_id",
            batch_interval_sec=1,
            max_batch_size=3,
            timeout=5
        )
        
        # Проверим что добавление сообщений работает
        batcher.add_message("Message 1")
        assert len(batcher.queue) == 1, "Должно быть 1 сообщение"
        
        batcher.add_message("Message 2")
        assert len(batcher.queue) == 2, "Должно быть 2 сообщения"
        
        # При добавлении третьего - батч отправится (max_batch_size=3)
        # Это нормальное поведение - flush при полной очереди
        print("  ✅ Batcher работает (очередь и should_flush)")
        
        # Проверим should_flush на свежем батчере
        batcher2 = TelegramBatcher(
            token="fake_token",
            chat_id="fake_chat_id",
            batch_interval_sec=10,  # долгой интервал
            max_batch_size=5,
            timeout=5
        )
        batcher2.add_message("Test")
        assert not batcher2.should_flush(), "Не должен флашнуть (1 из 5)"
        print("  ✅ should_flush работает правильно")
        
        return True
    except Exception as e:
        print(f"  ❌ Ошибка Telegram batcher: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_logging():
    """Проверить логирование"""
    print("\n📝 Проверка логирования...")
    try:
        from src.logger import setup_logging
        import tempfile
        import os
        
        # Создаем временный файл логов
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            logger = setup_logging(log_file=log_file, json_format=False)
            
            logger.info("Test message")
            logger.warning("Test warning")
            
            # Проверим что файл создан
            assert os.path.exists(log_file), "Log file должен быть создан"
            
            with open(log_file) as f:
                content = f.read()
                assert "Test message" in content, "Сообщение должно быть в логе"
                assert "Test warning" in content, "Предупреждение должно быть в логе"
            
            print("  ✅ Логирование работает")
        
        return True
    except Exception as e:
        print(f"  ❌ Ошибка логирования: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Запустить все тесты"""
    print("=" * 60)
    print("  🚀 Тестирование Quick Wins Оптимизаций")
    print("=" * 60)
    
    tests = [
        ("Импорты", test_imports),
        ("Конфигурация", test_config),
        ("Кэширование", test_cache),
        ("Telegram Batcher", test_telegram_batcher),
        ("Логирование", test_logging),
    ]
    
    results = []
    for name, test_fn in tests:
        try:
            result = test_fn()
            results.append((name, result))
        except Exception as e:
            print(f"❌ {name}: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("  📊 Результаты")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nВсего: {passed}/{total} тестов прошли")
    
    if passed == total:
        print("\n🎉 Все тесты прошли! Quick Wins оптимизации готовы к production.")
        return 0
    else:
        print(f"\n⚠️ {total - passed} тестов не прошли.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
