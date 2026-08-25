"""Shared bucket statistics — every breakdown in the app reduces to this."""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Hashable, Sequence

from ..models import Trade
from .core import _mean, _safe_div


def bucket_stats(trades: Sequence[Trade], label: str) -> dict:
    """The per-group numbers shared by the symbol, time and side breakdowns."""
    wins = [t.net for t in trades if t.net > 0]
    losses = [t.net for t in trades if t.net < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(t.net for t in trades)
    return {
        "label": label,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / len(trades), 2) if trades else 0.0,
        "net": round(net, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "costs": round(sum(t.costs for t in trades), 2),
        "profit_factor": (
            round(pf, 2) if (pf := _safe_div(gross_profit, gross_loss)) is not None else None
        ),
        "expectancy": round(_safe_div(net, len(trades)) or 0.0, 2),
        "avg_win": round(_mean(wins), 2),
        "avg_loss": round(_mean(losses), 2),
        "volume": round(sum(t.volume for t in trades), 2),
        "best": round(max((t.net for t in trades), default=0.0), 2),
        "worst": round(min((t.net for t in trades), default=0.0), 2),
        "avg_duration_minutes": round(_mean([t.duration_minutes for t in trades]), 1),
    }


def group_by(
    trades: Sequence[Trade],
    key: Callable[[Trade], Hashable],
    label: Callable[[Hashable], str] = str,
    order: Sequence[Hashable] | None = None,
) -> list[dict]:
    """Bucket trades by `key` and describe each bucket.

    `order` fixes the output sequence and keeps empty buckets, which is what the
    weekday and hour views need — a missing Friday should read as zero, not vanish.
    """
    buckets: OrderedDict[Hashable, list[Trade]] = OrderedDict()
    if order is not None:
        for value in order:
            buckets[value] = []
    for trade in trades:
        buckets.setdefault(key(trade), []).append(trade)

    rows = []
    for value, group in buckets.items():
        row = bucket_stats(group, label(value))
        row["key"] = value if isinstance(value, (str, int, float)) else str(value)
        rows.append(row)
    return rows


def add_share_of_net(rows: Sequence[dict]) -> list[dict]:
    """Each bucket's contribution to total profit and to total loss, as percentages.

    Split by sign on purpose: telling a trader that one symbol is 140% of net P&L
    (because another is bleeding) hides the thing they need to see.
    """
    total_positive = sum(r["net"] for r in rows if r["net"] > 0)
    total_negative = abs(sum(r["net"] for r in rows if r["net"] < 0))
    for row in rows:
        if row["net"] > 0 and total_positive > 0:
            row["share_of_profit"] = round(100.0 * row["net"] / total_positive, 2)
            row["share_of_loss"] = 0.0
        elif row["net"] < 0 and total_negative > 0:
            row["share_of_profit"] = 0.0
            row["share_of_loss"] = round(100.0 * abs(row["net"]) / total_negative, 2)
        else:
            row["share_of_profit"] = 0.0
            row["share_of_loss"] = 0.0
    return list(rows)
