"""
Mock-тесты (без обращения к блокчейну/API), как рекомендовано в требованиях.
Запуск:  PYTHONPATH=. python3 -m unittest tests.test_bot -v
"""

import os
import tempfile
import unittest

from src.config import CONFIG

# ВАЖНО: отключаем Telegram ДО импорта модулей, создающих Notifier
# (src.tracker создаёт его на уровне модуля). Иначе тесты, проходящие
# через notifier.send() (дневной стоп-лосс и т.п.), шлют РЕАЛЬНЫЕ
# сообщения в чат с токеном из локального .env.
CONFIG.telegram.enabled = False
CONFIG.telegram.token = None
CONFIG.telegram.chat_id = None

from src import api, db, scout, engine, notifier


class TestNotional(unittest.TestCase):
    def test_usdc_notional_is_size_times_price(self):
        # size = шеры, price = цена; notional = их произведение
        t = {"size": 36.47, "price": 0.22}
        self.assertAlmostEqual(api.usdc_notional(t), 8.0234, places=3)

    def test_notional_handles_garbage(self):
        self.assertEqual(api.usdc_notional({"size": "x", "price": None}), 0.0)


class TestScoutScoring(unittest.TestCase):
    def test_score_positions_winrate_and_pnl(self):
        positions = [
            {"realizedPnl": 100, "cashPnl": 0, "totalBought": 500, "curPrice": 0.99, "redeemable": True},   # win, decided
            {"realizedPnl": -50, "cashPnl": 0, "totalBought": 200, "curPrice": 0.01, "redeemable": True},   # loss, decided
            {"realizedPnl": 0, "cashPnl": 80, "totalBought": 300, "curPrice": 0.6, "redeemable": False},    # open, not decided
        ]
        s = scout._score_positions(positions)
        self.assertEqual(s["resolved_trades"], 2)
        self.assertAlmostEqual(s["winrate"], 0.5)
        self.assertAlmostEqual(s["total_pnl"], 130.0)  # 100 -50 +80
        self.assertAlmostEqual(s["volume"], 1000.0)

    def test_qualifies_diamond(self):
        w = {"winrate": 0.9, "total_pnl": 150_000, "resolved_trades": 10, "is_insider": False}
        self.assertTrue(scout.qualifies(w))

    def test_qualifies_rejects_low_winrate(self):
        w = {"winrate": 0.5, "total_pnl": 150_000, "resolved_trades": 10, "is_insider": False}
        self.assertFalse(scout.qualifies(w))

    def test_qualifies_insider_overrides(self):
        # Инсайдер: достаточный WinRate И положительный PnL
        w = {"winrate": CONFIG.scout.insider_min_winrate, "total_pnl": 5_000,
             "resolved_trades": 0, "is_insider": True}
        self.assertTrue(scout.qualifies(w))

    def test_qualifies_insider_pure_loser_blocked(self):
        # Новый кошелёк с 0% побед — не инсайдер, а просто убыточный новичок
        w = {"winrate": 0.0, "total_pnl": -100_000, "resolved_trades": 0, "is_insider": True}
        self.assertFalse(scout.qualifies(w))

    def test_qualifies_insider_negative_pnl_blocked(self):
        # Регрессия: «инсайдер» с WinRate 60%, но PnL −$126k не должен проходить
        w = {"winrate": 0.6, "total_pnl": -126_909, "resolved_trades": 355, "is_insider": True}
        self.assertFalse(scout.qualifies(w))

    def test_first_trade_ts_unknown_when_capped(self):
        # Гиперактивный кошелёк: все страницы полные → возраст неизвестен (None),
        # а не «минимум из последних 2000 сделок» (ложный молодой инсайдер)
        from unittest.mock import patch
        full_page = [{"timestamp": 1_750_000_000 + i} for i in range(500)]
        with patch.object(api, "get_trades", return_value=full_page):
            self.assertIsNone(api.get_first_trade_ts("0xbot", max_pages=4))

    def test_first_trade_ts_exact_when_history_ends(self):
        # История исчерпана (короткая последняя страница) → точный первый трейд
        from unittest.mock import patch
        pages = [[{"timestamp": 1_750_000_000}, {"timestamp": 1_749_000_000}], []]
        with patch.object(api, "get_trades", side_effect=pages):
            self.assertEqual(api.get_first_trade_ts("0xnew"), 1_749_000_000)

    def test_qualifies_leaderboard_whale(self):
        # Подтверждённый lifetime PnL с leaderboard + мало решённых позиций
        # в снапшоте (winrate не проверяем — мало данных)
        w = {"winrate": 0.0, "total_pnl": 50, "resolved_trades": 1,
             "is_insider": False, "lifetime_pnl": 100_000}
        self.assertTrue(scout.qualifies(w))

    def test_qualifies_leaderboard_whale_bad_snapshot_blocked(self):
        # Leaderboard-кит, но снапшот с достаточной статистикой показывает
        # низкий winrate → блокируем
        w = {"winrate": 0.20, "total_pnl": -500, "resolved_trades": 40,
             "is_insider": False, "lifetime_pnl": 100_000}
        self.assertFalse(scout.qualifies(w))


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.eng = engine.FilterEngine()

    def _entry(self, wallet, side, outcome="yes", price=0.6, notional=600):
        return {"wallet": wallet, "side": side, "outcome": outcome, "price": price,
                "notional": notional, "market": "Will X happen?", "cond_id": "c1",
                "event_slug": "will-x"}

    def test_consensus_buy_dominates(self):
        entries = [self._entry(f"w{i}", "BUY") for i in range(3)] + [self._entry("s1", "SELL")]
        sig = self.eng.evaluate_market(entries, now=1000)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "BUY")
        self.assertEqual(sig["n_wallets"], 3)
        self.assertEqual(sig["signal_type"], "consensus")

    def test_trusted_whale_fires_alone(self):
        # Один известный кит с достаточным notional — сигнал без консенсуса
        # (elite не передан → элитой считается весь trusted)
        trusted = {"trusted_w1"}
        entries = [self._entry("trusted_w1", "BUY", notional=1000)]
        sig = self.eng.evaluate_market(entries, now=1000, trusted=trusted)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["signal_type"], "trusted_whale")

    def test_non_elite_whale_needs_consensus(self):
        # Отслеживаемый, но НЕ элитный кит в одиночку сигнала не даёт
        trusted = {"mid_w1"}
        entries = [self._entry("mid_w1", "BUY", notional=5000)]
        sig = self.eng.evaluate_market(entries, now=1000, trusted=trusted, elite=set())
        self.assertIsNone(sig)

    def test_non_elite_whales_fire_by_consensus(self):
        # Два не-элитных кита на одной стороне → консенсусный сигнал
        trusted = {"mid_w1", "mid_w2"}
        entries = [self._entry("mid_w1", "BUY", notional=500),
                   self._entry("mid_w2", "BUY", notional=500)]
        sig = self.eng.evaluate_market(entries, now=1000, trusted=trusted, elite=set())
        self.assertIsNotNone(sig)
        self.assertEqual(sig["signal_type"], "consensus")

    def test_unknown_single_wallet_no_signal(self):
        # Незнакомый кошелёк один — сигнала нет
        entries = [self._entry("unknown_w", "BUY", notional=5000)]
        self.assertIsNone(self.eng.evaluate_market(entries, now=1000, trusted=set()))

    def test_no_signal_below_min_wallets(self):
        entries = [self._entry("w1", "BUY")]  # 1 < min_wallets(2), не trusted
        self.assertIsNone(self.eng.evaluate_market(entries, now=1000, trusted=set()))

    def test_volume_threshold_blocks_small(self):
        entries = [self._entry(f"w{i}", "BUY", notional=10) for i in range(3)]  # 30 < $1000
        self.assertIsNone(self.eng.evaluate_market(entries, now=1000, trusted=set()))

    def test_mev_mute(self):
        w = "bot1"
        for i in range(CONFIG.engine.mev_max_trades_per_window):
            self.eng.observe(w, ts=1000 + i * 0.1)
        self.assertTrue(self.eng.is_muted(w, now=1000))
        entries = [self._entry(w, "BUY") for _ in range(3)]
        self.assertIsNone(self.eng.evaluate_market(entries, now=1000, trusted={w}))

    def test_delta_neutral_flag(self):
        entries = [self._entry("w1", "BUY", outcome="yes"),
                   self._entry("w2", "BUY", outcome="yes"),
                   self._entry("w3", "BUY", outcome="no")]
        sig = self.eng.evaluate_market(entries, now=1000)
        self.assertTrue(sig["delta_neutral"])


