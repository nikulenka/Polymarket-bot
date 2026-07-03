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


class TestMarketResolution(unittest.TestCase):
    """get_market_resolution: фильтр condition_ids + признак фактического исхода."""
    from unittest.mock import patch

    def _market(self, **over):
        m = {"closed": False, "outcomes": '["Yes", "No"]',
             "outcomePrices": '["0.5", "0.5"]', "endDate": "2030-01-01T00:00:00Z"}
        m.update(over)
        return [m]

    def test_closed_market_resolves(self):
        from unittest.mock import patch
        with patch.object(api, "_get", return_value=self._market(
                closed=True, outcomePrices='["0.995", "0.005"]')):
            self.assertEqual(api.get_market_resolution("0xabc"), "yes")

    def test_past_enddate_extreme_resolves_even_if_not_closed(self):
        # Polymarket держит closed=False после исхода — событие прошло, цена на экстремуме
        from unittest.mock import patch
        with patch.object(api, "_get", return_value=self._market(
                closed=False, endDate="2020-01-01T00:00:00Z",
                outcomePrices='["0.002", "0.998"]')):
            self.assertEqual(api.get_market_resolution("0xabc"), "no")

    def test_future_enddate_extreme_stays_pending(self):
        # Цена на экстремуме, но событие ещё не наступило → не считаем разрешённым
        from unittest.mock import patch
        with patch.object(api, "_get", return_value=self._market(
                closed=False, endDate="2030-01-01T00:00:00Z",
                outcomePrices='["0.001", "0.999"]')):
            self.assertIsNone(api.get_market_resolution("0xabc"))

    def test_non_extreme_price_stays_pending(self):
        from unittest.mock import patch
        with patch.object(api, "_get", return_value=self._market(
                closed=False, endDate="2020-01-01T00:00:00Z",
                outcomePrices='["0.6", "0.4"]')):
            self.assertIsNone(api.get_market_resolution("0xabc"))

    def test_empty_response_returns_none(self):
        from unittest.mock import patch
        with patch.object(api, "_get", return_value=[]):
            self.assertIsNone(api.get_market_resolution("0xabc"))


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

    def test_cheap_entry_cap_no_longer_boosted(self):
        # Патч G: повышенный кап патча B отменён — бакет <0.35 дал −$236 за
        # 19.06–03.07 при неподтверждённой правоте китов. Кап как у всех.
        from src.tracker import position_size_usd
        # wr=0.9, цена 0.2 (< 0.35) → f* большой, упираемся в кап cheap-бакета
        size = position_size_usd([{"winrate": 0.9}], price=0.2, balance=1000)
        self.assertAlmostEqual(size, 1000 * CONFIG.trading.position_max_pct_cheap)
        self.assertLessEqual(CONFIG.trading.position_max_pct_cheap,
                             CONFIG.trading.position_max_pct)

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


