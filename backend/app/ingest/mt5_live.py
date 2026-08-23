"""Live connector for the MetaTrader 5 terminal.

MetaTrader has no public REST API. The official way into a running account is the
`MetaTrader5` Python package, which talks to a terminal installed on the same
Windows machine. This module is import-safe everywhere; it only fails when a sync
is actually attempted without the package or the terminal.

MT5 stores history as *deals*, not positions: an entry deal and one or more exit
deals share a `position_id`. Closed positions are rebuilt from those groups here,
and the initial stop-loss is read from the entry *order*, since the deal records
do not carry one and the position's final SL may have been trailed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..models import Account, ImportResult, OpenPosition, Trade

DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2
DEAL_ENTRY_OUT_BY = 3

DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1

POSITION_TYPE_BUY = 0


class MT5Unavailable(RuntimeError):
    """Raised when the terminal or its Python bridge cannot be reached."""


@dataclass
class MT5Settings:
    login: Optional[int] = None
    password: Optional[str] = None
    server: Optional[str] = None
    terminal_path: Optional[str] = None
    history_days: int = 3650


def _import_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:
        raise MT5Unavailable(
            "Пакет MetaTrader5 не установлен. Он работает только на Windows рядом "
            "с установленным терминалом: pip install MetaTrader5. "
            "На других системах используй импорт отчёта из терминала."
        ) from exc
    return mt5


def _connect(mt5, settings: MT5Settings):
    kwargs: dict[str, Any] = {}
    if settings.terminal_path:
        kwargs["path"] = settings.terminal_path
    if settings.login:
        kwargs.update(login=int(settings.login), password=settings.password,
                      server=settings.server)
    if not mt5.initialize(**kwargs):
        code, message = mt5.last_error()
        raise MT5Unavailable(
            f"Терминал MT5 не отвечает (код {code}: {message}). Проверь, что терминал "
            "запущен и в его настройках включён «Алгоритмический трейдинг»."
        )


def _utc(timestamp: int) -> datetime:
    """MT5 timestamps are broker server time; kept as-is and tagged UTC.

    The alternative — guessing the broker's offset — would silently shift every
    session statistic. Better that the hour breakdown reads as server time, which
    is the clock the trader saw on the chart anyway.
    """
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _account_from(info) -> Account:
    return Account(
        login=str(info.login),
        name=getattr(info, "name", "") or "",
        server=getattr(info, "server", "") or "",
        currency=getattr(info, "currency", "USD") or "USD",
        balance=float(info.balance),
        equity=float(info.equity),
        margin=float(getattr(info, "margin", 0.0)),
        free_margin=float(getattr(info, "margin_free", 0.0)),
        leverage=int(getattr(info, "leverage", 0)),
        source="mt5_live",
        updated_at=datetime.now(timezone.utc),
    )


def _entry_stops(orders) -> dict[int, tuple[Optional[float], Optional[float]]]:
    """Initial SL/TP per position, taken from the earliest order that opened it."""
    stops: dict[int, tuple[Optional[float], Optional[float]]] = {}
    for order in sorted(orders or [], key=lambda o: o.time_setup):
        position_id = int(getattr(order, "position_id", 0) or 0)
        if not position_id or position_id in stops:
            continue
        sl = float(getattr(order, "sl", 0.0) or 0.0)
        tp = float(getattr(order, "tp", 0.0) or 0.0)
        stops[position_id] = (sl or None, tp or None)
    return stops


def build_trades(deals, orders, account_login: str) -> list[Trade]:
    """Fold MT5 deal records into closed positions."""
    grouped: dict[int, list] = defaultdict(list)
    for deal in deals or []:
        position_id = int(getattr(deal, "position_id", 0) or 0)
        if not position_id:
            continue  # balance, credit and commission adjustments carry no position
        if int(deal.type) not in (DEAL_TYPE_BUY, DEAL_TYPE_SELL):
            continue
        grouped[position_id].append(deal)

    stops = _entry_stops(orders)
    trades: list[Trade] = []

    for position_id, position_deals in grouped.items():
        position_deals.sort(key=lambda d: (d.time, d.ticket))
        entries = [d for d in position_deals if int(d.entry) in (DEAL_ENTRY_IN, DEAL_ENTRY_INOUT)]
        exits = [d for d in position_deals if int(d.entry) in (DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY)]
        if not entries or not exits:
            continue  # still open, or the entry predates the requested window

        entry_volume = sum(float(d.volume) for d in entries) or 1.0
        exit_volume = sum(float(d.volume) for d in exits) or 1.0
        open_price = sum(float(d.price) * float(d.volume) for d in entries) / entry_volume
        close_price = sum(float(d.price) * float(d.volume) for d in exits) / exit_volume

        side = "buy" if int(entries[0].type) == DEAL_TYPE_BUY else "sell"
        sl, tp = stops.get(position_id, (None, None))

        trades.append(
            Trade(
                ticket=str(position_id),
                symbol=entries[0].symbol,
                side=side,
                volume=round(sum(float(d.volume) for d in entries), 4),
                open_time=_utc(entries[0].time),
                close_time=_utc(exits[-1].time),
                open_price=open_price,
                close_price=close_price,
                profit=sum(float(d.profit) for d in position_deals),
                commission=sum(float(getattr(d, "commission", 0.0)) for d in position_deals),
                swap=sum(float(getattr(d, "swap", 0.0)) for d in position_deals),
                fee=sum(float(getattr(d, "fee", 0.0)) for d in position_deals),
                sl=sl,
                tp=tp,
                magic=int(getattr(entries[0], "magic", 0) or 0),
                comment=getattr(entries[0], "comment", "") or "",
                account_login=account_login,
                source="mt5_live",
            )
        )

    trades.sort(key=lambda t: t.close_time)
    return trades


def build_open_positions(positions) -> list[OpenPosition]:
    result = []
    for p in positions or []:
        result.append(
            OpenPosition(
                ticket=str(p.ticket),
                symbol=p.symbol,
                side="buy" if int(p.type) == POSITION_TYPE_BUY else "sell",
                volume=float(p.volume),
                open_time=_utc(p.time),
                open_price=float(p.price_open),
                current_price=float(getattr(p, "price_current", 0.0)),
                profit=float(p.profit),
                swap=float(getattr(p, "swap", 0.0)),
                sl=float(p.sl) or None,
                tp=float(p.tp) or None,
                comment=getattr(p, "comment", "") or "",
            )
        )
    return result


def sync(settings: MT5Settings) -> ImportResult:
    """Pull account state, closed positions and open positions from the terminal."""
    mt5 = _import_mt5()
    _connect(mt5, settings)
    try:
        info = mt5.account_info()
        if info is None:
            raise MT5Unavailable(
                "Терминал запущен, но счёт не подключён — залогинься в MT5 и повтори."
            )
        account = _account_from(info)

        date_to = datetime.now() + timedelta(days=1)
        date_from = datetime.now() - timedelta(days=settings.history_days)

        deals = mt5.history_deals_get(date_from, date_to)
        orders = mt5.history_orders_get(date_from, date_to)
        positions = mt5.positions_get()

        trades = build_trades(deals, orders, account.login)
        open_positions = build_open_positions(positions)
    finally:
        mt5.shutdown()

    result = ImportResult(
        source="mt5_live",
        trades=trades,
        trades_seen=len(trades),
        account=account,
        open_positions=open_positions,
    )
    if not trades:
        result.warnings.append(
            "Терминал не вернул ни одной закрытой сделки за запрошенный период. "
            "Проверь глубину истории в настройках MT5."
        )
    without_stops = sum(1 for t in trades if not t.sl)
    if trades and without_stops == len(trades):
        result.warnings.append(
            "Ни у одной сделки нет стоп-лосса в истории ордеров — статистика по R "
            "и по риску будет недоступна."
        )
    return result
