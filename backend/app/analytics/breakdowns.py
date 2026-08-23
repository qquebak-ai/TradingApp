"""Instrument and time breakdowns: which symbols pay, and when you trade best."""

from __future__ import annotations

from typing import Sequence

from ..models import Trade
from .core import daily_pnl
from .grouping import add_share_of_net, group_by

WEEKDAYS = list(range(7))  # Monday = 0, matching datetime.weekday()
WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
HOURS = list(range(24))

DURATION_BUCKETS = (
    (0, 5, "< 5 мин"),
    (5, 30, "5–30 мин"),
    (30, 120, "30 мин – 2 ч"),
    (120, 480, "2–8 ч"),
    (480, 1440, "8–24 ч"),
    (1440, 10080, "1–7 дней"),
    (10080, float("inf"), "> 7 дней"),
)


def by_symbol(trades: Sequence[Trade], limit: int = 0) -> dict:
    """Per-instrument performance, plus the best and worst few by net P&L."""
    rows = add_share_of_net(group_by(trades, key=lambda t: t.symbol))
    rows.sort(key=lambda r: r["net"], reverse=True)
    ranked = [r for r in rows if r["trades"] > 0]
    # With few instruments the two lists would otherwise overlap and every row
    # would read as "best", including the losing ones.
    half = len(ranked) // 2
    top = min(3, max(1, half)) if len(ranked) > 1 else len(ranked)
    return {
        "rows": rows[:limit] if limit else rows,
        "best": ranked[:top],
        "worst": list(reversed(ranked[len(ranked) - top:])) if len(ranked) > 1 else [],
        "by_side": group_by(trades, key=lambda t: t.side, order=["buy", "sell"]),
    }


def _duration_label(minutes: float) -> str:
    for low, high, label in DURATION_BUCKETS:
        if low <= minutes < high:
            return label
    return DURATION_BUCKETS[-1][2]


def by_time(trades: Sequence[Trade]) -> dict:
    """Weekday, hour, month, duration and day-by-day views of the same P&L.

    Every timestamp is the terminal's, which is broker server time — usually a few
    hours off UTC. The hour histogram is therefore a session profile relative to the
    broker's clock, and is labelled that way in the UI.
    """
    weekday_rows = group_by(
        trades,
        key=lambda t: t.open_time.weekday(),
        label=lambda k: WEEKDAY_NAMES[int(k)],
        order=WEEKDAYS,
    )
    hour_rows = group_by(
        trades,
        key=lambda t: t.open_time.hour,
        label=lambda k: f"{int(k):02d}:00",
        order=HOURS,
    )
    month_rows = group_by(
        trades,
        key=lambda t: t.close_time.strftime("%Y-%m"),
    )
    month_rows.sort(key=lambda r: r["label"])

    duration_order = [label for _, _, label in DURATION_BUCKETS]
    duration_rows = group_by(
        trades,
        key=lambda t: _duration_label(t.duration_minutes),
        order=duration_order,
    )

    daily = daily_pnl(trades)
    calendar_rows = [
        {"date": day.isoformat(), "net": round(pnl, 2)} for day, pnl in sorted(daily.items())
    ]

    return {
        "weekday": weekday_rows,
        "hour": hour_rows,
        "month": month_rows,
        "duration": duration_rows,
        "daily": calendar_rows,
        "best_weekday": _best(weekday_rows),
        "worst_weekday": _worst(weekday_rows),
        "best_hour": _best(hour_rows),
        "worst_hour": _worst(hour_rows),
    }


def _best(rows: Sequence[dict]) -> dict | None:
    active = [r for r in rows if r["trades"] > 0]
    return max(active, key=lambda r: r["net"]) if active else None


def _worst(rows: Sequence[dict]) -> dict | None:
    active = [r for r in rows if r["trades"] > 0]
    return min(active, key=lambda r: r["net"]) if active else None
