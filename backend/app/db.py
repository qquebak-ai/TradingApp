"""SQLite persistence. One file on disk, no ORM — the schema is four flat tables."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import Account, OpenPosition, Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_login  TEXT    NOT NULL DEFAULT '',
    ticket         TEXT    NOT NULL,
    symbol         TEXT    NOT NULL,
    side           TEXT    NOT NULL,
    volume         REAL    NOT NULL,
    open_time      TEXT    NOT NULL,
    close_time     TEXT    NOT NULL,
    open_price     REAL    NOT NULL,
    close_price    REAL    NOT NULL,
    profit         REAL    NOT NULL DEFAULT 0,
    commission     REAL    NOT NULL DEFAULT 0,
    swap           REAL    NOT NULL DEFAULT 0,
    fee            REAL    NOT NULL DEFAULT 0,
    sl             REAL,
    tp             REAL,
    magic          INTEGER NOT NULL DEFAULT 0,
    comment        TEXT    NOT NULL DEFAULT '',
    source         TEXT    NOT NULL DEFAULT 'manual',
    UNIQUE (account_login, ticket)
);

CREATE INDEX IF NOT EXISTS idx_trades_close ON trades (close_time);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);

CREATE TABLE IF NOT EXISTS accounts (
    login       TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    server      TEXT NOT NULL DEFAULT '',
    currency    TEXT NOT NULL DEFAULT 'USD',
    balance     REAL NOT NULL DEFAULT 0,
    equity      REAL NOT NULL DEFAULT 0,
    margin      REAL NOT NULL DEFAULT 0,
    free_margin REAL NOT NULL DEFAULT 0,
    leverage    INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'manual',
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS open_positions (
    ticket        TEXT PRIMARY KEY,
    account_login TEXT NOT NULL DEFAULT '',
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL,
    volume        REAL NOT NULL,
    open_time     TEXT NOT NULL,
    open_price    REAL NOT NULL,
    current_price REAL NOT NULL DEFAULT 0,
    profit        REAL NOT NULL DEFAULT 0,
    swap          REAL NOT NULL DEFAULT 0,
    sl            REAL,
    tp            REAL,
    comment       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS import_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at    TEXT NOT NULL,
    source    TEXT NOT NULL,
    added     INTEGER NOT NULL DEFAULT 0,
    updated   INTEGER NOT NULL DEFAULT 0,
    detail    TEXT NOT NULL DEFAULT ''
);
"""

_TRADE_COLUMNS = (
    "account_login, ticket, symbol, side, volume, open_time, close_time, "
    "open_price, close_price, profit, commission, swap, fee, sl, tp, magic, "
    "comment, source"
)


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def _trade_row(trade: Trade) -> tuple:
    return (
        trade.account_login,
        str(trade.ticket),
        trade.symbol,
        trade.side,
        trade.volume,
        trade.open_time.isoformat(),
        trade.close_time.isoformat(),
        trade.open_price,
        trade.close_price,
        trade.profit,
        trade.commission,
        trade.swap,
        trade.fee,
        trade.sl,
        trade.tp,
        trade.magic,
        trade.comment,
        trade.source,
    )


def upsert_trades(conn: sqlite3.Connection, trades: Iterable[Trade]) -> tuple[int, int]:
    """Insert new trades, refresh the money legs of ones we already hold.

    A statement re-exported after a swap accrual carries the same ticket with new
    numbers, so re-importing must correct the row rather than duplicate or ignore it.
    Returns (added, updated).
    """
    added = updated = 0
    cur = conn.cursor()
    for trade in trades:
        row = _trade_row(trade)
        existing = cur.execute(
            "SELECT profit, commission, swap, fee, close_price, close_time, sl, tp "
            "FROM trades WHERE account_login = ? AND ticket = ?",
            (trade.account_login, str(trade.ticket)),
        ).fetchone()
        if existing is None:
            cur.execute(
                f"INSERT INTO trades ({_TRADE_COLUMNS}) "
                f"VALUES ({', '.join(['?'] * 18)})",
                row,
            )
            added += 1
            continue
        changed = (
            abs(existing["profit"] - trade.profit) > 1e-9
            or abs(existing["commission"] - trade.commission) > 1e-9
            or abs(existing["swap"] - trade.swap) > 1e-9
            or abs(existing["fee"] - trade.fee) > 1e-9
            or abs(existing["close_price"] - trade.close_price) > 1e-12
            or existing["close_time"] != trade.close_time.isoformat()
            or existing["sl"] != trade.sl
            or existing["tp"] != trade.tp
        )
        if changed:
            cur.execute(
                "UPDATE trades SET symbol=?, side=?, volume=?, open_time=?, "
                "close_time=?, open_price=?, close_price=?, profit=?, commission=?, "
                "swap=?, fee=?, sl=?, tp=?, magic=?, comment=?, source=? "
                "WHERE account_login=? AND ticket=?",
                row[2:] + (row[0], row[1]),
            )
            updated += 1
    conn.commit()
    return added, updated


