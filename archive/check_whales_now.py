import httpx
import pandas as pd
import os

TOP_WALLETS = "data/top_wallets.csv"
DATA_API = "https://data-api.polymarket.com"

def check():
    if not os.path.exists(TOP_WALLETS):
        print("❌ Файл с китами не найден.")
        return

    top_wallets = set(pd.read_csv(TOP_WALLETS)["wallet"].str.lower())
    print(f"📊 Загружено {len(top_wallets)} китов.")
    
    try:
        resp = httpx.get(f"{DATA_API}/trades", params={"limit": 2000}, timeout=15)
        trades = resp.json()
        print(f"📡 Получено {len(trades)} последних сделок с рынка.")
        
        found = []
        for t in trades:
            wallet = (t.get("proxyWallet") or "").lower()
            if wallet in top_wallets:
                found.append(t)
        
        if not found:
            print("😴 В последних 2000 сделках киты не обнаружены. Они просто отдыхают.")
        else:
            print(f"🔎 Найдено {len(found)} сделок от китов:")
            for f in found[:10]:
                price = float(f.get("price", 0))
                size = float(f.get("size", 0)) * price
                print(f" - {f.get('title')[:40]}... | ${size:>7.2f} | {f.get('side')}")
                
    except Exception as e:
        print(f"❌ Ошибка API: {e}")

if __name__ == "__main__":
    check()
