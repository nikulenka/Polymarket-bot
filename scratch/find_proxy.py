import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds

def find():
    load_dotenv()
    
    pk = os.getenv("POLY_PRIVATE_KEY")
    api_key = os.getenv("POLY_API_KEY")
    api_secret = os.getenv("POLY_API_SECRET")
    api_passphrase = os.getenv("POLY_API_PASSPHRASE")
    
    if not pk:
        print("❌ Ошибка: POLY_PRIVATE_KEY не найден в .env")
        return

    if not pk.startswith("0x"):
        pk = "0x" + pk

    try:
        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )
        client = ClobClient(
            "https://clob.polymarket.com",
            key=pk,
            chain_id=POLYGON,
            creds=creds
        )
        
        eoa_address = client.get_address()
        print(f"✅ Основной кошелек (EOA): {eoa_address}")
        
        # Пытаемся получить прокси-адрес через API
        # В некоторых версиях библиотеки это client.get_proxy_address()
        # В других — через запрос к профилю.
        try:
            proxy = client.get_proxy_address()
            if proxy:
                print(f"🚀 Найден Прокси-кошелек: {proxy}")
                return proxy
            else:
                print("⚠️ Прокси-кошелек не найден для этого аккаунта.")
        except Exception as e:
            print(f"⚠️ Не удалось получить прокси через прямой метод: {e}")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    return None

if __name__ == "__main__":
    find()
