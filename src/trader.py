"""
Исполнение сделок на Polymarket CLOB V2.

Поддерживает два режима (CONFIG.trading.paper_mode):
  • PAPER (по умолчанию) — ордера симулируются, реальных денег нет.
    Виртуальный банкролл и филлы хранятся в data/paper_fills.json.
  • LIVE — реальные ордера через py_clob_client_v2 (нужны ключи в .env).
"""

import os
import json
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv

from src.config import CONFIG

load_dotenv()
logger = logging.getLogger("polymarket_bot.trader")

CLOB_API = CONFIG.api.clob_api
MIN_TOKENS = CONFIG.trading.min_tokens


# ============================================================
#  PAPER-режим (симуляция)
# ============================================================

def _load_paper() -> dict:
    path = CONFIG.files.paper_fills_file
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {"balance": CONFIG.trading.paper_start_balance, "fills": []}


def _save_paper(state: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG.files.paper_fills_file) or ".", exist_ok=True)
    with open(CONFIG.files.paper_fills_file, "w") as f:
        json.dump(state, f, indent=2)


def _paper_fill(token_id: str, side: str, size: float, price: float) -> bool:
    state = _load_paper()
    cost = size * price
    if side == "BUY":
        if state["balance"] < cost:
            print(f"  [PAPER] Недостаточно средств: ${state['balance']:.2f} < ${cost:.2f}")
            return False
        state["balance"] -= cost
    else:  # SELL
        state["balance"] += cost
    state["fills"].append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "token_id": token_id, "side": side,
        "size": round(size, 4), "price": round(price, 4),
        "cost": round(cost, 4), "balance_after": round(state["balance"], 4),
    })
    _save_paper(state)
    print(f"  [PAPER] {side} {size:.2f} @ {price:.4f} (${cost:.2f}) → баланс ${state['balance']:.2f}")
    return True


# ============================================================
#  LIVE-режим (реальный CLOB)
# ============================================================

def get_client():
    """Инициализирует клиент CLOB V2 API (только для LIVE)."""
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.constants import POLYGON
    from py_clob_client_v2.clob_types import ApiCreds

    creds = ApiCreds(
        api_key=os.getenv("POLY_API_KEY"),
        api_secret=os.getenv("POLY_API_SECRET"),
        api_passphrase=os.getenv("POLY_API_PASSPHRASE"),
    )
    private_key = os.getenv("POLY_PRIVATE_KEY")
    if private_key and not private_key.startswith("0x"):
        private_key = "0x" + private_key
    proxy_address = os.getenv("POLY_PROXY_ADDRESS")

    return ClobClient(
        CLOB_API, POLYGON, private_key, creds,
        signature_type=2 if proxy_address else None,
        funder=proxy_address,
    )


def _normalize(price: float, amount_usd: float):
    safe_price = round(float(price), 4)
    safe_price = max(0.01, min(0.99, safe_price))
    token_size = float(amount_usd) / safe_price
    if token_size < MIN_TOKENS:
        token_size = MIN_TOKENS
    return safe_price, round(token_size, 4)


# ============================================================
#  Публичный интерфейс
# ============================================================

def place_bet(token_id, side, amount_usd, price):
    """Выставляет ордер (или симулирует в paper-режиме)."""
    safe_price, token_size = _normalize(price, amount_usd)

    if CONFIG.trading.paper_mode:
        return _paper_fill(token_id, side, token_size, safe_price)

    try:
        from py_clob_client_v2.clob_types import OrderArgs
        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=safe_price, size=token_size, side=side)
        print(f"  📋 Ордер: {side} {token_size} токенов @ {safe_price} (${amount_usd:.2f})")
        signed = client.create_order(order_args)
        resp = client.post_order(signed)
        if resp and resp.get("success"):
            print(f"  ✅ Ордер выставлен! ID: {resp.get('orderID')}")
            return True
        print(f"  [!] Ошибка ордера: {resp}")
        return False
    except Exception as e:
        print(f"  [!] Exception в place_bet: {e}")
        return False


def close_position(token_id, size, price):
    """Закрывает позицию SELL-ордером (или симулирует)."""
    safe_price = max(0.005, round(float(price), 4))
    token_size = round(float(size), 4)

    if CONFIG.trading.paper_mode:
        return _paper_fill(token_id, "SELL", token_size, safe_price)

    try:
        from py_clob_client_v2.clob_types import OrderArgs
        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=safe_price, size=token_size, side="SELL")
        print(f"  🔻 Закрываем: SELL {token_size} @ {safe_price}")
        signed = client.create_order(order_args)
        resp = client.post_order(signed)
        if resp and resp.get("success"):
            print(f"  ✅ Позиция закрыта! ID: {resp.get('orderID')}")
            return True
        print(f"  [!] Ошибка закрытия: {resp}")
        return False
    except Exception as e:
        print(f"  [!] Exception в close_position: {e}")
        return False


def get_usdc_balance():
    """Баланс: виртуальный в paper-режиме, реальный pUSD в live."""
    if CONFIG.trading.paper_mode:
        return _load_paper()["balance"]

    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        client = get_client()
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        resp = client.get_balance_allowance(params)
        if resp:
            return float(resp.get("balance", 0)) / 10**6  # pUSD: 6 decimals
    except Exception as e:
        print(f"  [!] Ошибка получения баланса: {e}")
    return 0.0
