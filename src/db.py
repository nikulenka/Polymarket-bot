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
    lifetime_pnl     REAL DEFAULT 0,
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

-- Исходы сигналов: был ли кит прав. Основа авточистки плохих китов
-- и оценки качества стратегии (Фаза 2).
CREATE TABLE IF NOT EXISTS signal_outcomes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cond_id        TEXT,
    market_title   TEXT,
    side           TEXT,            -- BUY / SELL (направление сигнала)
    outcome        TEXT,            -- consensus_outcome сигнала (lower)
    entry_price    REAL,            -- цена на момент сигнала
    signal_type    TEXT,            -- trusted_whale / consensus
    wallets        TEXT,            -- адреса на стороне сигнала, через запятую
    created_at     TEXT,
    resolved_at    TEXT,            -- NULL пока рынок не разрешён
    winner         TEXT,            -- выигравший outcome (lower)
    won            INTEGER,         -- 1 = кит был прав, 0 = нет, NULL = не разрешён
    -- Патч G: справедливая ротация очереди сверки. Раньше выборка была
    -- ORDER BY created_at LIMIT 100 — очередь вечно жевала 100 старейших
    -- неразрешаемых сигналов и не доходила до свежих (0 сверок за 2 недели).
    last_checked   TEXT,             -- когда последний раз спрашивали Gamma
    check_attempts INTEGER DEFAULT 0,
    gave_up        INTEGER DEFAULT 0 -- 1 = безнадёжен (нет cond_id / слишком стар)
);

