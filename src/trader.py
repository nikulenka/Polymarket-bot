from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
import os
from dotenv import load_dotenv

load_dotenv()

MIN_ORDER_SIZE = 0.1  # Минимальный размер ордера в токенах

def get_client():
    creds = ApiCreds(
        api_key=os.getenv("POLY_API_KEY"),
        api_secret=os.getenv("POLY_API_SECRET"),
        api_passphrase=os.getenv("POLY_API_PASSPHRASE"),
    )
    
    key = os.getenv("POLY_PRIVATE_KEY")
    if key and not key.startswith("0x"):
        key = "0x" + key
        
    proxy_address = os.getenv("POLY_PROXY_ADDRESS")
    
    return ClobClient(
        host="https://clob.polymarket.com",
        chain_id=POLYGON,
        key=key,
        creds=creds,
        signature_type=2 if proxy_address else None,
        funder=proxy_address if proxy_address else None
    )

def place_bet(token_id: str, side: str, size_usd: float, price: float):
    """Выставляет ордер на покупку/продажу токена."""
    if price <= 0 or price >= 1:
        print(f"  [!] Некорректная цена: {price} (должна быть 0 < price < 1)")
        return None
    
    # Динамический расчет размера ордера
    # Polymarket CLOB требует минимум 5 токенов для любого ордера
    MIN_TOKENS = 5.0
    token_size = size_usd / price
    
    if token_size < MIN_TOKENS:
        # Увеличиваем сумму до минимально необходимых 5 токенов
        token_size = MIN_TOKENS
        new_size_usd = token_size * price
        print(f"  [i] Сумма увеличена с ${size_usd:.2f} до ${new_size_usd:.2f} (минимум {MIN_TOKENS} токенов)")
        size_usd = new_size_usd
    
    token_size = round(token_size, 4)
    safe_price = max(0.01, min(0.99, round(price, 4)))
    
    if price < 0.01:
        print(f"  [!] Цена {price} ниже минимально допустимой (0.01)")
        return None
    
    print(f"  📋 Ордер: {side} {token_size} токенов @ {safe_price:.4f} (${size_usd:.2f})")
    
    try:
        client = get_client()
        order_args = OrderArgs(
            token_id=token_id,
            price=safe_price,
            size=token_size,
            side=side,
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)
        print(f"  ✅ Ордер отправлен: {resp}")
        return resp
    except Exception as e:
        print(f"  [!] Ошибка выставления ордера: {e}")
        return None

def close_position(token_id: str, size: float, current_price: float):
    """Закрывает позицию (продаёт токены)."""
    if current_price <= 0:
        print(f"  [!] Некорректная цена закрытия: {current_price}")
        return None
    
    token_size = round(size, 4)
    if token_size < MIN_ORDER_SIZE:
        print(f"  [!] Размер на закрытие слишком мал: {token_size}")
        return None
    
    # При закрытии (продаже) тоже ограничиваем цену
    safe_price = min(0.99, round(current_price, 4))
    
    print(f"  📋 Закрытие: SELL {token_size} токенов @ {safe_price:.4f}")
    
    try:
        client = get_client()
        order_args = OrderArgs(
            token_id=token_id,
            price=safe_price,
            size=token_size,
            side="SELL",
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)
        print(f"  ✅ Позиция закрыта: {resp}")
        return resp
    except Exception as e:
        print(f"  [!] Ошибка закрытия позиции: {e}")
        return None
