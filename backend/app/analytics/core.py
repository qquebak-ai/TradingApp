"""Headline P&L statistics and the equity curve everything else is measured against."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Iterable, Optional, Sequence

from ..models import Trade

TRADING_DAYS_PER_YEAR = 252


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    return numerator / denominator if abs(denominator) > 1e-12 else None


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: Sequence[float]) -> float:
    """Sample standard deviation; zero for fewer than two points."""
    if len(values) < 2:
        return 0.0
    mu = _mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / (len(values) - 1))


def starting_balance(trades: Sequence[Trade], current_balance: Optional[float]) -> float:
    """Balance the account began the period with.

    The terminal reports where the balance stands now, so we walk the realised P&L
    back off it. With no account synced we fall back to a nominal 10 000 so the
    percentage figures still have a scale — flagged as an estimate by the caller.
    """
    if current_balance is not None and current_balance > 0:
        return current_balance - sum(t.net for t in trades)
    return 10_000.0


def equity_curve(trades: Sequence[Trade], opening_balance: float) -> list[dict]:
    """Cumulative balance after every closed trade, with running drawdown."""
    points: list[dict] = []
    equity = opening_balance
    peak = opening_balance
    for trade in trades:
        equity += trade.net
        peak = max(peak, equity)
        drawdown = equity - peak
        points.append(
            {
                "ticket": trade.ticket,
                "close_time": trade.close_time.isoformat(),
                "symbol": trade.symbol,
                "net": round(trade.net, 2),
                "equity": round(equity, 2),
                "peak": round(peak, 2),
                "drawdown": round(drawdown, 2),
                "drawdown_pct": round(100.0 * drawdown / peak, 4) if peak > 0 else 0.0,
            }
        )
    return points


def max_drawdown(curve: Sequence[dict]) -> dict:
    """Deepest peak-to-trough decline on the closed-trade equity curve.

    This is a balance drawdown built from closed trades — it does not see intra-trade
    excursions, which only a tick-level history would show.
    """
    if not curve:
        return {"abs": 0.0, "pct": 0.0, "peak_time": None, "trough_time": None,
                "recovered": True, "trades_to_recover": 0}

    worst = min(curve, key=lambda p: p["drawdown"])
    trough_index = curve.index(worst)
    peak_time = None
    for point in reversed(curve[: trough_index + 1]):
        if abs(point["equity"] - point["peak"]) < 1e-9:
            peak_time = point["close_time"]
            break

    recovered = False
    trades_to_recover = 0
    for offset, point in enumerate(curve[trough_index + 1 :], start=1):
        if point["equity"] >= worst["peak"] - 1e-9:
            recovered = True
            trades_to_recover = offset
            break

    return {
        "abs": abs(worst["drawdown"]),
        "pct": abs(worst["drawdown_pct"]),
        "peak_time": peak_time,
        "trough_time": worst["close_time"],
        "recovered": recovered,
        "trades_to_recover": trades_to_recover,
    }


def streaks(trades: Sequence[Trade]) -> dict:
    """Longest and current runs of winners and losers, in close order."""
    longest_win = longest_loss = 0
    run_win = run_loss = 0
    for trade in trades:
        if trade.net > 0:
            run_win += 1
            run_loss = 0
        elif trade.net < 0:
            run_loss += 1
            run_win = 0
        else:
            run_win = run_loss = 0
        longest_win = max(longest_win, run_win)
        longest_loss = max(longest_loss, run_loss)

    current = 0
    current_kind = "none"
    for trade in reversed(trades):
        if trade.net > 0 and current_kind in ("none", "win"):
            current_kind, current = "win", current + 1
        elif trade.net < 0 and current_kind in ("none", "loss"):
            current_kind, current = "loss", current + 1
        else:
            break

    return {
        "longest_wins": longest_win,
        "longest_losses": longest_loss,
        "current": current,
        "current_kind": current_kind,
    }


def daily_pnl(trades: Iterable[Trade]) -> dict[date, float]:
    """Realised P&L keyed by the UTC date each trade closed on."""
    buckets: dict[date, float] = defaultdict(float)
    for trade in trades:
        buckets[trade.close_time.date()] += trade.net
    return dict(buckets)


def sharpe_ratio(trades: Sequence[Trade], opening_balance: float) -> Optional[float]:
    """Annualised Sharpe over daily returns, risk-free rate assumed zero.

    Only days that actually contain closed trades are counted, so a strategy that
    trades twice a month is not penalised for the silent days between.
    """
    if opening_balance <= 0:
        return None
    by_day = daily_pnl(trades)
    if len(by_day) < 2:
        return None
    returns = [pnl / opening_balance for _, pnl in sorted(by_day.items())]
    deviation = _stdev(returns)
    if deviation < 1e-12:
        return None
    return (_mean(returns) / deviation) * math.sqrt(TRADING_DAYS_PER_YEAR)


def summary(trades: Sequence[Trade], opening_balance: float) -> dict:
    """Every headline number the dashboard shows above the fold."""
    wins = [t for t in trades if t.net > 0]
    losses = [t for t in trades if t.net < 0]
    flat = [t for t in trades if t.net == 0]

    gross_profit = sum(t.net for t in wins)
    gross_loss = abs(sum(t.net for t in losses))
    net_profit = sum(t.net for t in trades)
    costs = sum(t.costs for t in trades)

    curve = equity_curve(trades, opening_balance)
    drawdown = max_drawdown(curve)
    by_day = daily_pnl(trades)
    winning_days = sum(1 for pnl in by_day.values() if pnl > 0)

    avg_win = _mean([t.net for t in wins])
    avg_loss = _mean([t.net for t in losses])

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(flat),
        "win_rate": round(100.0 * len(wins) / len(trades), 2) if trades else 0.0,
        "net_profit": round(net_profit, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "costs": round(costs, 2),
        "commission": round(sum(t.commission for t in trades), 2),
        "swap": round(sum(t.swap for t in trades), 2),
        "profit_factor": (
            round(pf, 2) if (pf := _safe_div(gross_profit, gross_loss)) is not None else None
        ),
        "expectancy": round(_safe_div(net_profit, len(trades)) or 0.0, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": (
            round(pr, 2) if (pr := _safe_div(avg_win, abs(avg_loss))) is not None else None
        ),
        "best_trade": round(max((t.net for t in trades), default=0.0), 2),
        "worst_trade": round(min((t.net for t in trades), default=0.0), 2),
        "total_volume": round(sum(t.volume for t in trades), 2),
        "avg_duration_minutes": round(_mean([t.duration_minutes for t in trades]), 1),
        "opening_balance": round(opening_balance, 2),
        "closing_balance": round(opening_balance + net_profit, 2),
        "return_pct": (
            round(100.0 * net_profit / opening_balance, 2) if opening_balance > 0 else None
        ),
        "max_drawdown": round(drawdown["abs"], 2),
        "max_drawdown_pct": round(drawdown["pct"], 2),
        "drawdown_recovered": drawdown["recovered"],
        "drawdown_peak_time": drawdown["peak_time"],
        "drawdown_trough_time": drawdown["trough_time"],
        "recovery_factor": (
            round(rf, 2)
            if (rf := _safe_div(net_profit, drawdown["abs"])) is not None
            else None
        ),
        "sharpe": (
            round(s, 2)
            if (s := sharpe_ratio(trades, opening_balance)) is not None
            else None
        ),
        "trading_days": len(by_day),
        "winning_days": winning_days,
        "day_win_rate": round(100.0 * winning_days / len(by_day), 2) if by_day else 0.0,
        "avg_trades_per_day": round(len(trades) / len(by_day), 2) if by_day else 0.0,
        "first_close": trades[0].close_time.isoformat() if trades else None,
        "last_close": trades[-1].close_time.isoformat() if trades else None,
        "streaks": streaks(trades),
    }
