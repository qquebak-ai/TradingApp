"""Rebuilding MT5 positions from deal records, without a terminal present."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.app.ingest.mt5_live import MT5Unavailable, build_trades, sync, MT5Settings


def deal(**kwargs):
    base = dict(ticket=1, order=1, time=1_700_000_000, type=0, entry=0, magic=0,
                position_id=900, volume=1.0, price=1.10, commission=0.0, swap=0.0,
                profit=0.0, fee=0.0, symbol="EURUSD", comment="")
    base.update(kwargs)
    return SimpleNamespace(**base)


def order(**kwargs):
    base = dict(ticket=1, time_setup=1_700_000_000, position_id=900, sl=0.0, tp=0.0)
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_entry_and_exit_deals_fold_into_one_trade():
    deals = [
        deal(ticket=1, entry=0, type=0, price=1.1000, time=1_700_000_000, commission=-3.5),
        deal(ticket=2, entry=1, type=1, price=1.1050, time=1_700_003_600, commission=-3.5,
             profit=50.0, swap=-1.0),
    ]
    orders = [order(sl=1.0950, tp=1.1100)]

    trades = build_trades(deals, orders, "77001")
    assert len(trades) == 1

    trade = trades[0]
    assert trade.ticket == "900" and trade.side == "buy"
    assert trade.open_price == pytest.approx(1.1000)
    assert trade.close_price == pytest.approx(1.1050)
    assert trade.net == pytest.approx(50.0 - 7.0 - 1.0)
    assert trade.sl == pytest.approx(1.0950)  # taken from the entry order
    assert trade.close_time == datetime.fromtimestamp(1_700_003_600, tz=timezone.utc)


def test_partial_closes_average_the_exit_price_and_sum_the_money():
    deals = [
        deal(ticket=1, entry=0, type=0, volume=2.0, price=1.1000),
        deal(ticket=2, entry=1, type=1, volume=1.0, price=1.1100, profit=100.0,
             time=1_700_003_600),
        deal(ticket=3, entry=1, type=1, volume=1.0, price=1.1300, profit=300.0,
             time=1_700_007_200),
    ]
    trade = build_trades(deals, [], "77001")[0]
    assert trade.volume == pytest.approx(2.0)
    assert trade.close_price == pytest.approx(1.12)  # volume-weighted
    assert trade.profit == pytest.approx(400.0)
    assert trade.close_time == datetime.fromtimestamp(1_700_007_200, tz=timezone.utc)


def test_still_open_positions_are_not_reported_as_closed():
    assert build_trades([deal(entry=0)], [], "77001") == []


def test_balance_operations_carry_no_position_and_are_dropped():
    balance = deal(position_id=0, type=2, entry=0, profit=1000.0)
    assert build_trades([balance], [], "77001") == []


def test_sell_positions_keep_their_direction():
    deals = [
        deal(ticket=1, entry=0, type=1, price=1.2000),
        deal(ticket=2, entry=1, type=0, price=1.1900, profit=100.0, time=1_700_003_600),
    ]
    assert build_trades(deals, [], "77001")[0].side == "sell"


def test_sync_reports_a_clear_error_when_the_package_is_missing(monkeypatch):
    import backend.app.ingest.mt5_live as module

    def missing():
        raise MT5Unavailable("Пакет MetaTrader5 не установлен.")

    monkeypatch.setattr(module, "_import_mt5", missing)
    with pytest.raises(MT5Unavailable, match="MetaTrader5"):
        sync(MT5Settings())