CREATE INDEX IF NOT EXISTS idx_whales_insider ON whales(is_insider);
CREATE INDEX IF NOT EXISTS idx_tx_address ON tx_history(address);
CREATE INDEX IF NOT EXISTS idx_outcomes_unresolved ON signal_outcomes(resolved_at) WHERE resolved_at IS NULL;
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
    """Создаёт таблицы если их нет + миграции существующей БД."""
    migrations = [
        # Фаза 2
        "ALTER TABLE whales ADD COLUMN lifetime_pnl REAL DEFAULT 0",
        # Патч G: ротация очереди сверки исходов
        "ALTER TABLE signal_outcomes ADD COLUMN last_checked TEXT",
        "ALTER TABLE signal_outcomes ADD COLUMN check_attempts INTEGER DEFAULT 0",
        "ALTER TABLE signal_outcomes ADD COLUMN gave_up INTEGER DEFAULT 0",
    ]
    with _conn() as con:
        con.executescript(SCHEMA)
        for sql in migrations:
            try:
                con.execute(sql)
            except sqlite3.OperationalError:
                pass  # колонка уже есть
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
                 resolved_trades, is_insider, age_days, score, lifetime_pnl,
                 first_seen, created_at, last_active, last_scored)
            VALUES (:address, :pseudonym, :winrate, :total_pnl, :portfolio_value,
                    :resolved_trades, :is_insider, :age_days, :score, :lifetime_pnl,
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
                lifetime_pnl=excluded.lifetime_pnl,
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
                "lifetime_pnl": w.get("lifetime_pnl", 0),
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


def remove_unqualified_whales(min_winrate: float, min_pnl: float,
                              insider_min_winrate: float,
                              insider_min_pnl: float = 0.0,
                              lb_min_pnl: float = float("inf"),
                              lb_min_winrate: float = 0.0,
                              lb_min_resolved_for_check: int = 0) -> int:
    """
    Удаляет из БД китов, которые больше не соответствуют критериям.
    Вызывается в конце каждого прогона скаута после изменения порогов.
    Условия зеркалят scout.qualifies(): бриллиант ИЛИ инсайдер ИЛИ leaderboard-кит.
    """
    with _conn() as con:
        result = con.execute(
            """
            DELETE FROM whales
            WHERE NOT (
                (winrate >= ? AND total_pnl >= ?) OR
                (is_insider = 1 AND winrate >= ? AND total_pnl > ?) OR
                (lifetime_pnl >= ? AND (resolved_trades < ? OR winrate >= ?))
            )
            """,
            (min_winrate, min_pnl, insider_min_winrate, insider_min_pnl,
             lb_min_pnl, lb_min_resolved_for_check, lb_min_winrate),
        )
        removed = result.rowcount
    if removed:
        logger.info(f"Удалено {removed} китов, не прошедших текущие критерии.")
    return removed


def get_elite_addresses(min_winrate: float, min_pnl: float,
                        lb_min_pnl: float) -> Set[str]:
    """
    «Элита» — киты, которым доверяем одиночный сигнал без консенсуса:
    высокий WinRate + PnL, инсайдеры, либо подтверждённый lifetime PnL с leaderboard.
    """
    with _conn() as con:
        rows = con.execute(
            """
            SELECT address FROM whales
            WHERE (winrate >= ? AND (total_pnl >= ? OR lifetime_pnl >= ?))
               OR is_insider = 1
               OR lifetime_pnl >= ?
            """,
            (min_winrate, min_pnl, min_pnl, lb_min_pnl),
        ).fetchall()
    return {r["address"].lower() for r in rows}


def export_whales_csv(path: Optional[str] = None) -> int:
    """Экспорт китов в CSV (обзор/совместимость). Колонка `wallet` для старого кода."""
    path = path or CONFIG.files.top_wallets_path
    whales = get_all_whales()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cols = ["wallet", "pseudonym", "winrate", "total_pnl", "lifetime_pnl",
            "portfolio_value", "resolved_trades", "is_insider", "age_days", "score"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for w in whales:
            writer.writerow({
                "wallet": w["address"],
                "pseudonym": w.get("pseudonym", ""),
                "winrate": round(w.get("winrate", 0), 4),
                "total_pnl": round(w.get("total_pnl", 0), 2),
                "lifetime_pnl": round(w.get("lifetime_pnl", 0) or 0, 2),
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


# ============================================================
#  signal_outcomes — был ли кит прав (петля обратной связи, Фаза 2)
# ============================================================

def record_signal_outcome(signal: Dict[str, Any], wallets: List[str],
                          entry_price: float) -> int:
    """
    Фиксирует сигнал для последующей сверки с разрешением рынка.
    Возвращает id записи (autoincrement, персистентный) — используется как
    номер сигнала в алерте, чтобы он не сбрасывался при рестарте процесса
    (в отличие от счётчика в памяти).
    """
    with _conn() as con:
        cur = con.execute(
            """
            INSERT INTO signal_outcomes
                (cond_id, market_title, side, outcome, entry_price,
                 signal_type, wallets, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.get("cond_id", ""),
                signal.get("market", ""),
                signal.get("side", ""),
                (signal.get("consensus_outcome") or "").lower(),
                entry_price,
                signal.get("signal_type", ""),
                ",".join(sorted(set(w.lower() for w in wallets))),
                _now(),
            ),
        )
        return cur.lastrowid


def get_unresolved_outcomes(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Патч G: очередь сверки с честной ротацией — сначала давно не проверенные
    (непроверенные вообще — первыми, среди них свежие вперёд), безнадёжные
    (gave_up) не занимают место. Раньше `ORDER BY created_at LIMIT N` вечно
    возвращал одни и те же N старейших неразрешаемых → петля голодала.
    """
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM signal_outcomes "
            "WHERE resolved_at IS NULL AND COALESCE(gave_up, 0) = 0 "
            "ORDER BY COALESCE(last_checked, '') ASC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_outcome_resolved(outcome_id: int, winner: str, won: bool) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE signal_outcomes SET resolved_at = ?, winner = ?, won = ? WHERE id = ?",
            (_now(), winner, 1 if won else 0, outcome_id),
        )


def touch_outcomes_checked(ids: List[int]) -> None:
    """Отметить, что сигналы только что сверялись (для ротации очереди)."""
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    with _conn() as con:
        con.execute(
            f"UPDATE signal_outcomes SET last_checked = ?, "
            f"check_attempts = COALESCE(check_attempts, 0) + 1 WHERE id IN ({marks})",
            [_now(), *ids],
        )


def mark_outcomes_gave_up(ids: List[int]) -> None:
    """Пометить сигналы безнадёжными — рынок так и не разрешился за отведённый срок."""
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    with _conn() as con:
        con.execute(
            f"UPDATE signal_outcomes SET gave_up = 1 WHERE id IN ({marks})",
            ids,
        )


def whale_signal_stats() -> Dict[str, Dict[str, int]]:
    """
    Атрибуция по китам: сколько разрешённых сигналов с участием кошелька
    и сколько из них оказались верными. {address: {"resolved": n, "wins": k}}
    """
    stats: Dict[str, Dict[str, int]] = {}
    with _conn() as con:
        rows = con.execute(
            "SELECT wallets, won FROM signal_outcomes WHERE resolved_at IS NOT NULL"
        ).fetchall()
    for r in rows:
        for addr in (r["wallets"] or "").split(","):
            addr = addr.strip().lower()
            if not addr:
                continue
            s = stats.setdefault(addr, {"resolved": 0, "wins": 0})
            s["resolved"] += 1
            s["wins"] += int(r["won"] or 0)
    return stats


def signal_outcome_summary() -> Dict[str, int]:
    """Сводка по сигналам для ежедневного отчёта."""
    with _conn() as con:
        total = con.execute(
            "SELECT COUNT(*) c FROM signal_outcomes").fetchone()["c"]
        resolved = con.execute(
            "SELECT COUNT(*) c FROM signal_outcomes WHERE resolved_at IS NOT NULL").fetchone()["c"]
        wins = con.execute(
            "SELECT COUNT(*) c FROM signal_outcomes WHERE won = 1").fetchone()["c"]
    return {"total": total, "resolved": resolved, "wins": wins}


def _entry_bucket(price) -> str:
    if price is None:
        return "?"
    if price < 0.3:
        return "<0.3"
    if price < 0.5:
        return "0.3-0.5"
    if price < 0.7:
        return "0.5-0.7"
    if price < 0.9:
        return "0.7-0.9"
    return ">=0.9"


def signal_outcome_breakdown() -> Dict[str, Dict[str, Dict[str, int]]]:
    """
    Патч E: разбивка правоты по стороне (BUY/SELL), типу сигнала и корзине цены
    входа — чтобы калибровка видела, где край теряется (SELL, зона фаворитов).
    Возвращает {"by_side": {...}, "by_type": {...}, "by_bucket": {...}},
    где значение = {"resolved": n, "wins": k}.
    """
    out = {"by_side": {}, "by_type": {}, "by_bucket": {}}
    with _conn() as con:
        rows = con.execute(
            "SELECT side, signal_type, entry_price, won FROM signal_outcomes "
            "WHERE resolved_at IS NOT NULL"
        ).fetchall()
    for r in rows:
        won = int(r["won"] or 0)
        for dim, key in (
            ("by_side", (r["side"] or "?").upper()),
            ("by_type", r["signal_type"] or "?"),
            ("by_bucket", _entry_bucket(r["entry_price"])),
        ):
            s = out[dim].setdefault(key, {"resolved": 0, "wins": 0})
            s["resolved"] += 1
            s["wins"] += won
    return out


def prune_bad_performers(min_signals: int, min_winshare: float) -> List[str]:
    """
    Удаляет китов, чьи скопированные сигналы статистически убыточны:
    >= min_signals разрешённых сигналов и доля правоты < min_winshare.
    История в signal_outcomes сохраняется. Возвращает удалённые адреса.
    """
    stats = whale_signal_stats()
    bad = [
        addr for addr, s in stats.items()
        if s["resolved"] >= min_signals
        and (s["wins"] / s["resolved"]) < min_winshare
    ]
    if not bad:
        return []
    with _conn() as con:
        removed = []
        for addr in bad:
            r = con.execute("DELETE FROM whales WHERE address = ?", (addr,))
            if r.rowcount:
                removed.append(addr)
    if removed:
        logger.info(f"Авточистка по исходам сигналов: удалено {len(removed)} китов: "
                    + ", ".join(a[:10] + "…" for a in removed))
    return removed


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