class TestResolveToken(unittest.TestCase):
    TOKENS = {"yes": "tok_yes", "no": "tok_no"}

    def test_buy_returns_target_outcome(self):
        from src.tracker import resolve_token_id
        token, outcome = resolve_token_id(self.TOKENS, "Yes", "BUY")
        self.assertEqual((token, outcome), ("tok_yes", "yes"))

    def test_sell_returns_opposite_outcome(self):
        # Кит продал YES → мы покупаем NO и помним, что купили именно NO
        from src.tracker import resolve_token_id
        token, outcome = resolve_token_id(self.TOKENS, "Yes", "SELL")
        self.assertEqual((token, outcome), ("tok_no", "no"))

    def test_missing_map_returns_none_pair(self):
        from src.tracker import resolve_token_id
        self.assertEqual(resolve_token_id({}, "Yes", "BUY"), (None, None))


class TestCapitalManagement(unittest.TestCase):
    def test_kelly_sizing_caps_at_position_max_pct(self):
        from src.tracker import position_size_usd
        # wr=0.8, цена 0.5 → f*=0.6 → 0.25×0.6×1000=$150, но кап 2% = $20
        size = position_size_usd([{"winrate": 0.8}], price=0.5, balance=1000)
        self.assertAlmostEqual(size, 1000 * CONFIG.trading.position_max_pct)

    def test_kelly_sizing_scales_with_edge(self):
        from src.tracker import position_size_usd
        # wr=0.52, цена 0.5 → f*=0.04 → 0.25×0.04×1000=$10 (между базой и капом)
        size = position_size_usd([{"winrate": 0.52}], price=0.5, balance=1000)
        self.assertAlmostEqual(size, 10.0)

    def test_no_edge_returns_base_amount(self):
        from src.tracker import position_size_usd
        # winrate <= цены → края нет → базовая ставка
        size = position_size_usd([{"winrate": 0.5}], price=0.6, balance=1000)
        self.assertEqual(size, CONFIG.trading.trade_amount_usd)
        # нет статистики китов → базовая ставка
        self.assertEqual(position_size_usd([], 0.5, 1000), CONFIG.trading.trade_amount_usd)

    def test_entry_blocked_by_position_limit(self):
        from src.tracker import entry_blocked
        positions = {f"t{i}": {"cond_id": f"c{i}"}
                     for i in range(CONFIG.trading.max_open_positions)}
        self.assertIsNotNone(entry_blocked(positions, "c_new"))

    def test_entry_blocked_same_market(self):
        from src.tracker import entry_blocked
        positions = {"t1": {"cond_id": "c1"}}
        self.assertIsNotNone(entry_blocked(positions, "c1"))
        self.assertIsNone(entry_blocked(positions, "c2"))

    def test_poll_round_robin(self):
        from src.tracker import next_poll_chunk
        tracked = {"w1", "w2", "w3"}
        chunk, rest = next_poll_chunk([], tracked, 2)
        self.assertEqual(chunk, ["w1", "w2"])
        self.assertEqual(rest, ["w3"])
        chunk, rest = next_poll_chunk(rest, tracked, 2)
        self.assertEqual(chunk, ["w3"])
        self.assertEqual(rest, [])
        # пустая очередь снова пополняется из tracked
        chunk, _ = next_poll_chunk(rest, tracked, 2)
        self.assertEqual(chunk, ["w1", "w2"])

    def test_daily_stop_loss(self):
        import tempfile
        from src import tracker
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        os.unlink(tmp.name)
        orig = CONFIG.files.metrics_file
        CONFIG.files.metrics_file = tmp.name
        try:
            # Первый вызов дня — якорь, паузы нет
            self.assertFalse(tracker.daily_stop_active(1000.0))
            # Просадка 3% < 5% — торгуем
            self.assertFalse(tracker.daily_stop_active(970.0))
            # Просадка 6% >= 5% — пауза
            self.assertTrue(tracker.daily_stop_active(940.0))
            # Новый день UTC — якорь сбрасывается, торгуем снова
            m = tracker._load_metrics()
            m["day"] = "2000-01-01"
            tracker._save_metrics(m)
            self.assertFalse(tracker.daily_stop_active(940.0))
        finally:
            CONFIG.files.metrics_file = orig
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)


