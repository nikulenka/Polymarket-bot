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
    return ClobClient(
        host="https://clob.polymarket.com",
        chain_id=POLYGON,
        key=os.getenv("POLY_PRIVATE_KEY"),
        creds=creds,
        signature_type=1  # EOA (Standard Wallet)
    )

def place_bet(token_id: str, side: str, size_usd: float, price: float):
    """Выставляет ордер на покупку/продажу токена."""
    if price <= 0 or price >= 1:
        print(f"  [!] Некорректная цена: {price} (должна быть 0 < price < 1)")
        return None
    
    token_size = round(size_usd / price, 1)
    if token_size < MIN_ORDER_SIZE:
        print(f"  [!] Размер ордера слишком мал: {token_size} токенов")
        return None
    
    print(f"  📋 Ордер: {side} {token_size} токенов @ {price:.4f} (${size_usd:.2f})")
    
    try:
        client = get_client()
        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
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
    
    token_size = round(size, 1)
    if token_size < MIN_ORDER_SIZE:
        print(f"  [!] Размер на закрытие слишком мал: {token_size}")
        return None
    
    print(f"  📋 Закрытие: SELL {token_size} токенов @ {current_price:.4f}")
    
    try:
        client = get_client()
        order_args = OrderArgs(
            token_id=token_id,
            price=round(current_price, 2),
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