class TestExitProfile(unittest.TestCase):
    """Патчи A/F: профиль выхода (TP/флип/SL) зависит от цены входа."""

    def test_cheap_entry_disables_partial_and_widens_tp(self):
        from src.tracker import exit_params
        _, frac, tp, sl = exit_params(0.20)  # дешёвый лонгшот
        self.assertEqual(frac, CONFIG.trading.cheap_partial_take_fraction)  # 0 → флип выкл
        self.assertEqual(tp, CONFIG.trading.cheap_take_profit_delta)        # широкий TP
        self.assertIsNone(sl)  # патч F: стоп выключен — едем до разрешения

    def test_expensive_entry_takes_fast(self):
        from src.tracker import exit_params
        pdelta, frac, tp, sl = exit_params(0.70)  # фаворит
        self.assertEqual(tp, CONFIG.trading.expensive_take_profit_delta)
        self.assertEqual(pdelta, CONFIG.trading.expensive_partial_take_delta)
        self.assertGreater(frac, 0)
        self.assertEqual(sl, CONFIG.trading.expensive_stop_loss_delta)  # патч F: −15c оправдан

    def test_mid_entry_uses_base_profile(self):
        from src.tracker import exit_params
        pdelta, frac, tp, sl = exit_params(0.42)  # середина
        self.assertEqual(tp, CONFIG.trading.take_profit_delta)
        self.assertEqual(frac, CONFIG.trading.partial_take_fraction)
        self.assertEqual(pdelta, CONFIG.trading.partial_take_delta)
        self.assertEqual(sl, CONFIG.trading.stop_loss_delta)  # патч F: базовый стоп

    def test_cheap_entry_never_stops_out(self):
        """Патч F: дешёвый вход 0.18, упавший на −16c (тот самый кейс из
        анализа 10–24 июня, выбивавший позицию в минус), стоп НЕ срабатывает."""
        from src.tracker import exit_params
        entry = 0.18
        _, _, _, sl = exit_params(entry)
        change = 0.02 - entry  # цена ушла к 0.02 → −0.16
        stopped = sl is not None and change <= sl
        self.assertFalse(stopped)  # едем до разрешения, а не режем шумом

    def test_expensive_entry_stops_out_on_15c_drop(self):
        """Дорогой фаворит со стопом −15c: падение на 16c закрывает позицию."""
        from src.tracker import exit_params
        entry = 0.70
        _, _, _, sl = exit_params(entry)
        change = 0.54 - entry  # −0.16
        self.assertIsNotNone(sl)
        self.assertTrue(change <= sl)


class TestNoiseFloorGate(unittest.TestCase):
    """manage_positions(): порог cur<=0.03 не должен фиксировать ложный
    проигрыш дешёвого лонгшота (стоп выключен патчем F), если Gamma не
    подтверждает фактическое разрешение рынка — иначе шум live-рынка
    (например, спорт в моменте) выбивает позицию тем же способом, который
    патч F должен был устранить."""

    def _position(self, entry=0.18, tokens=100.0):
        # close_at — динамический (в будущем): зашитая дата однажды протухла,
        # и таймер времени закрывал позицию, ломая смысл теста.
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        return {
            "tok1": {
                "market": "Exact Score: Test 0 - 0 Test?",
                "cond_id": "0xabc",
                "outcome": "yes",
                "signal_side": "BUY",
                "entry_price": entry,
                "tokens": tokens,
                "opened_at": (now - timedelta(hours=1)).isoformat(),
                "close_at": (now + timedelta(hours=23)).isoformat(),
            }
        }

    def test_cheap_entry_unconfirmed_noise_keeps_riding(self):
        from unittest.mock import patch
        from src import tracker
        positions = self._position()
        with patch.object(tracker, "load_positions", return_value=positions), \
             patch.object(tracker, "save_positions") as mock_save, \
             patch.object(tracker.api, "get_price", return_value=0.02), \
             patch.object(tracker.api, "get_market_resolution", return_value=None), \
             patch.object(tracker, "close_position") as mock_close:
            tracker.manage_positions()
        mock_close.assert_not_called()
        mock_save.assert_not_called()

    def test_cheap_entry_gamma_confirmed_loss_closes_at_zero(self):
        from unittest.mock import patch
        from src import tracker
        positions = self._position()
        with patch.object(tracker, "load_positions", return_value=positions), \
             patch.object(tracker, "save_positions") as mock_save, \
             patch.object(tracker.api, "get_price", return_value=0.02), \
             patch.object(tracker.api, "get_market_resolution", return_value="no"), \
             patch.object(tracker, "close_position", return_value=True) as mock_close, \
             patch.object(tracker, "get_usdc_balance", return_value=1000.0):
            tracker.manage_positions()
        mock_close.assert_called_once_with("tok1", 100.0, 0.0)
        mock_save.assert_called_once()

    def test_expensive_entry_still_closes_immediately_on_noise_floor(self):
        """Дорогой вход со стопом — старое поведение без изменений:
        закрывается сразу по cur, без обращения к Gamma."""
        from unittest.mock import patch
        from src import tracker
        positions = self._position(entry=0.70)
        with patch.object(tracker, "load_positions", return_value=positions), \
             patch.object(tracker, "save_positions"), \
             patch.object(tracker.api, "get_price", return_value=0.02), \
             patch.object(tracker.api, "get_market_resolution") as mock_resolution, \
             patch.object(tracker, "close_position", return_value=True) as mock_close, \
             patch.object(tracker, "get_usdc_balance", return_value=1000.0):
            tracker.manage_positions()
        mock_close.assert_called_once_with("tok1", 100.0, 0.02)
        mock_resolution.assert_not_called()