class TestMarketFilter(unittest.TestCase):
    def test_skips_sports(self):
        self.assertTrue(CONFIG.market_filter.should_skip("NBA Finals 2026"))
        self.assertTrue(CONFIG.market_filter.should_skip("Bitcoin Up or Down - June 9"))

    def test_keeps_macro(self):
        self.assertFalse(CONFIG.market_filter.should_skip("Will the Fed cut rates in July?"))


class TestDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self._orig = CONFIG.files.db_path
        CONFIG.files.db_path = self.tmp.name
        db.init_db()

    def tearDown(self):
        CONFIG.files.db_path = self._orig
        os.unlink(self.tmp.name)

    def test_whale_roundtrip(self):
        db.upsert_whale({"address": "0xABC", "winrate": 0.9, "total_pnl": 200000,
                         "resolved_trades": 12, "is_insider": False, "age_days": 120})
        w = db.get_whale("0xabc")
        self.assertIsNotNone(w)
        self.assertEqual(w["address"], "0xabc")
        self.assertAlmostEqual(w["total_pnl"], 200000)
        self.assertIn("0xabc", db.get_tracked_addresses())

    def test_tx_dedup(self):
        tx = {"tx_hash": "0xdead", "outcome": "Yes", "address": "0xabc",
              "condition_id": "c1", "side": "BUY", "amount_usd": 600, "price": 0.6, "timestamp": 1}
        self.assertFalse(db.tx_seen("0xdead", "Yes"))
        db.record_tx(tx)
        self.assertTrue(db.tx_seen("0xdead", "Yes"))

    def _signal(self, cond_id, wallets, side="BUY", outcome="yes"):
        sig = {"cond_id": cond_id, "market": "M?", "side": side,
               "consensus_outcome": outcome, "signal_type": "trusted_whale"}
        db.record_signal_outcome(sig, wallets, entry_price=0.6)

    def test_signal_outcome_roundtrip_and_attribution(self):
        self._signal("c1", ["0xAAA"])
        self._signal("c2", ["0xaaa", "0xbbb"])
        unresolved = db.get_unresolved_outcomes()
        self.assertEqual(len(unresolved), 2)

        db.mark_outcome_resolved(unresolved[0]["id"], "yes", won=True)
        db.mark_outcome_resolved(unresolved[1]["id"], "no", won=False)
        self.assertEqual(len(db.get_unresolved_outcomes()), 0)

        stats = db.whale_signal_stats()
        self.assertEqual(stats["0xaaa"], {"resolved": 2, "wins": 1})
        self.assertEqual(stats["0xbbb"], {"resolved": 1, "wins": 0})

    def test_prune_bad_performers(self):
        db.upsert_whale({"address": "0xbad", "winrate": 0.8, "total_pnl": 5000})
        db.upsert_whale({"address": "0xgood", "winrate": 0.8, "total_pnl": 5000})
        # 0xbad: 5 разрешённых сигналов, 1 победа (20% < 40%) → удаляется
        for i in range(5):
            self._signal(f"c{i}", ["0xbad"])
        # 0xgood: 5 сигналов, 4 победы → остаётся
        for i in range(5, 10):
            self._signal(f"c{i}", ["0xgood"])
        for o in db.get_unresolved_outcomes():
            is_bad = "0xbad" in o["wallets"]
            won = (o["id"] % 5 == 0) if is_bad else (o["id"] % 5 != 0)
            db.mark_outcome_resolved(o["id"], "yes" if won else "no", won=won)

        removed = db.prune_bad_performers(min_signals=5, min_winshare=0.40)
        self.assertEqual(removed, ["0xbad"])
        self.assertIsNone(db.get_whale("0xbad"))
        self.assertIsNotNone(db.get_whale("0xgood"))


class TestNotifier(unittest.TestCase):
    def test_format_contains_required_fields(self):
        n = notifier.Notifier()
        signal = {"side": "BUY", "n_wallets": 4, "total_notional": 12500,
                  "consensus_outcome": "yes", "median_price": 0.62, "delta_neutral": True,
                  "market": "Will the Fed cut rates?", "cond_id": "c1", "event_slug": "fed-cut"}
        whales = [{"winrate": 0.88, "total_pnl": 1_200_000, "is_insider": True, "age_days": 9}]
        msg = n.format_signal(1, signal, whales, "✅ PAPER: BUY")
        self.assertIn("polymarket.com/event/fed-cut", msg)  # ссылка
        self.assertIn("BUY", msg)                            # сторона
        self.assertIn("0.62", msg)                           # цена входа
        self.assertIn("WinRate", msg)                        # стата кошелька
        self.assertIn("INSIDER", msg)                        # инсайдер-метка
        self.assertIn("дельта", msg.lower())                 # риск-флаг


if __name__ == "__main__":
    unittest.main(verbosity=2)
