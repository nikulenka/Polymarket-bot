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
        host="https://clob-v2.polymarket.com",
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
    """Закрывает позицию (продаёт токены) с проверкой баланса."""
    if current_price <= 0:
        print(f"  [!] Некорректная цена закрытия: {current_price}")
        return None
    
    try:
        client = get_client()
        
        # Проверка реального баланса перед продажей
        balance_info = client.get_balance(token_id)
        # balance_info обычно имеет вид {'balance': '5000000', ...}
        raw_balance = int(balance_info.get("balance", 0))
        real_balance = raw_balance / 10**6 # Polymarket использует 6 знаков
        
        print(f"  [i] Проверка баланса: у нас {real_balance} токенов, хотим продать {size}")
        
        if real_balance < 0.1: # Если меньше 0.1 токена, считаем что позиции нет
            print(f"  [!] Ошибка: на балансе нет токенов {token_id}. Пропускаем закрытие.")
            return True # Возвращаем True, чтобы бот удалил эту "фантомную" позицию из памяти
            
        token_size = min(real_balance, round(size, 4))
        if token_size < 5.0: # Минимальный размер ордера на продажу тоже 5 токенов
            print(f"  [!] Баланс {token_size} меньше минимального ордера (5). Ждем или увеличиваем.")
            # Если у нас меньше 5 токенов, мы не сможем их продать через CLOB.
            # В этом случае либо ждать закрытия рынка, либо оставить как есть.
            return False

        safe_price = min(0.99, round(current_price, 4))
        print(f"  📋 Закрытие: SELL {token_size} токенов @ {safe_price:.4f}")
        
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

def get_usdc_balance():
    """Получает баланс pUSD (новый Cash) на прокси-кошельке."""
    # Адрес pUSD на Polygon
    PUSD = "0x23a1aB2F10B247a3e35A22e036e52E3E852D4203"
    try:
        client = get_client()
        # В этой версии SDK используем get_balance_allowance
        resp = client.get_balance_allowance(asset_type="collateral", token_id=PUSD)
        # Ответ: {"balance": "1500000", "allowance": "..."}
        raw_balance = int(resp.get("balance", 0))
        return raw_balance / 10**6
    except Exception as e:
        print(f"  [!] Ошибка получения баланса pUSD: {e}")
        # Попробуем старый USDC.e если pUSD не сработал
        try:
            USDCE = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            client = get_client()
            resp = client.get_balance_allowance(asset_type="collateral", token_id=USDCE)
            raw_balance = int(resp.get("balance", 0))
            return raw_balance / 10**6
        except:
            return 0.0
