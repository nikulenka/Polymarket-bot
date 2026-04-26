from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import OrderArgs, OrderType
import os
from dotenv import load_dotenv

load_dotenv()

def get_client():
    return ClobClient(
        host="https://clob.polymarket.com",
        chain_id=POLYGON,
        key=os.getenv("POLY_PRIVATE_KEY"),
        api_key=os.getenv("POLY_API_KEY"),
        api_secret=os.getenv("POLY_API_SECRET"),
        api_passphrase=os.getenv("POLY_API_PASSPHRASE"),
        signature_type=1  # EOA (Standard Wallet)
    )

def place_bet(token_id: str, side: str, size_usd: float, price: float):
    try:
        client = get_client()
        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
            size=round(size_usd / price, 1),
            side=side,
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)
        return resp
    except Exception as e:
        print(f"  [!] Ошибка выставления ордера: {e}")
        return None

def close_position(token_id: str, size: float, current_price: float):
    try:
        client = get_client()
        order_args = OrderArgs(
            token_id=token_id,
            price=round(current_price, 2),
            size=round(size, 1),
            side="SELL",
        )
        signed_order = client.create_order(order_args)
        return client.post_order(signed_order, OrderType.GTC)
    except Exception as e:
        print(f"  [!] Ошибка закрытия позиции: {e}")
        return None
