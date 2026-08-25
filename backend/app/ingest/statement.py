"""Normalise a MetaTrader statement table into Trade objects.

Handles MT4 statements ("Closed Transactions") and MT5 reports ("Positions"), in
English or Russian, from HTML, CSV or XLSX — they are all the same table with
different column names, so the work is header mapping plus tolerant parsing.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional, Sequence

from ..models import Account, ImportResult, Trade

# Header alias -> (field, occurrence). Occurrence 1 means "the first column with
# this name", which is how the duplicated Time/Price columns get split into the
# open and close sides.
HEADER_ALIASES: dict[str, tuple[str, int]] = {
    "ticket": ("ticket", 1),
    "position": ("ticket", 1),
    "positionid": ("ticket", 1),
    "order": ("ticket", 1),
    "deal": ("ticket", 1),
    "ордер": ("ticket", 1),
    "позиция": ("ticket", 1),
    "тикет": ("ticket", 1),
    "opentime": ("open_time", 1),
    "openingtime": ("open_time", 1),
    "времяоткрытия": ("open_time", 1),
    "closetime": ("close_time", 1),
    "closingtime": ("close_time", 1),
    "времязакрытия": ("close_time", 1),
    "time": ("open_time", 1),
    "время": ("open_time", 1),
    "type": ("side", 1),
    "тип": ("side", 1),
    "size": ("volume", 1),
    "volume": ("volume", 1),
    "lots": ("volume", 1),
    "объем": ("volume", 1),
    "объём": ("volume", 1),
    "размер": ("volume", 1),
    "item": ("symbol", 1),
    "symbol": ("symbol", 1),
    "инструмент": ("symbol", 1),
    "символ": ("symbol", 1),
    "price": ("open_price", 1),
    "цена": ("open_price", 1),
    "openprice": ("open_price", 1),
    "ценаоткрытия": ("open_price", 1),
    "closeprice": ("close_price", 1),
    "ценазакрытия": ("close_price", 1),
    "sl": ("sl", 1),
    "stoploss": ("sl", 1),
    "tp": ("tp", 1),
    "takeprofit": ("tp", 1),
    "commission": ("commission", 1),
    "комиссия": ("commission", 1),
    "taxes": ("fee", 1),
    "fee": ("fee", 1),
    "налоги": ("fee", 1),
    "swap": ("swap", 1),
    "своп": ("swap", 1),
    "profit": ("profit", 1),
    "прибыль": ("profit", 1),
    "comment": ("comment", 1),
    "комментарий": ("comment", 1),
    "magic": ("magic", 1),
    "мэджик": ("magic", 1),
}

# Columns that repeat in MetaTrader layouts; the Nth occurrence means something else.
REPEATED_COLUMNS = {
    ("open_time", 2): "close_time",
    ("open_price", 2): "close_price",
}

REQUIRED_FIELDS = {
    "symbol",
    "side",
    "volume",
    "open_time",
    "close_time",
    "open_price",
    "close_price",
    "profit",
}

BUY_WORDS = {"buy", "покупка", "buylimit", "buystop", "long"}
SELL_WORDS = {"sell", "продажа", "selllimit", "sellstop", "short"}

DATE_FORMATS = (
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%Y.%m.%d",
    "%Y-%m-%d",
)

_NUMBER_CLEAN = re.compile(r"[\s  ']")


def _norm(text: str) -> str:
    """Header cells vary by spacing and punctuation only — flatten that away."""
    return re.sub(r"[^0-9a-zа-яё]", "", text.lower())


def parse_number(raw: str) -> Optional[float]:
    if raw is None:
        return None
    text = _NUMBER_CLEAN.sub("", str(raw)).replace("−", "-")
    if not text or text in ("-", "—", "–"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    # Statements use '.' for decimals and thin spaces for thousands; a lone comma
    # is a decimal separator in localised CSV exports.
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif text.count(",") == 1 and len(text.split(",")[1]) in (1, 2, 3, 4, 5):
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def parse_datetime(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    text = " ".join(str(raw).split())
    if not text or text in ("-", "—"):
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:  # ISO strings, which is what our own CSV export writes
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def map_header(cells: Sequence[str]) -> Optional[dict[str, int]]:
    """Map a candidate header row to column indexes, or None if it is not one."""
    mapping: dict[str, int] = {}
    seen: Counter[str] = Counter()
    for index, cell in enumerate(cells):
        alias = HEADER_ALIASES.get(_norm(cell))
        if alias is None:
            continue
        field = alias[0]
        seen[field] += 1
        resolved = REPEATED_COLUMNS.get((field, seen[field]), field if seen[field] == 1 else None)
        if resolved and resolved not in mapping:
            mapping[resolved] = index
    return mapping if REQUIRED_FIELDS.issubset(mapping.keys()) else None


def _cell(row: Sequence[str], mapping: dict[str, int], field: str) -> str:
    index = mapping.get(field)
    if index is None or index >= len(row):
        return ""
    return row[index]


def _side(raw: str) -> Optional[str]:
    token = _norm(raw)
    if token in BUY_WORDS:
        return "buy"
    if token in SELL_WORDS:
        return "sell"
    return None


def row_to_trade(
    row: Sequence[str], mapping: dict[str, int], account_login: str, source: str
) -> Optional[Trade]:
    """Build a Trade from one table row, or None when the row is not a trade.

    Balance operations, section subtotals and still-open positions all live in the
    same tables and are filtered out here.
    """
    side = _side(_cell(row, mapping, "side"))
    if side is None:
        return None

    open_time = parse_datetime(_cell(row, mapping, "open_time"))
    close_time = parse_datetime(_cell(row, mapping, "close_time"))
    if open_time is None or close_time is None:
        return None  # open position, or a summary line

    volume = parse_number(_cell(row, mapping, "volume"))
    open_price = parse_number(_cell(row, mapping, "open_price"))
    close_price = parse_number(_cell(row, mapping, "close_price"))
    if volume is None or open_price is None or close_price is None:
        return None

    ticket = _cell(row, mapping, "ticket").strip()
    if not ticket:
        ticket = f"{_cell(row, mapping, 'symbol')}-{open_time:%Y%m%d%H%M%S}-{close_time:%H%M%S}"

    magic_raw = parse_number(_cell(row, mapping, "magic"))

    return Trade(
        ticket=ticket,
        symbol=_cell(row, mapping, "symbol").strip() or "UNKNOWN",
        side=side,
        volume=volume,
        open_time=open_time,
        close_time=close_time,
        open_price=open_price,
        close_price=close_price,
        profit=parse_number(_cell(row, mapping, "profit")) or 0.0,
        commission=parse_number(_cell(row, mapping, "commission")) or 0.0,
        swap=parse_number(_cell(row, mapping, "swap")) or 0.0,
        fee=parse_number(_cell(row, mapping, "fee")) or 0.0,
        sl=parse_number(_cell(row, mapping, "sl")) or None,
        tp=parse_number(_cell(row, mapping, "tp")) or None,
        magic=int(magic_raw) if magic_raw is not None else 0,
        comment=_cell(row, mapping, "comment").strip(),
        account_login=account_login,
        source=source,
    )


ACCOUNT_PATTERNS = {
    "login": re.compile(r"(?:account|счет|счёт|номерсчета)[:\s#]*([0-9]{4,})", re.I),
    "name": re.compile(
        r"(?:^|\s)(?:name|имя|владелец)[:\s]+"
        r"([^\d,;]{2,40}?)"
        r"(?=\s{2,}|\s*(?:currency|валюта|account|счет|счёт|leverage|плечо|company)\b|$)",
        re.I,
    ),
    "currency": re.compile(r"(?:currency|валюта)[:\s]+([A-Z]{3})", re.I),
    "leverage": re.compile(r"(?:leverage|кредитноеплечо|плечо)[:\s]*1[:\s]*([0-9]{1,5})", re.I),
}


def sniff_account(rows: Sequence[Sequence[str]]) -> Account:
    """Best-effort account details from the statement's header block."""
    blob = " ".join(" ".join(row) for row in rows[:40])
    compact = re.sub(r"\s+", "", blob)

    login_match = ACCOUNT_PATTERNS["login"].search(compact)
    currency_match = ACCOUNT_PATTERNS["currency"].search(blob)
    name_match = ACCOUNT_PATTERNS["name"].search(blob)
    leverage_match = ACCOUNT_PATTERNS["leverage"].search(compact)

    return Account(
        login=login_match.group(1) if login_match else "",
        name=name_match.group(1).strip() if name_match else "",
        currency=currency_match.group(1).upper() if currency_match else "USD",
        leverage=int(leverage_match.group(1)) if leverage_match else 0,
        source="statement",
    )


