"""Risk and discipline: position sizing, R-multiples, stop handling, overtrading.

Money-risk figures need an initial stop-loss on the trade. MT4/MT5 report the stop
that was set when the position closed, so a stop moved to breakeven or trailed will
understate the original risk; the coverage counters below make it visible how many
trades the R-statistics actually rest on.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import timedelta
from typing import Optional, Sequence

from ..models import Trade
from .core import _mean, _safe_div, _stdev

REVENGE_WINDOW = timedelta(minutes=30)
REVENGE_SIZE_FACTOR = 1.5
OVERTRADE_FACTOR = 2.0
STOP_OVERRUN_TOLERANCE = 1.2  # a loss beyond 120% of planned risk means the stop slipped


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def stop_usage(trades: Sequence[Trade]) -> dict:
    """How often a stop was in place, and what it was worth in P&L terms."""
    with_sl = [t for t in trades if t.sl not in (None, 0)]
    without_sl = [t for t in trades if t.sl in (None, 0)]
    with_tp = [t for t in trades if t.tp not in (None, 0)]
    return {
        "trades": len(trades),
        "with_sl": len(with_sl),
        "without_sl": len(without_sl),
        "sl_coverage_pct": round(100.0 * len(with_sl) / len(trades), 2) if trades else 0.0,
        "tp_coverage_pct": round(100.0 * len(with_tp) / len(trades), 2) if trades else 0.0,
        "net_with_sl": round(sum(t.net for t in with_sl), 2),
        "net_without_sl": round(sum(t.net for t in without_sl), 2),
        "avg_net_with_sl": round(_mean([t.net for t in with_sl]), 2),
        "avg_net_without_sl": round(_mean([t.net for t in without_sl]), 2),
        "worst_without_sl": round(min((t.net for t in without_sl), default=0.0), 2),
    }


def r_multiples(trades: Sequence[Trade]) -> dict:
    """Each trade's outcome expressed in units of the risk it took on.

    R = net P&L / money at risk when the stop was placed. Only trades that carry a
    usable stop take part; `coverage_pct` says how much of the history that is.
    """
    samples = []
    for trade in trades:
        risk = trade.risk_money()
        if risk and risk > 0:
            samples.append({"ticket": trade.ticket, "symbol": trade.symbol,
                            "r": trade.net / risk, "risk": risk, "net": trade.net,
                            "close_time": trade.close_time.isoformat()})

    values = [s["r"] for s in samples]
    histogram_edges = [-3, -2, -1, 0, 1, 2, 3]
    histogram = _r_histogram(values, histogram_edges)

    return {
        "sample_size": len(samples),
        "coverage_pct": round(100.0 * len(samples) / len(trades), 2) if trades else 0.0,
        "expectancy_r": round(_mean(values), 3) if values else None,
        "avg_win_r": round(_mean([v for v in values if v > 0]), 3) if values else None,
        "avg_loss_r": round(_mean([v for v in values if v < 0]), 3) if values else None,
        "best_r": round(max(values), 2) if values else None,
        "worst_r": round(min(values), 2) if values else None,
        "histogram": histogram,
        "worst_trades": sorted(samples, key=lambda s: s["r"])[:5],
        "best_trades": sorted(samples, key=lambda s: s["r"], reverse=True)[:5],
    }


def _r_histogram(values: Sequence[float], edges: Sequence[float]) -> list[dict]:
    bins = [{"label": f"< {edges[0]}R", "count": 0}]
    for low, high in zip(edges, edges[1:]):
        bins.append({"label": f"{low}R…{high}R", "count": 0})
    bins.append({"label": f"> {edges[-1]}R", "count": 0})

    for value in values:
        if value < edges[0]:
            bins[0]["count"] += 1
        elif value >= edges[-1]:
            bins[-1]["count"] += 1
        else:
            for index, (low, high) in enumerate(zip(edges, edges[1:]), start=1):
                if low <= value < high:
                    bins[index]["count"] += 1
                    break
    return bins


def sizing_consistency(trades: Sequence[Trade]) -> dict:
    """Whether position size and money risk stay level, or jump around.

    The coefficient of variation (stdev / mean) is the headline: under ~0.25 the
    sizing is effectively fixed, above ~0.75 the account is being sized by feel.
    """
    volumes = [t.volume for t in trades]
    risks = [r for t in trades if (r := t.risk_money()) and r > 0]

    volume_cv = _safe_div(_stdev(volumes), _mean(volumes))
    risk_cv = _safe_div(_stdev(risks), _mean(risks))

    oversized = []
    if risks:
        threshold = _median(risks) * REVENGE_SIZE_FACTOR
        for trade in trades:
            risk = trade.risk_money()
            if risk and risk > threshold:
                oversized.append(
                    {"ticket": trade.ticket, "symbol": trade.symbol,
                     "risk": round(risk, 2), "net": round(trade.net, 2),
                     "close_time": trade.close_time.isoformat()}
                )

    return {
        "avg_volume": round(_mean(volumes), 3),
        "median_volume": round(_median(volumes), 3),
        "max_volume": round(max(volumes), 3) if volumes else 0.0,
        "volume_cv": round(volume_cv, 3) if volume_cv is not None else None,
        "risk_sample_size": len(risks),
        "avg_risk": round(_mean(risks), 2),
        "median_risk": round(_median(risks), 2),
        "max_risk": round(max(risks), 2) if risks else 0.0,
        "risk_p90": round(_percentile(risks, 90), 2),
        "risk_cv": round(risk_cv, 3) if risk_cv is not None else None,
        "oversized_trades": sorted(oversized, key=lambda o: o["risk"], reverse=True)[:10],
        "oversized_net": round(sum(o["net"] for o in oversized), 2),
        "verdict": _sizing_verdict(risk_cv if risk_cv is not None else volume_cv),
    }


def _sizing_verdict(cv: Optional[float]) -> str:
    if cv is None:
        return "unknown"
    if cv < 0.25:
        return "consistent"
    if cv < 0.75:
        return "variable"
    return "erratic"


def stop_discipline(trades: Sequence[Trade]) -> dict:
    """Losses that ran past the planned risk — slippage, gaps, or a widened stop."""
    overruns = []
    checked = 0
    for trade in trades:
        risk = trade.risk_money()
        if not risk or risk <= 0 or trade.net >= 0:
            continue
        checked += 1
        if abs(trade.net) > risk * STOP_OVERRUN_TOLERANCE:
            overruns.append(
                {
                    "ticket": trade.ticket,
                    "symbol": trade.symbol,
                    "planned_risk": round(risk, 2),
                    "actual_loss": round(abs(trade.net), 2),
                    "overrun_pct": round(100.0 * (abs(trade.net) / risk - 1), 1),
                    "close_time": trade.close_time.isoformat(),
                }
            )
    return {
        "losses_checked": checked,
        "overruns": len(overruns),
        "overrun_pct": round(100.0 * len(overruns) / checked, 2) if checked else 0.0,
        "excess_loss": round(
            sum(o["actual_loss"] - o["planned_risk"] for o in overruns), 2
        ),
        "worst": sorted(overruns, key=lambda o: o["overrun_pct"], reverse=True)[:5],
    }


def holding_bias(trades: Sequence[Trade]) -> dict:
    """The classic asymmetry: cutting winners early while letting losers run."""
    win_durations = [t.duration_minutes for t in trades if t.net > 0]
    loss_durations = [t.duration_minutes for t in trades if t.net < 0]
    ratio = _safe_div(_mean(loss_durations), _mean(win_durations))
    return {
        "avg_win_minutes": round(_mean(win_durations), 1),
        "avg_loss_minutes": round(_mean(loss_durations), 1),
        "median_win_minutes": round(_median(win_durations), 1),
        "median_loss_minutes": round(_median(loss_durations), 1),
        "loss_to_win_ratio": round(ratio, 2) if ratio is not None else None,
        "holds_losers_longer": bool(ratio and ratio > 1.25),
    }


def overtrading(trades: Sequence[Trade]) -> dict:
    """Days with an unusual number of trades, and what they did to the P&L."""
    per_day: dict = defaultdict(list)
    for trade in trades:
        per_day[trade.close_time.date()].append(trade)

    counts = [len(day_trades) for day_trades in per_day.values()]
    if not counts:
        return {"median_trades_per_day": 0, "threshold": 0, "busy_days": [],
                "busy_day_net": 0.0, "normal_day_net": 0.0, "busy_day_avg": 0.0,
                "normal_day_avg": 0.0, "hurts": False}

    median_count = _median(counts)
    threshold = max(median_count * OVERTRADE_FACTOR, median_count + 1)

    busy, normal = [], []
    for day, day_trades in sorted(per_day.items()):
        entry = {
            "date": day.isoformat(),
            "trades": len(day_trades),
            "net": round(sum(t.net for t in day_trades), 2),
        }
        (busy if len(day_trades) >= threshold else normal).append(entry)

    busy_net = sum(d["net"] for d in busy)
    normal_net = sum(d["net"] for d in normal)
    busy_avg = _mean([d["net"] for d in busy])
    normal_avg = _mean([d["net"] for d in normal])

    return {
        "median_trades_per_day": round(median_count, 1),
        "threshold": round(threshold, 1),
        "busy_days": sorted(busy, key=lambda d: d["trades"], reverse=True)[:10],
        "busy_day_count": len(busy),
        "busy_day_net": round(busy_net, 2),
        "normal_day_net": round(normal_net, 2),
        "busy_day_avg": round(busy_avg, 2),
        "normal_day_avg": round(normal_avg, 2),
        "hurts": bool(busy and busy_avg < normal_avg),
    }


def revenge_trading(trades: Sequence[Trade]) -> dict:
    """Oversized entries opened right after a loss closed.

    A trade counts when it opens within 30 minutes of a losing trade closing and
    carries at least 1.5× the median size — the mechanical signature of trying to
    win the money straight back.
    """
    if not trades:
        return {"count": 0, "net": 0.0, "avg_net": 0.0, "win_rate": 0.0,
                "baseline_avg_net": 0.0, "examples": [], "hurts": False}

    median_volume = _median([t.volume for t in trades])
    by_open = sorted(trades, key=lambda t: t.open_time)
    losses_by_close = sorted(
        (t for t in trades if t.net < 0), key=lambda t: t.close_time
    )

    loss_close_times = [t.close_time for t in losses_by_close]

    flagged, baseline = [], []
    for trade in by_open:
        # Nearest loss that had already closed when this trade was opened.
        index = bisect_right(loss_close_times, trade.open_time) - 1
        while index >= 0 and losses_by_close[index].ticket == trade.ticket:
            index -= 1
        prior_loss = losses_by_close[index] if index >= 0 else None
        is_revenge = (
            prior_loss is not None
            and trade.open_time - prior_loss.close_time <= REVENGE_WINDOW
            and trade.volume >= median_volume * REVENGE_SIZE_FACTOR
        )
        (flagged if is_revenge else baseline).append(trade)

    wins = sum(1 for t in flagged if t.net > 0)
    flagged_avg = _mean([t.net for t in flagged])
    baseline_avg = _mean([t.net for t in baseline])

    return {
        "count": len(flagged),
        "net": round(sum(t.net for t in flagged), 2),
        "avg_net": round(flagged_avg, 2),
        "win_rate": round(100.0 * wins / len(flagged), 2) if flagged else 0.0,
        "baseline_avg_net": round(baseline_avg, 2),
        "median_volume": round(median_volume, 3),
        "examples": [
            {
                "ticket": t.ticket,
                "symbol": t.symbol,
                "volume": t.volume,
                "net": round(t.net, 2),
                "open_time": t.open_time.isoformat(),
            }
            for t in sorted(flagged, key=lambda t: t.net)[:5]
        ],
        "hurts": bool(flagged and flagged_avg < baseline_avg),
    }


def risk_report(trades: Sequence[Trade]) -> dict:
    """The whole discipline section, plus plain-language findings for the UI."""
    report = {
        "stops": stop_usage(trades),
        "r_multiples": r_multiples(trades),
        "sizing": sizing_consistency(trades),
        "stop_discipline": stop_discipline(trades),
        "holding": holding_bias(trades),
        "overtrading": overtrading(trades),
        "revenge": revenge_trading(trades),
    }
    report["findings"] = build_findings(report)
    return report


def build_findings(report: dict) -> list[dict]:
    """Turn the numbers into a short list of things worth acting on."""
    findings: list[dict] = []

    stops = report["stops"]
    if stops["trades"] and stops["sl_coverage_pct"] < 80:
        findings.append(
            {
                "level": "warn" if stops["sl_coverage_pct"] >= 50 else "bad",
                "title": "Стоп-лосс стоит не везде",
                "detail": (
                    f"SL был выставлен в {stops['sl_coverage_pct']}% сделок. "
                    f"Без стопа: {stops['without_sl']} сделок, суммарно "
                    f"{stops['net_without_sl']}, худшая {stops['worst_without_sl']}."
                ),
            }
        )

    sizing = report["sizing"]
    if sizing["verdict"] == "erratic":
        findings.append(
            {
                "level": "bad",
                "title": "Риск на сделку скачет",
                "detail": (
                    f"Коэффициент вариации риска {sizing['risk_cv']} — размер позиции "
                    f"выбирается на глаз. Медианный риск {sizing['median_risk']}, "
                    f"максимальный {sizing['max_risk']}."
                ),
            }
        )
    elif sizing["verdict"] == "variable":
        findings.append(
            {
                "level": "warn",
                "title": "Размер риска нестабилен",
                "detail": (
                    f"Коэффициент вариации риска {sizing['risk_cv']}. Крупные сделки "
                    f"(риск выше медианы в 1.5 раза) дали {sizing['oversized_net']}."
                ),
            }
        )

    discipline = report["stop_discipline"]
    if discipline["overruns"]:
        findings.append(
            {
                "level": "warn",
                "title": "Убытки выходят за плановый риск",
                "detail": (
                    f"{discipline['overruns']} из {discipline['losses_checked']} убыточных "
                    f"сделок закрылись хуже плана; лишний убыток "
                    f"{discipline['excess_loss']}."
                ),
            }
        )

    holding = report["holding"]
    if holding["holds_losers_longer"]:
        findings.append(
            {
                "level": "warn",
                "title": "Убытки держатся дольше прибыли",
                "detail": (
                    f"Средний убыток живёт {holding['avg_loss_minutes']} мин, прибыль — "
                    f"{holding['avg_win_minutes']} мин "
                    f"(в {holding['loss_to_win_ratio']} раза дольше)."
                ),
            }
        )

    over = report["overtrading"]
    if over["hurts"]:
        findings.append(
            {
                "level": "warn",
                "title": "Перебор со сделками вредит",
                "detail": (
                    f"В дни от {over['threshold']} сделок средний результат "
                    f"{over['busy_day_avg']} против {over['normal_day_avg']} "
                    f"в обычные дни ({over['busy_day_count']} таких дней)."
                ),
            }
        )

    revenge = report["revenge"]
    if revenge["hurts"]:
        findings.append(
            {
                "level": "bad",
                "title": "Похоже на отыгрыш после убытка",
                "detail": (
                    f"{revenge['count']} увеличенных входов в течение 30 минут после "
                    f"убыточной сделки: суммарно {revenge['net']}, средняя "
                    f"{revenge['avg_net']} против {revenge['baseline_avg_net']} "
                    f"по остальным."
                ),
            }
        )

    r_stats = report["r_multiples"]
    if r_stats["sample_size"] and r_stats["expectancy_r"] is not None:
        findings.append(
            {
                "level": "good" if r_stats["expectancy_r"] > 0 else "bad",
                "title": f"Матожидание {r_stats['expectancy_r']}R на сделку",
                "detail": (
                    f"Посчитано по {r_stats['sample_size']} сделкам со стопом "
                    f"({r_stats['coverage_pct']}% истории). Средняя прибыль "
                    f"{r_stats['avg_win_r']}R, средний убыток {r_stats['avg_loss_r']}R."
                ),
            }
        )

    if not findings:
        findings.append(
            {
                "level": "good",
                "title": "Явных проблем с дисциплиной не видно",
                "detail": "Стопы на месте, риск ровный, перебора со сделками нет.",
            }
        )
    return findings
