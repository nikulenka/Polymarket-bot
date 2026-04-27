import httpx, pandas as pd
proxies = set(pd.read_csv('data/top_wallets.csv')['wallet'].str.lower())
try:
    trades = httpx.get('https://data-api.polymarket.com/trades', params={'limit': 10000}).json()
    found_whales = 0
    for t in trades:
        p = (t.get('proxyWallet') or '').lower()
        if p in proxies:
            size = float(t.get('size', 0)) * float(t.get('price', 0))
            print(f"✅ Вижу кита! {p}, Сумма: ${size:.2f}")
            found_whales += 1
    print(f"Всего сделок китов >$100 за 10000 транзакций: {found_whales}")
except Exception as e:
    print(f"Ошибка: {e}")
