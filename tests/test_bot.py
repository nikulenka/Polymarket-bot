"""
Mock-тесты (без обращения к блокчейну/API), как рекомендовано в требованиях.
Запуск:  PYTHONPATH=. python3 -m unittest tests.test_bot -v
"""

import os
import tempfile
import unittest

from src.config import CONFIG
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
        # Инсайдер должен иметь минимальный WinRate (insider_min_winrate)
        w = {"winrate": CONFIG.scout.insider_min_winrate, "total_pnl": 0, "resolved_trades": 0, "is_insider": True}
        self.assertTrue(scout.qualifies(w))

    def test_qualifies_insider_pure_loser_blocked(self):
        # Новый кошелёк с 0% побед — не инсайдер, а просто убыточный новичок
        w = {"winrate": 0.0, "total_pnl": -100_000, "resolved_trades": 0, "is_insider": True}
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
        trusted = {"trusted_w1"}
        entries = [self._entry("trusted_w1", "BUY", notional=1000)]
        sig = self.eng.evaluate_market(entries, now=1000, trusted=trusted)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["signal_type"], "trusted_whale")

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
