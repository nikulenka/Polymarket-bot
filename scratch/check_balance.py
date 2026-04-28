import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds

load_dotenv()

def check():
    creds = ApiCreds(
        api_key=os.getenv("POLY_API_KEY"),
        api_secret=os.getenv("POLY_API_SECRET"),
        api_passphrase=os.getenv("POLY_API_PASSPHRASE"),
    )
    key = os.getenv("POLY_PRIVATE_KEY")
    proxy = os.getenv("POLY_PROXY_ADDRESS")
    
    client = ClobClient("https://clob.polymarket.com", POLYGON, key, creds, signature_type=2, funder=proxy)
    
    print(f"Checking balances for Proxy: {proxy}")
    # Мы не знаем все token_id, но можем попробовать получить информацию о портфеле через API, если оно поддерживает
    # Или просто проверить конкретный токен из лога, если мы его найдем.
    # Но проще всего посмотреть, что в cached_positions.json
    
    import json
    if os.path.exists("data/positions.json"):
        with open("data/positions.json", "r") as f:
            pos = json.load(f)
            for tid, data in pos.items():
                print(f"\nChecking token: {tid} ({data.get('market')})")
                # К сожалению, в SDK нет прямого метода get_balance(token_id)
                # Но мы можем попробовать создать ордер на продажу 0.0001 и посмотреть ошибку или успех
                print(f"Expected tokens in bot memory: {data.get('tokens')}")

check()
