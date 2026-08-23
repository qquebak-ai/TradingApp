"""Агент для Windows: что именно он отправляет на дашборд."""

from argparse import Namespace
from datetime import datetime, timedelta, timezone

import pytest

import tools.mt5_agent as agent
from backend.app.models import Account, ImportResult, OpenPosition, Trade

BASE = datetime(2025, 6, 1, tzinfo=timezone.utc)


def make_result(count=40):
    trades = [
        Trade(ticket=str(i), symbol="EURUSD", side="buy", volume=0.1,
              open_time=BASE + timedelta(days=i),
              close_time=BASE + timedelta(days=i, hours=2),
              open_price=1.10, close_price=1.101, profit=10.0, sl=1.09)
        for i in range(count)
    ]
    return ImportResult(
        source="mt5_live", trades=trades, trades_seen=len(trades),
        account=Account(login="9001", currency="USD", balance=10_400.0),
        open_positions=[OpenPosition(ticket="p1", symbol="XAUUSD", side="sell",
                                     volume=0.2, open_time=BASE, open_price=2300.0)],
    )


@pytest.fixture()
def args():
    return Namespace(url="http://dashboard.test", token="tok", login=None, password=None,
                     server=None, terminal=None, days=3650, interval=0)


@pytest.fixture()
def captured(monkeypatch):
    """Подменяем терминал и сеть, запоминая, что агент собирался отправить."""
    sent = []
    monkeypatch.setattr(agent, "sync", lambda settings: make_result())
    monkeypatch.setattr(
        agent, "push",
        lambda url, token, result, trades: sent.append(list(trades))
        or {"trades_added": len(trades), "trades_updated": 0},
    )
    return sent


def test_first_pass_uploads_the_whole_history(args, captured):
    assert agent.run_once(args, {}) == 0
    assert len(captured[0]) == 40


def test_later_passes_send_only_the_recent_tail(args, captured):
    high_water: dict = {}
    agent.run_once(args, high_water)
    agent.run_once(args, high_water)

    # Второй проход шлёт только окно перекрытия: своп начисляется задним числом,
    # поэтому недавние сделки пересылаются, а старые — нет.
    assert len(captured[1]) < len(captured[0])
    cutoff = high_water["close_time"] - timedelta(days=agent.OVERLAP_DAYS)
    assert all(t.close_time >= cutoff for t in captured[1])
    assert captured[1][-1].ticket == "39"


def test_high_water_mark_follows_the_newest_close(args, captured):
    high_water: dict = {}
    agent.run_once(args, high_water)
    assert high_water["close_time"] == BASE + timedelta(days=39, hours=2)


def test_a_terminal_that_is_not_running_is_reported_not_crashed(args, monkeypatch):
    def unavailable(settings):
        raise agent.MT5Unavailable("терминал не отвечает")

    monkeypatch.setattr(agent, "sync", unavailable)
    assert agent.run_once(args, {}) == 2  # ненулевой код, без исключения наружу


def test_a_dashboard_that_is_down_does_not_kill_the_agent(args, monkeypatch):
    monkeypatch.setattr(agent, "sync", lambda settings: make_result(3))

    def refused(url, token, result, trades):
        raise OSError("connection refused")

    monkeypatch.setattr(agent, "push", refused)
    assert agent.run_once(args, {}) == 4
