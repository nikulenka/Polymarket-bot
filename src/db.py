"""
SQLite-слой — единый источник правды для бота.

Схема основана на docs/Requirements Polymarket.md, расширена полями для
WinRate/PnL-скоринга, инсайдерского фильтра и paper-трейдинга.
"""

import os
import csv
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set

from src.config import CONFIG

logger = logging.getLogger("polymarket_bot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS whales (
    address          TEXT PRIMARY KEY,
    pseudonym        TEXT,
    winrate          REAL DEFAULT 0,
    total_pnl        REAL DEFAULT 0,
    portfolio_value  REAL DEFAULT 0,
    resolved_trades  INTEGER DEFAULT 0,
    is_insider       INTEGER DEFAULT 0,
    age_days         REAL,
    score            REAL DEFAULT 0,
    first_seen       TEXT,
    created_at       TEXT,
    last_active      TEXT,
    last_scored      TEXT
);

CREATE TABLE IF NOT EXISTS tx_history (
    tx_hash       TEXT,
    outcome       TEXT,
    address       TEXT,
    condition_id  TEXT,
    market_title  TEXT,
    event_slug    TEXT,
    side          TEXT,
    amount_usd    REAL,
    price         REAL,
    timestamp     INTEGER,
    PRIMARY KEY (tx_hash, outcome)
);

CREATE INDEX IF NOT EXISTS idx_whales_insider ON whales(is_insider);
CREATE INDEX IF NOT EXISTS idx_tx_address ON tx_history(address);
"""


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(CONFIG.files.db_path) or ".", exist_ok=True)
    con = sqlite3.connect(CONFIG.files.db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    """Создаёт таблицы если их нет."""
    with _conn() as con:
        con.executescript(SCHEMA)
    logger.info(f"DB готова: {CONFIG.files.db_path}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
#  Whales
# ============================================================

def upsert_whale(w: Dict[str, Any]) -> None:
    """Вставляет/обновляет кита. Сохраняет first_seen при обновлении."""
    with _conn() as con:
        existing = con.execute(
            "SELECT first_seen FROM whales WHERE address = ?", (w["address"],)
        ).fetchone()
        first_seen = existing["first_seen"] if existing else _now()
        con.execute(
            """
            INSERT INTO whales
                (address, pseudonym, winrate, total_pnl, portfolio_value,
                 resolved_trades, is_insider, age_days, score,
                 first_seen, created_at, last_active, last_scored)
            VALUES (:address, :pseudonym, :winrate, :total_pnl, :portfolio_value,
                    :resolved_trades, :is_insider, :age_days, :score,
                    :first_seen, :created_at, :last_active, :last_scored)
            ON CONFLICT(address) DO UPDATE SET
                pseudonym=excluded.pseudonym,
                winrate=excluded.winrate,
                total_pnl=excluded.total_pnl,
                portfolio_value=excluded.portfolio_value,
                resolved_trades=excluded.resolved_trades,
                is_insider=excluded.is_insider,
                age_days=excluded.age_days,
                score=excluded.score,
                last_scored=excluded.last_scored
            """,
            {
                "address": w["address"].lower(),
                "pseudonym": w.get("pseudonym", ""),
                "winrate": w.get("winrate", 0),
                "total_pnl": w.get("total_pnl", 0),
                "portfolio_value": w.get("portfolio_value", 0),
                "resolved_trades": w.get("resolved_trades", 0),
                "is_insider": 1 if w.get("is_insider") else 0,
                "age_days": w.get("age_days"),
                "score": w.get("score", 0),
                "first_seen": first_seen,
                "created_at": w.get("created_at", first_seen),
                "last_active": w.get("last_active", _now()),
                "last_scored": _now(),
            },
        )


def get_tracked_addresses() -> Set[str]:
    """Множество адресов всех отслеживаемых китов (lower)."""
    with _conn() as con:
        rows = con.execute("SELECT address FROM whales").fetchall()
    return {r["address"].lower() for r in rows}


def get_whale(address: str) -> Optional[Dict[str, Any]]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM whales WHERE address = ?", (address.lower(),)
        ).fetchone()
    return dict(row) if row else None


def get_all_whales() -> List[Dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM whales ORDER BY is_insider DESC, total_pnl DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def touch_last_active(address: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE whales SET last_active = ? WHERE address = ?",
            (_now(), address.lower()),
        )


def export_whales_csv(path: Optional[str] = None) -> int:
    """Экспорт китов в CSV (обзор/совместимость). Колонка `wallet` для старого кода."""
    path = path or CONFIG.files.top_wallets_path
    whales = get_all_whales()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cols = ["wallet", "pseudonym", "winrate", "total_pnl", "portfolio_value",
            "resolved_trades", "is_insider", "age_days", "score"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for w in whales:
            writer.writerow({
                "wallet": w["address"],
                "pseudonym": w.get("pseudonym", ""),
                "winrate": round(w.get("winrate", 0), 4),
                "total_pnl": round(w.get("total_pnl", 0), 2),
                "portfolio_value": round(w.get("portfolio_value", 0), 2),
                "resolved_trades": w.get("resolved_trades", 0),
                "is_insider": w.get("is_insider", 0),
                "age_days": round(w["age_days"], 1) if w.get("age_days") is not None else "",
                "score": round(w.get("score", 0), 4),
            })
    return len(whales)


# ============================================================
#  tx_history (антидубликаты сигналов/сделок)
# ============================================================

def tx_seen(tx_hash: str, outcome: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM tx_history WHERE tx_hash = ? AND outcome = ?",
            (tx_hash, outcome),
        ).fetchone()
    return row is not None


def record_tx(t: Dict[str, Any]) -> None:
    with _conn() as con:
        con.execute(
            """
            INSERT OR IGNORE INTO tx_history
                (tx_hash, outcome, address, condition_id, market_title,
                 event_slug, side, amount_usd, price, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t.get("tx_hash", ""), t.get("outcome", ""),
                (t.get("address", "") or "").lower(),
                t.get("condition_id", ""), t.get("market_title", ""),
                t.get("event_slug", ""), t.get("side", ""),
                t.get("amount_usd", 0), t.get("price", 0),
                t.get("timestamp", 0),
            ),
        )