def save_account(conn: sqlite3.Connection, account: Account) -> None:
    conn.execute(
        "INSERT INTO accounts (login, name, server, currency, balance, equity, "
        "margin, free_margin, leverage, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(login) DO UPDATE SET name=excluded.name, server=excluded.server, "
        "currency=excluded.currency, balance=excluded.balance, equity=excluded.equity, "
        "margin=excluded.margin, free_margin=excluded.free_margin, "
        "leverage=excluded.leverage, source=excluded.source, "
        "updated_at=excluded.updated_at",
        (
            account.login,
            account.name,
            account.server,
            account.currency,
            account.balance,
            account.equity,
            account.margin,
            account.free_margin,
            account.leverage,
            account.source,
            (account.updated_at or datetime.now(timezone.utc)).isoformat(),
        ),
    )
    conn.commit()


def get_account(conn: sqlite3.Connection, login: Optional[str] = None) -> Optional[Account]:
    if login:
        row = conn.execute("SELECT * FROM accounts WHERE login = ?", (login,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM accounts ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    updated = row["updated_at"]
    return Account(
        login=row["login"],
        name=row["name"],
        server=row["server"],
        currency=row["currency"],
        balance=row["balance"],
        equity=row["equity"],
        margin=row["margin"],
        free_margin=row["free_margin"],
        leverage=row["leverage"],
        source=row["source"],
        updated_at=datetime.fromisoformat(updated) if updated else None,
    )


def replace_open_positions(
    conn: sqlite3.Connection, account_login: str, positions: Iterable[OpenPosition]
) -> None:
    """Open positions are a snapshot, not a log — the latest sync is the truth."""
    cur = conn.cursor()
    cur.execute("DELETE FROM open_positions WHERE account_login = ?", (account_login,))
    for p in positions:
        cur.execute(
            "INSERT INTO open_positions (ticket, account_login, symbol, side, volume, "
            "open_time, open_price, current_price, profit, swap, sl, tp, comment) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(p.ticket),
                account_login,
                p.symbol,
                p.side,
                p.volume,
                p.open_time.isoformat(),
                p.open_price,
                p.current_price,
                p.profit,
                p.swap,
                p.sl,
                p.tp,
                p.comment,
            ),
        )
    conn.commit()


def get_open_positions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM open_positions ORDER BY open_time DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def log_import(
    conn: sqlite3.Connection, source: str, added: int, updated: int, detail: str = ""
) -> None:
    conn.execute(
        "INSERT INTO import_log (ran_at, source, added, updated, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), source, added, updated, detail),
    )
    conn.commit()


def last_import(conn: sqlite3.Connection) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM import_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def load_trades(
    conn: sqlite3.Connection,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    symbol: Optional[str] = None,
    magic: Optional[int] = None,
    account_login: Optional[str] = None,
) -> list[Trade]:
    """Closed trades matching the dashboard filters, oldest close first."""
    sql = "SELECT * FROM trades WHERE 1 = 1"
    params: list = []
    if date_from:
        sql += " AND close_time >= ?"
        params.append(date_from)
    if date_to:
        # Inclusive end-of-day: callers pass a plain date, not a timestamp.
        sql += " AND close_time <= ?"
        params.append(date_to + "T23:59:59.999999+00:00" if len(date_to) == 10 else date_to)
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol)
    if magic is not None:
        sql += " AND magic = ?"
        params.append(magic)
    if account_login:
        sql += " AND account_login = ?"
        params.append(account_login)
    sql += " ORDER BY close_time ASC, id ASC"

    trades = []
    for row in conn.execute(sql, params):
        trades.append(
            Trade(
                ticket=row["ticket"],
                symbol=row["symbol"],
                side=row["side"],
                volume=row["volume"],
                open_time=datetime.fromisoformat(row["open_time"]),
                close_time=datetime.fromisoformat(row["close_time"]),
                open_price=row["open_price"],
                close_price=row["close_price"],
                profit=row["profit"],
                commission=row["commission"],
                swap=row["swap"],
                fee=row["fee"],
                sl=row["sl"],
                tp=row["tp"],
                magic=row["magic"],
                comment=row["comment"],
                account_login=row["account_login"],
                source=row["source"],
            )
        )
    return trades


def distinct_symbols(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM trades ORDER BY symbol")]


def date_bounds(conn: sqlite3.Connection) -> tuple[Optional[str], Optional[str]]:
    row = conn.execute("SELECT MIN(close_time), MAX(close_time) FROM trades").fetchone()
    return (row[0], row[1]) if row else (None, None)


def clear_trades(conn: sqlite3.Connection, account_login: Optional[str] = None) -> int:
    cur = conn.cursor()
    if account_login:
        cur.execute("DELETE FROM trades WHERE account_login = ?", (account_login,))
    else:
        cur.execute("DELETE FROM trades")
    conn.commit()
    return cur.rowcount
