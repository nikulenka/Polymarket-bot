"""Прямая диагностика: почему top_wallets не совпадают с proxyWallet в ленте."""
import httpx, pandas as pd

# 1. Загружаем наш список китов (это proxyWallet из исторических данных)
wallets_df = pd.read_csv("data/top_wallets.csv")
our_wallets = set(wallets_df["wallet"].str.lower().str.strip())
print(f"Наших китов в базе: {len(our_wallets)}")
print(f"Пример из нашей базы: {list(our_wallets)[:3]}")

# 2. Загружаем текущую ленту сделок
trades = httpx.get("https://data-api.polymarket.com/trades", params={"limit": 5000}, timeout=30).json()
api_wallets = set()
for t in trades:
    pw = (t.get("proxyWallet") or "").lower().strip()
    if pw:
        api_wallets.add(pw)

print(f"\nУникальных proxyWallet в ленте (5000 сделок): {len(api_wallets)}")
print(f"Пример из API: {list(api_wallets)[:3]}")

# 3. Пересечение
overlap = our_wallets & api_wallets
print(f"\nПересечение: {len(overlap)} адресов")
if overlap:
    print(f"Примеры: {list(overlap)[:5]}")

# 4. Проверяем формат — может разная длина или регистр?
sample_ours = list(our_wallets)[0]
sample_api = list(api_wallets)[0]
print(f"\nФормат наших: len={len(sample_ours)}, starts_0x={sample_ours.startswith('0x')}")
print(f"Формат API:   len={len(sample_api)}, starts_0x={sample_api.startswith('0x')}")

# 5. А теперь проверим — есть ли наши киты вообще СРЕДИ АКТИВНЫХ за последние сутки?
# Для этого воспользуемся эндпоинтом /trades с параметром user (из документации)
test_addr = wallets_df["wallet"].iloc[0]
print(f"\nТестируем адрес: {test_addr}")
r = httpx.get("https://data-api.polymarket.com/trades", params={"user": test_addr, "limit": 3}, timeout=10)
user_trades = r.json()
print(f"Сделок через ?user=: {len(user_trades)}")
if user_trades:
    print(f"  proxyWallet: {user_trades[0].get('proxyWallet')}")
    print(f"  timestamp: {user_trades[0].get('timestamp')}")
    print(f"  title: {user_trades[0].get('title')}")
