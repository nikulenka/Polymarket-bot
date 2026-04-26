import httpx
import pandas as pd
import os
import time

DATA_API  = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

def fetch_trades():
    csv_path = "data/trades_raw.csv"
    if not os.path.exists(csv_path):
        print(f"Файл {csv_path} не найден. Сначала запустите скрипт загрузки рынков.")
        return

    # --- Шаг 1: топ-300 рынков по объёму ---
    df_markets = pd.read_csv(csv_path)
    df_markets["volume_total"] = pd.to_numeric(df_markets["volume_total"], errors="coerce")
    top300 = df_markets.sort_values("volume_total", ascending=False).head(300)
    market_ids = top300["market_id"].tolist()
    print(f"Отобрано рынков для запроса: {len(market_ids)}")

    # --- Шаг 2: получаем conditionId через Gamma API ---
    print("Загружаем conditionId для рынков...")
    market_map = {}  # conditionId -> question
    for i, mid in enumerate(market_ids, 1):
        try:
            resp = httpx.get(f"{GAMMA_API}/markets", params={"id": mid}, timeout=10)
            if resp.status_code == 200:
                for m in resp.json():
                    cid = m.get("conditionId")
                    if cid:
                        market_map[cid] = m.get("question", "")
        except Exception as e:
            print(f"  [!] Рынок {mid}: {e}")
        if i % 10 == 0:
            print(f"  {i}/{len(market_ids)} рынков обработано...")
        time.sleep(0.05)

    print(f"Рынков с conditionId: {len(market_map)}")

    # --- Шаг 3: скачиваем 500 сделок на рынок ---
    print(f"\nЗагружаем сделки (до 500 на рынок)...")
    all_trades = []

    for i, (condition_id, question) in enumerate(market_map.items(), 1):
        try:
            resp = httpx.get(
                f"{DATA_API}/trades",
                params={"market": condition_id, "limit": 500},
                timeout=20,
            )
            if resp.status_code == 200:
                trades = resp.json()
                for t in trades:
                    size  = float(t.get("size", 0))
                    price = float(t.get("price", 0))
                    all_trades.append({
                        "market_id":   condition_id,
                        "market_name": question,
                        "wallet":      t.get("proxyWallet", ""),
                        "side":        t.get("side", ""),
                        "price":       price,
                        "size":        size,
                        "size_usdc":   size * price,
                        "outcome":     t.get("outcome", ""),
                        "timestamp":   t.get("timestamp", 0),
                        "tx_hash":     t.get("transactionHash", ""),
                    })
        except Exception as e:
            print(f"  [!] Ошибка {condition_id[:10]}...: {e}")

        if i % 10 == 0:
            print(f"  [{i}/{len(market_map)}] собрано сделок: {len(all_trades):,}")
        time.sleep(0.1)

    if not all_trades:
        print("Сделок не найдено.")
        return

    # --- Шаг 4: сохраняем ---
    df = pd.DataFrame(all_trades)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)

    os.makedirs("data", exist_ok=True)
    out = "data/trades_with_wallets.csv"
    df.to_csv(out, index=False)

    # --- Шаг 5: статистика ---
    unique_wallets  = df["wallet"].nunique()
    unique_markets  = df["market_id"].nunique()
    total_vol       = df["size_usdc"].sum()
    min_time        = df["datetime"].min()
    max_time        = df["datetime"].max()
    duration_days   = (max_time - min_time).days

    print(f"\n{'='*50}")
    print(f"✓ Файл сохранён: {out}")
    print(f"{'='*50}")
    print(f"  Сделок собрано:        {len(df):>10,}")
    print(f"  Уникальных кошельков:  {unique_wallets:>10,}")
    print(f"  Рынков охвачено:       {unique_markets:>10,}")
    print(f"  Суммарный объём:       ${total_vol:>12,.2f}")
    print(f"  Диапазон: {min_time.strftime('%Y-%m-%d')} -> {max_time.strftime('%Y-%m-%d')} ({duration_days} дн.)")
    print(f"{'='*50}")

    print(f"\nТоп-3 рынка по числу сделок:")
    top3 = df["market_name"].value_counts().head(3)
    for name, cnt in top3.items():
        print(f"  {cnt:>5,} сделок — {name[:70]}")

if __name__ == "__main__":
    fetch_trades()
