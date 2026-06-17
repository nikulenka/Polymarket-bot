#!/usr/bin/env python3
"""
Калибровочная проверка петли обратной связи (P1/P2 из docs/improvement-plan.md).

Разовый запуск (~21 июня 2026), когда накопятся разрешённые signal_outcomes:
считает winshare по китам, число разрешённых/ожидающих сигналов, кандидатов
на прунинг и шлёт отчёт в Telegram с напоминанием про калибровку порогов.

Только чтение БД — прунинг и так делает живой трекер каждые 30 минут.

Запуск: PYTHONPATH=. python3 -m scripts.calibration_check
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db
from src.config import CONFIG
from src.notifier import Notifier


def build_report() -> str:
    summary = db.signal_outcome_summary()
    total, resolved, wins = summary["total"], summary["resolved"], summary["wins"]
    pending = total - resolved
    stats = db.whale_signal_stats()

    min_sig = CONFIG.scout.prune_min_signals
    min_share = CONFIG.scout.prune_min_winshare

    lines = [
        "📊 <b>КАЛИБРОВКА ПЕТЛИ (P1/P2)</b>",
        f"Сигналов всего: {total} | разрешено: {resolved} | ждут: {pending}",
    ]
    if resolved:
        lines.append(f"Общая правота китов: {wins}/{resolved} = {wins / resolved * 100:.0f}%")
    else:
        lines.append("⚠️ Разрешённых сигналов пока нет — петля ещё набирает данные.")

    # Киты с накопленной статистикой (>=2 разрешённых), худшие сверху
    scored = [
        (addr, s["wins"], s["resolved"], s["wins"] / s["resolved"])
        for addr, s in stats.items() if s["resolved"] >= 2
    ]
    scored.sort(key=lambda x: (x[3], -x[2]))

    candidates = [
        x for x in scored if x[2] >= min_sig and x[3] < min_share
    ]

    if candidates:
        lines.append(f"\n🗑 <b>Кандидаты на прунинг</b> (≥{min_sig} сигналов, правота &lt;{min_share:.0%}):")
        for addr, w, r, share in candidates:
            who = (db.get_whale(addr) or {}).get("pseudonym") or addr[:10]
            lines.append(f"  • {who}: {w}/{r} ({share:.0%})")
    elif scored:
        lines.append(f"\n✅ Кандидатов на прунинг нет (порог: ≥{min_sig} сигналов и &lt;{min_share:.0%}).")

    if scored:
        lines.append("\n<b>Топ-5 китов по правоте:</b>")
        for addr, w, r, share in sorted(scored, key=lambda x: (-x[3], -x[2]))[:5]:
            who = (db.get_whale(addr) or {}).get("pseudonym") or addr[:10]
            lines.append(f"  • {who}: {w}/{r} ({share:.0%})")

    # Готовность к калибровке
    lines.append("")
    if resolved >= 30:
        lines.append(
            "🎯 Данных достаточно (≥30 разрешённых). Пора калибровать по "
            "docs/improvement-plan.md:\n"
            "  P1 — пороги китов: min_winrate 0.55→0.65, min_total_pnl $1k→$25k, "
            "lb_min_winrate 0.50→0.60; prune_min_winshare 0.40→0.50\n"
            "  P2 — выходы: SL −15c→−10c (симметрия), флип +5c→+8c или off, трейлинг вместо TP"
        )
    else:
        lines.append(
            f"⏳ Разрешено {resolved}/30 — данных для уверенной калибровки P1/P2 ещё мало. "
            "Подождать ещё пару дней и перезапустить проверку."
        )
    return "\n".join(lines)


def main() -> None:
    report = build_report()
    print(report)
    notifier = Notifier()
    notifier.send(report)
    notifier.flush()


if __name__ == "__main__":
    main()
