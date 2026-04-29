import os
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.constants import POLYGON
from py_clob_client_v2.clob_types import OrderArgs, OrderType, ApiCreds, BalanceAllowanceParams, AssetType

load_dotenv()

CLOB_API = "https://clob.polymarket.com"

def get_client():
    """Инициализирует клиент для CLOB V2 API."""
    creds = ApiCreds(
        api_key=os.getenv("POLY_API_KEY"),
        api_secret=os.getenv("POLY_API_SECRET"),
        api_passphrase=os.getenv("POLY_API_PASSPHRASE"),
    )
    private_key = os.getenv("POLY_PRIVATE_KEY")
    if private_key and not private_key.startswith("0x"):
        private_key = "0x" + private_key
        
    proxy_address = os.getenv("POLY_PROXY_ADDRESS")
    
    # В V2 SDK используется SignatureTypeV2. POLY_GNOSIS_SAFE = 2
    client = ClobClient(
        CLOB_API, 
        POLYGON, 
        private_key, 
        creds, 
        signature_type=2 if proxy_address else None, 
        funder=proxy_address
    )
    return client

def place_bet(token_id, side, amount_usd, price):
    """
    Выставляет GTC ордер на Polymarket CLOB V2.
    """
    try:
        # Округляем цену до 4 знаков (стандарт CLOB)
        safe_price = round(float(price), 4)
        if safe_price < 0.01: safe_price = 0.01
        if safe_price > 0.99: safe_price = 0.99
        
        # Рассчитываем размер позиции (количество токенов)
        # Polymarket требует минимум 5 токенов для сделки
        MIN_TOKENS = 5.0
        token_size = float(amount_usd) / safe_price
        if token_size < MIN_TOKENS:
            token_size = MIN_TOKENS
            print(f"  [i] Размер увеличен до минимума: {token_size:.4f} токенов")
        
        token_size = round(token_size, 4)
        
        client = get_client()
        
        # Подготовка аргументов для V2 ордера
        order_args = OrderArgs(
            token_id=token_id,
            price=safe_price,
            size=token_size,
            side=side,
        )
        
        print(f"  📋 Ордер: {side} {token_size} токенов @ {safe_price} (${amount_usd:.2f})")
        
        # Создаем и подписываем ордер. V2 SDK автоматически выбирает версию контракта.
        signed_order = client.create_order(order_args)
        
        # Отправляем ордер. 
        # V2 SDK автоматически обрабатывает order_version_mismatch и обновляет версию.
        resp = client.post_order(signed_order)
        
        if resp and resp.get("success"):
            print(f"  ✅ Ордер успешно выставлен! ID: {resp.get('orderID')}")
            return True
        else:
            print(f"  [!] Ошибка выставления ордера: {resp}")
            return False
            
    except Exception as e:
        print(f"  [!] Exception в place_bet: {e}")
        return False

def close_position(token_id, size, price):
    """
    Закрывает позицию (продает токены).
    """
    try:
        # Для закрытия позиции (продажи) выставляем SELL ордер
        safe_price = round(float(price), 4)
        if safe_price < 0.005: safe_price = 0.005
        
        token_size = round(float(size), 4)
        
        client = get_client()
        order_args = OrderArgs(
            token_id=token_id,
            price=safe_price,
            size=token_size,
            side="SELL",
        )
        
        print(f"  🔻 Закрываем позицию: SELL {token_size} токенов @ {safe_price}")
        
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order)
        
        if resp and resp.get("success"):
            print(f"  ✅ Позиция закрыта! ID: {resp.get('orderID')}")
            return True
        else:
            print(f"  [!] Ошибка закрытия позиции: {resp}")
            return False
            
    except Exception as e:
        print(f"  [!] Exception в close_position: {e}")
        return False

def get_usdc_balance():
    """Получает баланс pUSD (новый Cash) на прокси-кошельке."""
    try:
        client = get_client()
        # В CLOB V2 для collateral asset_id должен быть пустым
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        resp = client.get_balance_allowance(params)
        
        if resp:
            raw_balance = float(resp.get("balance", 0))
            return raw_balance / 10**6 # pUSD uses 6 decimals
    except Exception as e:
        print(f"  [!] Ошибка получения баланса: {e}")
    return 0.0