BALANCE_PATTERN = re.compile(r"^(balance|баланс)$", re.I)


def sniff_balance(rows: Sequence[Sequence[str]]) -> Optional[float]:
    """The 'Balance:' figure from the statement's summary block, if present."""
    for row in rows:
        for index, cell in enumerate(row):
            if BALANCE_PATTERN.match(cell.strip().rstrip(":")):
                for candidate in row[index + 1 : index + 4]:
                    value = parse_number(candidate)
                    if value is not None:
                        return value
    return None


def parse_statement(
    rows: Sequence[Sequence[str]], source: str = "statement", account_login: str = ""
) -> ImportResult:
    """Walk the whole table, re-mapping columns whenever a new section starts."""
    account = sniff_account(rows)
    login = account_login or account.login
    account.login = login

    balance = sniff_balance(rows)
    if balance is not None:
        account.balance = balance
        account.equity = balance

    mapping: Optional[dict[str, int]] = None
    trades: list[Trade] = []
    skipped = 0

    for row in rows:
        candidate = map_header(row)
        if candidate is not None:
            mapping = candidate
            continue
        if mapping is None:
            continue
        trade = row_to_trade(row, mapping, login, source)
        if trade is None:
            skipped += 1
        else:
            trades.append(trade)

    result = ImportResult(
        source=source, trades=trades, trades_seen=len(trades), account=account
    )
    if not trades:
        result.warnings.append(
            "В файле не нашлось ни одной закрытой сделки. Проверь, что это отчёт "
            "из вкладки «История» терминала (MT4: Statement, MT5: Report)."
        )
    elif skipped:
        result.warnings.append(
            f"Пропущено {skipped} строк, не похожих на закрытые сделки "
            "(балансовые операции, открытые позиции, итоги)."
        )
    return result
