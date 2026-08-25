"""Normalised domain objects shared by every ingest adapter and the analytics layer."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

BUY = "buy"
SELL = "sell"


def _utc(value: datetime | str) -> datetime:
    """Coerce to an aware UTC datetime, accepting the ISO strings JSON carries."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class Trade:
    """A single closed position, however it reached us (live MT5, statement, CSV).

    Prices and volumes stay in the instrument's own units; every money field is in
    the account's deposit currency, which is what the terminal reports too.
    """

    ticket: str
    symbol: str
    side: str
    volume: float
    open_time: datetime
    close_time: datetime
    open_price: float
    close_price: float
    profit: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    fee: float = 0.0
    sl: Optional[float] = None
    tp: Optional[float] = None
    magic: int = 0
    comment: str = ""
    account_login: str = ""
    source: str = "manual"

    def __post_init__(self) -> None:
        self.side = self.side.lower()
        if self.side not in (BUY, SELL):
            raise ValueError(f"unknown side {self.side!r} on ticket {self.ticket}")
        self.open_time = _utc(self.open_time)
        self.close_time = _utc(self.close_time)

    @property
    def net(self) -> float:
        """P&L actually credited to the account: gross plus every cost leg."""
        return self.profit + self.commission + self.swap + self.fee

    @property
    def costs(self) -> float:
        return self.commission + self.swap + self.fee

    @property
    def duration_minutes(self) -> float:
        return max(0.0, (self.close_time - self.open_time).total_seconds() / 60.0)

    @property
    def is_win(self) -> bool:
        return self.net > 0

    @property
    def price_move(self) -> float:
        """Signed price travel in the trade's own direction."""
        raw = self.close_price - self.open_price
        return raw if self.side == BUY else -raw

    def money_per_price_unit(self) -> Optional[float]:
        """Deposit-currency value of one price unit for this position's size.

        Derived from the trade itself rather than from contract specs, which a
        statement never carries: gross profit divided by the price travel. Returns
        None when the trade closed flat and the ratio is undefined.
        """
        move = self.price_move
        if abs(move) < 1e-12 or abs(self.profit) < 1e-12:
            return None
        return abs(self.profit / move)

    def risk_money(self) -> Optional[float]:
        """What the initial stop-loss put at risk, in deposit currency."""
        if self.sl is None or self.sl <= 0:
            return None
        per_unit = self.money_per_price_unit()
        if per_unit is None:
            return None
        distance = (
            self.open_price - self.sl if self.side == BUY else self.sl - self.open_price
        )
        if distance <= 0:
            return None
        return distance * per_unit

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["open_time"] = self.open_time.isoformat()
        data["close_time"] = self.close_time.isoformat()
        data["net"] = self.net
        data["costs"] = self.costs
        data["duration_minutes"] = self.duration_minutes
        data["risk_money"] = self.risk_money()
        return data


@dataclass
class Account:
    """Terminal-reported account state; the live connector refreshes it on sync."""

    login: str
    name: str = ""
    server: str = ""
    currency: str = "USD"
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0
    leverage: int = 0
    source: str = "manual"
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        return data


@dataclass
class OpenPosition:
    """A position still running — shown live, excluded from closed-trade statistics."""

    ticket: str
    symbol: str
    side: str
    volume: float
    open_time: datetime
    open_price: float
    current_price: float = 0.0
    profit: float = 0.0
    swap: float = 0.0
    sl: Optional[float] = None
    tp: Optional[float] = None
    comment: str = ""

    def __post_init__(self) -> None:
        self.side = self.side.lower()
        self.open_time = _utc(self.open_time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["open_time"] = self.open_time.isoformat()
        return data


@dataclass
class ImportResult:
    """What an ingest run actually changed, so the UI can report it honestly."""

    source: str
    trades: list = field(default_factory=list)
    trades_seen: int = 0
    trades_added: int = 0
    trades_updated: int = 0
    account: Optional[Account] = None
    open_positions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "trades_seen": self.trades_seen,
            "trades_added": self.trades_added,
            "trades_updated": self.trades_updated,
            "account": self.account.to_dict() if self.account else None,
            "open_positions": [p.to_dict() for p in self.open_positions],
            "warnings": self.warnings,
        }
