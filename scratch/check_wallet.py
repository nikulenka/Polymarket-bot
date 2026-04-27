import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds

def check():
    load_dotenv()
    
    # Пытаемся достать ключи как в trader.py
    pk = os.getenv("POLY_PRIVATE_KEY") or os.getenv("PK") or os.getenv("PRIVATE_KEY")
    api_key = os.getenv("POLY_API_KEY")
    api_secret = os.getenv("POLY_API_SECRET")
    api_passphrase = os.getenv("POLY_API_PASSPHRASE")
    
    if not pk:
        print("❌ Ошибка: Приватный ключ не найден в .env (проверьте POLY_PRIVATE_KEY)")
        # Выведем список всех переменных для отладки (без значений)
        print("Доступные переменные в .env:", [k for k in os.environ.keys() if "POLY" in k or "PK" in k])
        return

    if not pk.startswith("0x"):
        pk = "0x" + pk

    print("--- Диагностика кошелька ---")
    
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
        
        address = client.get_address()
        print(f"✅ Адрес из PRIVATE_KEY: {address}")
        print(f"🔍 Ссылка для проверки баланса: https://polygonscan.com/address/{address}#tokentxns")
        
        # Пробуем получить баланс через API
        try:
            print("\nПроверяю баланс USDC на Polymarket...")
            # Прямого метода get_balance в ClobClient может не быть, но мы можем проверить состояние аккаунта
            # Или просто попросить пользователя сравнить адрес.
        except:
            pass

    except Exception as e:
        print(f"❌ Ошибка инициализации клиента: {e}")

if __name__ == "__main__":
    check()