class TestSignalGates(unittest.TestCase):
    """Патчи B/C: ограничения одиночного сигнала trusted_whale в execute_trade."""
    from unittest.mock import patch

    def _signal(self, **over):
        s = {"cond_id": "c1", "median_price": 0.6, "side": "BUY",
             "consensus_outcome": "yes", "signal_type": "trusted_whale",
             "market": "Will X?"}
        s.update(over)
        return s

    def test_single_whale_sell_skipped(self):
        from unittest.mock import patch
        from src import tracker
        with patch.object(tracker.api, "get_market_tokens",
                          return_value={"yes": "tok_y", "no": "tok_n"}), \
             patch.object(tracker.api, "get_price", return_value=0.4):
            status = tracker.execute_trade(self._signal(side="SELL"), {}, [])
        self.assertIn("SELL", status)
        self.assertTrue(status.startswith("⏭"))

    def test_single_whale_favorite_price_skipped(self):
        from unittest.mock import patch
        from src import tracker
        with patch.object(tracker.api, "get_market_tokens",
                          return_value={"yes": "tok_y", "no": "tok_n"}), \
             patch.object(tracker.api, "get_price", return_value=0.65):
            status = tracker.execute_trade(self._signal(side="BUY"), {}, [])
        self.assertTrue(status.startswith("⏭"))
        self.assertIn("консенсус", status)

    def test_single_whale_no_edge_skipped(self):
        from unittest.mock import patch
        from src import tracker
        # цена 0.45 < single_whale_max_price, но WinRate кита 0.40 <= цена → нет края
        with patch.object(tracker.api, "get_market_tokens",
                          return_value={"yes": "tok_y", "no": "tok_n"}), \
             patch.object(tracker.api, "get_price", return_value=0.45):
            status = tracker.execute_trade(
                self._signal(side="BUY"), {}, [{"winrate": 0.40}])
        self.assertTrue(status.startswith("⏭"))
        self.assertIn("края", status)

    def test_consensus_bypasses_single_whale_gates(self):
        # Консенсус-сигнал в зоне фаворитов на SELL НЕ режется гейтами одиночки.
        # save_positions мокаем ОБЯЗАТЕЛЬНО: без мока тест пишет tok_n в
        # реальный data/open_positions.json (поймано сверкой на проде 04.07).
        from unittest.mock import patch
        from src import tracker
        with patch.object(tracker.api, "get_market_tokens",
                          return_value={"yes": "tok_y", "no": "tok_n"}), \
             patch.object(tracker.api, "get_price", return_value=0.65), \
             patch.object(tracker, "get_usdc_balance", return_value=1000.0), \
             patch.object(tracker, "daily_stop_active", return_value=False), \
             patch.object(tracker, "place_bet", return_value=True), \
             patch.object(tracker, "save_positions"):
            # median_price=0.35 → при SELL ref=1-0.35=0.65 ≈ ask 0.65, слиппедж ок
            status = tracker.execute_trade(
                self._signal(side="SELL", signal_type="consensus", median_price=0.35),
                {}, [])
        self.assertFalse(status.startswith("⏭"), status)


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

        # Резолвим по cond_id, а не по индексу: порядок очереди — деталь ротации
        by_cond = {o["cond_id"]: o for o in unresolved}
        db.mark_outcome_resolved(by_cond["c1"]["id"], "yes", won=True)
        db.mark_outcome_resolved(by_cond["c2"]["id"], "no", won=False)
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
        msg = n.format_signal(1, signal, whales, "✅ PAPER: BUY", balance=1050.0)
        self.assertIn("polymarket.com/event/fed-cut", msg)  # ссылка
        self.assertIn("BUY", msg)                            # сторона
        self.assertIn("0.62", msg)                           # цена входа
        self.assertIn("WinRate", msg)                        # стата кошелька
        self.assertIn("INSIDER", msg)                        # инсайдер-метка
        self.assertIn("дельта", msg.lower())                 # риск-флаг
        self.assertIn("Баланс", msg)                         # состояние счёта
        self.assertIn("от старта", msg)                      # итоговый P&L

    def test_balance_line_pnl_sign(self):
        n = notifier.Notifier()
        start = CONFIG.trading.paper_start_balance
        up = n.balance_line(start + 25.0)
        down = n.balance_line(start - 40.0)
        self.assertIn("+25.00$", up)    # прибыль со знаком +
        self.assertIn("-40.00$", down)  # убыток со знаком −


