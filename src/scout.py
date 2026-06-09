"""
Модуль 1 — Whale Scouter.

Сканирует топ-холдеров ликвидных рынков, считает по каждому кандидату
WinRate / Total PnL / возраст и наполняет БД «бриллиантовыми» кошельками.

Критерии (docs/Requirements Polymarket.md):
  • «Бриллиантовый» кит: WinRate >= 80% И Total PnL >= $100k (при >= N разрешённых позициях)
  • Инсайдер: возраст кошелька < 14 дней, но объём сделок > $10k → [INSIDER-ALERT]

Методология WinRate/PnL (на реальных полях Data API /positions):
  • total_pnl  = Σ (realizedPnl + cashPnl) по позициям
  • volume     = Σ totalBought
  • позиция «решена», если redeemable=True или цена ушла к 0/1 (curPrice<=0.03 / >=0.97)
  • win = решённая позиция с (realizedPnl + cashPnl) > 0
  • winrate = wins / resolved
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List

from src import api, db
from src.config import CONFIG

logger = logging.getLogger("polymarket_bot.scout")


def _score_positions(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Считает PnL, объём, WinRate и число разрешённых позиций из /positions."""
    total_pnl = 0.0
    volume = 0.0
    wins = 0
    resolved = 0

    for p in positions:
        try:
            realized = float(p.get("realizedPnl", 0) or 0)
            cash = float(p.get("cashPnl", 0) or 0)
            bought = float(p.get("totalBought", 0) or 0)
            cur_price = float(p.get("curPrice", 0.5) or 0.5)
        except (TypeError, ValueError):
            continue

        pos_pnl = realized + cash
        total_pnl += pos_pnl
        volume += bought

        decided = bool(p.get("redeemable")) or cur_price >= 0.97 or cur_price <= 0.03
        if decided:
            resolved += 1
            if pos_pnl > 0:
                wins += 1

    winrate = (wins / resolved) if resolved else 0.0
    return {
        "total_pnl": total_pnl,
        "volume": volume,
        "winrate": winrate,
        "resolved_trades": resolved,
    }


def score_wallet(address: str, pseudonym: str = "") -> Dict[str, Any]:
    """Полный скоринг одного кошелька. Возвращает запись для БД (без фильтрации)."""
    positions = api.get_positions(address)
    stats = _score_positions(positions)
    portfolio = api.get_portfolio_value(address)

    # Возраст кошелька (для инсайдерского фильтра)
    age_days = None
    first_ts = api.get_first_trade_ts(address)
    if first_ts:
        age_days = (datetime.now(timezone.utc).timestamp() - first_ts) / 86400.0

    is_insider = bool(
        age_days is not None
        and age_days < CONFIG.scout.insider_max_age_days
        and stats["volume"] > CONFIG.scout.insider_min_volume
    )

    # Скор для ранжирования: нормированный PnL + WinRate + бонус инсайдеру
    pnl_norm = min(stats["total_pnl"] / max(CONFIG.scout.min_total_pnl, 1), 3.0)
    score = pnl_norm * 0.5 + stats["winrate"] * 0.4 + (0.5 if is_insider else 0.0)

    return {
        "address": address.lower(),
        "pseudonym": pseudonym,
        "winrate": stats["winrate"],
        "total_pnl": stats["total_pnl"],
        "portfolio_value": portfolio,
        "resolved_trades": stats["resolved_trades"],
        "is_insider": is_insider,
        "age_days": age_days,
        "score": score,
        "volume": stats["volume"],
    }


def qualifies(w: Dict[str, Any]) -> bool:
    """Проходит ли кошелёк критерии отбора (бриллиант ИЛИ инсайдер)."""
    diamond = (
        w["winrate"] >= CONFIG.scout.min_winrate
        and w["total_pnl"] >= CONFIG.scout.min_total_pnl
        and w["resolved_trades"] >= CONFIG.scout.min_resolved_trades
    )
    return bool(diamond or w["is_insider"])


def collect_candidates() -> Dict[str, str]:
    """Собирает уникальных кандидатов (address -> pseudonym) из топ-холдеров ликвидных рынков."""
    markets = api.get_liquid_markets(
        limit=CONFIG.scout.markets_to_scan,
        min_liquidity=CONFIG.scout.min_market_liquidity,
    )
    logger.info(f"Ликвидных рынков для сканирования: {len(markets)}")

    candidates: Dict[str, str] = {}
    for m in markets:
        cid = m.get("conditionId")
        if not cid:
            continue
        if CONFIG.market_filter.should_skip(m.get("question", "")):
            continue
        holders = api.get_holders(cid, limit=CONFIG.scout.holders_per_market)
        for h in holders:
            addr = (h.get("proxyWallet") or "").lower()
            if addr:
                candidates.setdefault(addr, h.get("pseudonym", ""))
    return candidates


def run_scout() -> int:
    """Полный прогон скаута. Возвращает число добавленных/обновлённых китов."""
    db.init_db()
    print("=" * 60)
    print("  WHALE SCOUTER — поиск умных денег")
    print("=" * 60)

    candidates = collect_candidates()
    print(f"Уникальных кандидатов: {len(candidates)}")

    added = 0
    for i, (addr, pseudonym) in enumerate(candidates.items(), 1):
        try:
            w = score_wallet(addr, pseudonym)
            if qualifies(w):
                db.upsert_whale(w)
                added += 1
                tag = "🥷 INSIDER" if w["is_insider"] else "💎 WHALE"
                age_str = f" age={w['age_days']:.0f}д" if w["age_days"] is not None else ""
                print(
                    f"  {tag} {addr[:8]}… "
                    f"WinRate={w['winrate']*100:.0f}% "
                    f"PnL=${w['total_pnl']:,.0f}{age_str}"
                )
        except Exception as e:
            logger.warning(f"Скоринг {addr[:10]} упал: {e}")
        if i % 25 == 0:
            print(f"  …обработано {i}/{len(candidates)} кандидатов")

    count = db.export_whales_csv()
    print(f"\n✓ Отобрано китов: {added}. Всего в БД: {count}")
    print(f"✓ Экспорт: {CONFIG.files.top_wallets_path}")
    return added


if __name__ == "__main__":
    from src.logger import setup_logging
    setup_logging(log_file=CONFIG.files.monitor_log, json_format=False)
    run_scout()
