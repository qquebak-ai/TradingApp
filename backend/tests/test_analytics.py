"""Statistics: the numbers a trader would recompute by hand to check us."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.analytics.breakdowns import by_symbol, by_time
from backend.app.analytics.core import equity_curve, max_drawdown, streaks, summary
from backend.app.analytics.risk import (
    holding_bias,
    overtrading,
    r_multiples,
    revenge_trading,
    sizing_consistency,
    stop_discipline,
    stop_usage,
)
from backend.app.models import Trade

BASE = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)


def make(net, *, minutes=60, symbol="EURUSD", side="buy", volume=0.1, sl=None,
         day_offset=0, hour=None, ticket=None, open_price=1.10):
    """A trade whose net P&L is exactly `net`, with prices that stay consistent."""
    open_time = BASE + timedelta(days=day_offset)
    if hour is not None:
        open_time = open_time.replace(hour=hour)
    # 1000 units of money per 1.0 of price move keeps risk_money() arithmetic simple.
    close_price = open_price + net / 1000.0 * (1 if side == "buy" else -1)
    return Trade(
        ticket=ticket or f"t{abs(hash((net, day_offset, hour, symbol, minutes))) % 10**7}",
        symbol=symbol, side=side, volume=volume,
        open_time=open_time, close_time=open_time + timedelta(minutes=minutes),
        open_price=open_price, close_price=close_price,
        profit=net, sl=sl,
    )


def test_summary_matches_hand_computed_figures():
    trades = [make(100), make(-50), make(200), make(-25), make(0)]
    s = summary(trades, 1000.0)

    assert s["trades"] == 5
    assert s["wins"] == 2 and s["losses"] == 2 and s["breakeven"] == 1
    assert s["win_rate"] == 40.0  # a flat trade counts in the denominator, not as a win
    assert s["net_profit"] == pytest.approx(225.0)
    assert s["gross_profit"] == pytest.approx(300.0)
    assert s["gross_loss"] == pytest.approx(75.0)
    assert s["profit_factor"] == pytest.approx(4.0)
    assert s["expectancy"] == pytest.approx(45.0)
    assert s["payoff_ratio"] == pytest.approx(150.0 / 37.5)
    assert s["closing_balance"] == pytest.approx(1225.0)
    assert s["return_pct"] == pytest.approx(22.5)


def test_profit_factor_is_none_when_nothing_was_lost():
    assert summary([make(10), make(20)], 1000.0)["profit_factor"] is None


def test_equity_curve_and_drawdown_track_the_peak():
    trades = [make(100), make(-40), make(-30), make(50)]
    curve = equity_curve(trades, 1000.0)
    assert [p["equity"] for p in curve] == [1100.0, 1060.0, 1030.0, 1080.0]

    drawdown = max_drawdown(curve)
    assert drawdown["abs"] == pytest.approx(70.0)
    assert drawdown["pct"] == pytest.approx(70.0 / 1100.0 * 100, rel=1e-3)
    assert drawdown["recovered"] is False  # 1080 never regains the 1100 peak


def test_drawdown_marks_recovery_when_the_peak_returns():
    curve = equity_curve([make(100), make(-40), make(60)], 1000.0)
    drawdown = max_drawdown(curve)
    assert drawdown["recovered"] is True
    assert drawdown["trades_to_recover"] == 1


def test_streaks_count_runs_in_close_order():
    result = streaks([make(1), make(2), make(-1), make(-2), make(-3), make(4)])
    assert result["longest_wins"] == 2
    assert result["longest_losses"] == 3
    assert result["current_kind"] == "win" and result["current"] == 1


def test_costs_are_part_of_net_profit():
    trade = Trade(
        ticket="1", symbol="EURUSD", side="buy", volume=1.0,
        open_time=BASE, close_time=BASE + timedelta(hours=1),
        open_price=1.1, close_price=1.11,
        profit=100.0, commission=-7.0, swap=-2.0, fee=-1.0,
    )
    assert trade.net == pytest.approx(90.0)
    assert summary([trade], 1000.0)["costs"] == pytest.approx(-10.0)


def test_symbol_breakdown_splits_profit_and_loss_shares():
    trades = [make(100, symbol="EURUSD"), make(-60, symbol="XAUUSD"), make(20, symbol="EURUSD")]
    report = by_symbol(trades)
    rows = {r["label"]: r for r in report["rows"]}
    assert rows["EURUSD"]["net"] == pytest.approx(120.0)
    assert rows["EURUSD"]["share_of_profit"] == 100.0
    assert rows["XAUUSD"]["share_of_loss"] == 100.0
    assert report["rows"][0]["label"] == "EURUSD"  # sorted by net, best first


def test_time_breakdown_keeps_empty_buckets():
    trades = [make(50, hour=10), make(-20, hour=10), make(40, hour=15)]
    report = by_time(trades)
    assert len(report["hour"]) == 24
    assert len(report["weekday"]) == 7
    hours = {r["label"]: r for r in report["hour"]}
    assert hours["10:00"]["trades"] == 2 and hours["10:00"]["net"] == pytest.approx(30.0)
    assert hours["03:00"]["trades"] == 0
    assert report["best_hour"]["label"] == "15:00"


def test_r_multiple_uses_the_stop_distance_for_risk():
    # 0.01 of price is worth 100 of money here, and the stop sits 0.01 away.
    trade = make(200, sl=1.09, open_price=1.10)
    assert trade.risk_money() == pytest.approx(10.0, rel=1e-6)

    report = r_multiples([trade])
    assert report["sample_size"] == 1
    assert report["expectancy_r"] == pytest.approx(20.0, rel=1e-3)


def test_r_statistics_ignore_trades_without_a_stop():
    report = r_multiples([make(100), make(-50, sl=1.09)])
    assert report["sample_size"] == 1
    assert report["coverage_pct"] == 50.0


def test_stop_usage_separates_protected_from_unprotected_trades():
    report = stop_usage([make(100, sl=1.09), make(-300)])
    assert report["with_sl"] == 1 and report["without_sl"] == 1
    assert report["sl_coverage_pct"] == 50.0
    assert report["net_without_sl"] == pytest.approx(-300.0)
    assert report["worst_without_sl"] == pytest.approx(-300.0)


def test_stop_discipline_flags_losses_beyond_the_plan():
    # Risk 10, loss 25 — well past the 20% tolerance.
    overrun = make(-250, sl=1.09, open_price=1.10)
    inside = make(-9, sl=1.09, open_price=1.10)
    report = stop_discipline([overrun, inside])
    assert report["losses_checked"] == 2
    assert report["overruns"] == 1
    assert report["worst"][0]["ticket"] == overrun.ticket


def test_sizing_verdict_reacts_to_scattered_risk():
    steady = [make(10, sl=1.09, volume=0.1) for _ in range(6)]
    assert sizing_consistency(steady)["verdict"] == "consistent"

    scattered = [
        make(10, sl=1.09, open_price=1.10),
        make(10, sl=1.05, open_price=1.10),
        make(10, sl=1.30, open_price=1.10, side="sell"),
    ]
    assert sizing_consistency(scattered)["verdict"] in ("variable", "erratic")


def test_holding_bias_detects_nursing_losers():
    trades = [make(100, minutes=30), make(-100, minutes=600)]
    report = holding_bias(trades)
    assert report["loss_to_win_ratio"] == pytest.approx(20.0)
    assert report["holds_losers_longer"] is True


def test_overtrading_compares_busy_days_with_ordinary_ones():
    trades = []
    for day in range(6):                       # six quiet days, one trade each
        trades.append(make(10, day_offset=day, hour=9))
    for index in range(8):                     # one blowout day
        trades.append(make(-20, day_offset=10, hour=9 + index % 8))
    report = overtrading(trades)
    assert report["median_trades_per_day"] == 1
    assert report["busy_day_count"] == 1
    assert report["hurts"] is True


def test_revenge_trading_needs_both_size_and_proximity():
    loss = make(-100, day_offset=0, hour=9, minutes=30, volume=0.1)
    # Opens 10 minutes after the loss closed, at triple the median size.
    revenge = Trade(
        ticket="revenge", symbol="EURUSD", side="buy", volume=0.3,
        open_time=loss.close_time + timedelta(minutes=10),
        close_time=loss.close_time + timedelta(minutes=70),
        open_price=1.10, close_price=1.05, profit=-500.0,
    )
    calm = make(50, day_offset=3, hour=9, volume=0.1)

    report = revenge_trading([loss, revenge, calm])
    assert report["count"] == 1
    assert report["examples"][0]["ticket"] == "revenge"
    assert report["hurts"] is True


def test_revenge_ignores_a_normal_sized_trade_after_a_loss():
    loss = make(-100, day_offset=0, hour=9, minutes=30, volume=0.1)
    follow_up = Trade(
        ticket="calm", symbol="EURUSD", side="buy", volume=0.1,
        open_time=loss.close_time + timedelta(minutes=5),
        close_time=loss.close_time + timedelta(minutes=45),
        open_price=1.10, close_price=1.11, profit=100.0,
    )
    assert revenge_trading([loss, follow_up])["count"] == 0


def test_empty_history_does_not_explode():
    s = summary([], 1000.0)
    assert s["trades"] == 0 and s["net_profit"] == 0.0
    assert max_drawdown([])["abs"] == 0.0
    assert overtrading([])["busy_days"] == []
    assert revenge_trading([])["count"] == 0