class TestStateFiles(unittest.TestCase):
    """Патч G: атомарная запись state-файлов и громкий отказ при битом JSON.
    Мотив: неатомарный open('w')+dump, убитый рестартом, съел ~$179 позиций
    (19–25.06) — файл бился, load молча возвращал {}."""

    def setUp(self):
        import tempfile
        from src import state
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "st.json")
        self.alerts = []
        state.set_alert_hook(self.alerts.append)

    def tearDown(self):
        import shutil
        from src import state
        state.set_alert_hook(None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_roundtrip_and_no_tmp_leftover(self):
        from src import state
        state.save_json(self.path, {"a": 1})
        self.assertEqual(state.load_json(self.path, dict), {"a": 1})
        self.assertFalse(os.path.exists(self.path + ".tmp"))  # tmp заменён атомарно

    def test_missing_file_returns_default_silently(self):
        from src import state
        self.assertEqual(state.load_json(self.path, dict), {})
        self.assertEqual(self.alerts, [])  # нет файла — не авария

    def test_corrupt_file_quarantined_and_alerted(self):
        from src import state
        with open(self.path, "w") as f:
            f.write('{"balance": 911.2, "fil')  # обрыв посреди записи
        out = state.load_json(self.path, dict)
        self.assertEqual(out, {})
        self.assertEqual(len(self.alerts), 1)          # CRITICAL-алерт ушёл
        self.assertIn("повреждён", self.alerts[0])
        self.assertFalse(os.path.exists(self.path))     # битый файл убран...
        quarantined = [f for f in os.listdir(self.dir) if ".corrupt-" in f]
        self.assertEqual(len(quarantined), 1)           # ...в карантин, не удалён

    def test_default_factory_not_shared(self):
        from src import state
        a = state.load_json(self.path, dict)
        a["x"] = 1
        self.assertEqual(state.load_json(self.path, dict), {})  # свежий объект


class TestReconcile(unittest.TestCase):
    """Патч G: сверка ленты paper-филлов с open_positions при старте."""

    def _fills(self):
        # куплено 100 tok1 (открыта) и 50 tok2, tok2 продана полностью
        return [
            {"token_id": "tok1", "side": "BUY", "size": 100.0},
            {"token_id": "tok2", "side": "BUY", "size": 50.0},
            {"token_id": "tok2", "side": "SELL", "size": 50.0},
        ]

    def test_consistent_state_is_silent(self):
        from src.tracker import reconcile_report
        report = reconcile_report(self._fills(), {"tok1": {"tokens": 100.0}})
        self.assertIsNone(report)

    def test_ghost_position_detected(self):
        # tok1 куплен по филлам, но из open_positions исчез (кейс 19–25.06)
        from src.tracker import reconcile_report
        report = reconcile_report(self._fills(), {})
        self.assertIsNotNone(report)
        self.assertIn("tok1", report)
        self.assertIn("НЕ отслеживаются", report)

    def test_orphan_position_detected(self):
        # позиция есть в файле, а филлов под неё нет
        from src.tracker import reconcile_report
        report = reconcile_report(self._fills(), {"tok1": {}, "tok9": {}})
        self.assertIsNotNone(report)
        self.assertIn("tok9", report)
        self.assertIn("без филлов", report)

    def test_partial_close_leaves_no_false_alarm(self):
        from src.tracker import reconcile_report
        fills = self._fills() + [{"token_id": "tok1", "side": "SELL", "size": 100.0}]
        self.assertIsNone(reconcile_report(fills, {}))


class TestOutcomeQueueRotation(unittest.TestCase):
    """Патч G: очередь сверки исходов ротируется и не закупоривается."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self._orig = CONFIG.files.db_path
        CONFIG.files.db_path = self.tmp.name
        db.init_db()
        for i in range(1, 5):
            db.record_signal_outcome(
                {"cond_id": f"c{i}", "market": "M?", "side": "BUY",
                 "consensus_outcome": "yes", "signal_type": "consensus"},
                ["0xaaa"], entry_price=0.5)

    def tearDown(self):
        CONFIG.files.db_path = self._orig
        os.unlink(self.tmp.name)

    def test_unchecked_come_first_newest_forward(self):
        ids = [o["cond_id"] for o in db.get_unresolved_outcomes(limit=2)]
        self.assertEqual(ids, ["c4", "c3"])  # свежие вперёд, а не старейшие

    def test_checked_rotate_to_back_of_queue(self):
        first = db.get_unresolved_outcomes(limit=2)
        db.touch_outcomes_checked([o["id"] for o in first])
        second = db.get_unresolved_outcomes(limit=2)
        # следующий батч — те, кого ещё не проверяли (раньше застревали навсегда)
        self.assertEqual({o["cond_id"] for o in second}, {"c1", "c2"})
        db.touch_outcomes_checked([o["id"] for o in second])
        third = db.get_unresolved_outcomes(limit=2)
        # все проверены по разу → по кругу, начиная с проверенных раньше всех
        self.assertEqual({o["cond_id"] for o in third}, {"c4", "c3"})

    def test_gave_up_leaves_queue(self):
        all_ids = [o["id"] for o in db.get_unresolved_outcomes()]
        db.mark_outcomes_gave_up(all_ids[:2])
        left = db.get_unresolved_outcomes()
        self.assertEqual(len(left), 2)
        self.assertTrue(all(o["id"] not in all_ids[:2] for o in left))

    def test_check_signal_outcomes_gives_up_on_stale(self):
        """tracker.check_signal_outcomes: древний неразрешаемый сигнал
        помечается gave_up и больше не жуётся каждые 30 минут."""
        from unittest.mock import patch
        from src import tracker
        with db._conn() as con:  # состарим один сигнал за горизонт
            con.execute("UPDATE signal_outcomes SET created_at = ? WHERE cond_id = 'c1'",
                        ("2026-01-01T00:00:00+00:00",))
        with patch.object(tracker.api, "get_market_resolution", return_value=None):
            tracker.check_signal_outcomes()
        left = {o["cond_id"] for o in db.get_unresolved_outcomes()}
        self.assertNotIn("c1", left)          # безнадёжный выбыл
        self.assertEqual(left, {"c2", "c3", "c4"})


class TestCheapEntryGates(unittest.TestCase):
    """Патч G: лотерейный гейт и горизонт разрешения для дешёвых входов."""

    def _signal(self, **over):
        s = {"cond_id": "c1", "median_price": 0.15, "side": "BUY",
             "consensus_outcome": "yes", "signal_type": "trusted_whale",
             "market": "Will X?"}
        s.update(over)
        return s

    def test_single_whale_lottery_entry_skipped(self):
        from unittest.mock import patch
        from src import tracker
        with patch.object(tracker.api, "get_market_tokens",
                          return_value={"yes": "tok_y", "no": "tok_n"}), \
             patch.object(tracker.api, "get_price", return_value=0.15):
            status = tracker.execute_trade(self._signal(), {}, [{"winrate": 0.9}])
        self.assertTrue(status.startswith("⏭"), status)
        self.assertIn("консенсус", status)

    def test_consensus_lottery_entry_passes_gate(self):
        from unittest.mock import patch, MagicMock
        from datetime import datetime, timezone, timedelta
        from src import tracker
        end = datetime.now(timezone.utc) + timedelta(days=2)
        positions = {}
        with patch.object(tracker.api, "get_market_tokens",
                          return_value={"yes": "tok_y", "no": "tok_n"}), \
             patch.object(tracker.api, "get_price", return_value=0.15), \
             patch.object(tracker.api, "get_market_end_date", return_value=end), \
             patch.object(tracker, "get_usdc_balance", return_value=1000.0), \
             patch.object(tracker, "daily_stop_active", return_value=False), \
             patch.object(tracker, "place_bet", return_value=True), \
             patch.object(tracker, "save_positions"):
            status = tracker.execute_trade(
                self._signal(signal_type="consensus"), positions, [])
        self.assertFalse(status.startswith("⏭"), status)

    def test_cheap_far_horizon_skipped(self):
        """Разрешение дальше cheap_max_horizon_days → вход пропускается:
        стопа нет, а таймер всё равно закрыл бы по шумовой цене."""
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta
        from src import tracker
        far = datetime.now(timezone.utc) + timedelta(days=30)
        with patch.object(tracker.api, "get_market_tokens",
                          return_value={"yes": "tok_y", "no": "tok_n"}), \
             patch.object(tracker.api, "get_price", return_value=0.15), \
             patch.object(tracker.api, "get_market_end_date", return_value=far):
            status = tracker.execute_trade(
                self._signal(signal_type="consensus"), {}, [])
        self.assertTrue(status.startswith("⏭"), status)
        self.assertIn("лонгшот", status)

    def test_cheap_close_at_bound_to_end_date(self):
        """close_at дешёвой позиции = endDate рынка (+буфер), не 24ч-таймер."""
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta
        from src import tracker
        end = datetime.now(timezone.utc) + timedelta(days=3)
        positions = {}
        with patch.object(tracker.api, "get_market_tokens",
                          return_value={"yes": "tok_y", "no": "tok_n"}), \
             patch.object(tracker.api, "get_price", return_value=0.15), \
             patch.object(tracker.api, "get_market_end_date", return_value=end), \
             patch.object(tracker, "get_usdc_balance", return_value=1000.0), \
             patch.object(tracker, "daily_stop_active", return_value=False), \
             patch.object(tracker, "place_bet", return_value=True), \
             patch.object(tracker, "save_positions"):
            status = tracker.execute_trade(
                self._signal(signal_type="consensus"), positions, [])
        self.assertFalse(status.startswith("⏭"), status)
        from datetime import datetime as dt
        close_at = dt.fromisoformat(positions["tok_y"]["close_at"])
        self.assertEqual(close_at, end + timedelta(hours=2))

    def test_mid_entry_keeps_24h_timer(self):
        """Середина (>=0.35) не трогает Gamma и живёт по прежнему таймеру."""
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta
        from src import tracker
        positions = {}
        with patch.object(tracker.api, "get_market_tokens",
                          return_value={"yes": "tok_y", "no": "tok_n"}), \
             patch.object(tracker.api, "get_price", return_value=0.45), \
             patch.object(tracker.api, "get_market_end_date") as mock_end, \
             patch.object(tracker, "get_usdc_balance", return_value=1000.0), \
             patch.object(tracker, "daily_stop_active", return_value=False), \
             patch.object(tracker, "place_bet", return_value=True), \
             patch.object(tracker, "save_positions"):
            status = tracker.execute_trade(
                self._signal(signal_type="consensus", median_price=0.45),
                positions, [])
        self.assertFalse(status.startswith("⏭"), status)
        mock_end.assert_not_called()
        from datetime import datetime as dt
        close_at = dt.fromisoformat(positions["tok_y"]["close_at"])
        expected = datetime.now(timezone.utc) + timedelta(
            hours=CONFIG.trading.position_hold_hours)
        self.assertLess(abs((close_at - expected).total_seconds()), 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
