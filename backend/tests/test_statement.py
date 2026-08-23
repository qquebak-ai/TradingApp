"""Statement parsing: the MT4 and MT5 layouts, localisation, and junk rows."""

from datetime import datetime, timezone

import pytest

from backend.app.ingest.readers import csv_rows, html_rows, read_rows
from backend.app.ingest.statement import (
    map_header,
    parse_datetime,
    parse_number,
    parse_statement,
)

MT4_HTML = """<html><body>
<div>Account: 500123 Name: Ivan Petrov Currency: EUR Leverage: 1:200</div>
<table>
<tr><td>Ticket</td><td>Open Time</td><td>Type</td><td>Size</td><td>Item</td><td>Price</td>
<td>S / L</td><td>T / P</td><td>Close Time</td><td>Price</td><td>Commission</td>
<td>Taxes</td><td>Swap</td><td>Profit</td></tr>
<tr><td>101</td><td>2025.03.03 10:00</td><td>buy</td><td>0.50</td><td>EURUSD</td>
<td>1.08000</td><td>1.07500</td><td>1.09000</td><td>2025.03.03 12:30</td><td>1.08600</td>
<td>-3.50</td><td>0.00</td><td>-1.20</td><td>300.00</td></tr>
<tr><td>102</td><td>2025.03.04 09:00</td><td>sell</td><td>0.20</td><td>XAUUSD</td>
<td>2 300.50</td><td>0</td><td>0</td><td>2025.03.04 09:45</td><td>2 305.00</td>
<td>-1.40</td><td>0.00</td><td>0.00</td><td>-90.00</td></tr>
<tr><td>103</td><td>2025.03.05 09:00</td><td>balance</td><td></td><td></td><td></td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td><td>1000.00</td></tr>
</table>
<table><tr><td>Balance:</td><td>10 208.30</td></tr></table>
</body></html>"""

MT5_HTML = """<html><body>
<div>Name: Demo Currency: USD Account: 77001</div>
<table>
<tr><td>Time</td><td>Position</td><td>Symbol</td><td>Type</td><td>Volume</td><td>Price</td>
<td>S / L</td><td>T / P</td><td>Time</td><td>Price</td><td>Commission</td><td>Swap</td>
<td>Profit</td></tr>
<tr><td>2025.05.01 08:00:00</td><td>900</td><td>GBPUSD</td><td>Buy</td><td>1.00</td>
<td>1.25000</td><td>1.24500</td><td>1.26000</td><td>2025.05.01 11:00:00</td><td>1.25400</td>
<td>-7.00</td><td>-0.50</td><td>400.00</td></tr>
</table>
<table>
<tr><td>Time</td><td>Deal</td><td>Symbol</td><td>Type</td><td>Direction</td><td>Volume</td>
<td>Price</td><td>Order</td><td>Commission</td><td>Fee</td><td>Swap</td><td>Profit</td></tr>
<tr><td>2025.05.01 08:00:00</td><td>5001</td><td>GBPUSD</td><td>Buy</td><td>In</td><td>1.00</td>
<td>1.25000</td><td>900</td><td>-3.50</td><td>0.00</td><td>0.00</td><td>0.00</td></tr>
</table>
</body></html>"""

RU_CSV = (
    "Ордер;Время;Тип;Размер;Символ;Цена;S / L;T / P;Время;Цена;Комиссия;Налоги;Своп;Прибыль\n"
    "77;2025.02.10 07:15;sell;0,30;USDJPY;150,250;150,750;149,500;"
    "2025.02.10 08:05;149,900;-2,10;0,00;0,00;70,50\n"
)


def test_number_parsing_handles_metatrader_formats():
    assert parse_number("1 234.56") == pytest.approx(1234.56)
    assert parse_number("1,234.56") == pytest.approx(1234.56)
    assert parse_number("0,30") == pytest.approx(0.30)
    assert parse_number("-2.10") == pytest.approx(-2.10)
    assert parse_number("(45.00)") == pytest.approx(-45.00)
    assert parse_number("") is None
    assert parse_number("—") is None


def test_datetime_parsing_accepts_both_terminal_formats():
    assert parse_datetime("2025.03.03 10:00") == datetime(2025, 3, 3, 10, 0, tzinfo=timezone.utc)
    assert parse_datetime("2025.03.03 10:00:15") == datetime(2025, 3, 3, 10, 0, 15, tzinfo=timezone.utc)
    assert parse_datetime("03.03.2025 10:00") == datetime(2025, 3, 3, 10, 0, tzinfo=timezone.utc)
    assert parse_datetime("") is None


def test_header_needs_both_time_and_price_sides():
    """A Deals table has one Time and one Price, so it must not read as positions."""
    deals_header = ["Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price",
                    "Order", "Commission", "Fee", "Swap", "Profit"]
    assert map_header(deals_header) is None

    positions_header = ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L",
                        "T / P", "Time", "Price", "Commission", "Swap", "Profit"]
    mapping = map_header(positions_header)
    assert mapping["open_time"] == 0 and mapping["close_time"] == 8
    assert mapping["open_price"] == 5 and mapping["close_price"] == 9


def test_mt4_statement_parses_trades_and_skips_balance_rows():
    result = parse_statement(html_rows(MT4_HTML), source="mt4")
    assert result.trades_seen == 2

    first, second = result.trades
    assert first.ticket == "101"
    assert first.symbol == "EURUSD" and first.side == "buy"
    assert first.volume == pytest.approx(0.5)
    assert first.profit == pytest.approx(300.0)
    assert first.net == pytest.approx(300.0 - 3.5 - 1.2)
    assert first.sl == pytest.approx(1.075)
    assert second.open_price == pytest.approx(2300.50)  # thin-space thousands
    assert second.sl is None  # a literal "0" means no stop was set

    assert result.account.login == "500123"
    assert result.account.currency == "EUR"
    assert result.account.leverage == 200
    assert result.account.balance == pytest.approx(10208.30)


def test_mt5_report_reads_positions_and_ignores_the_deals_table():
    result = parse_statement(html_rows(MT5_HTML), source="mt5")
    assert result.trades_seen == 1
    trade = result.trades[0]
    assert trade.ticket == "900" and trade.symbol == "GBPUSD"
    assert trade.close_time.hour == 11
    assert trade.net == pytest.approx(400.0 - 7.0 - 0.5)


def test_russian_csv_with_comma_decimals():
    result = parse_statement(csv_rows(RU_CSV), source="csv")
    assert result.trades_seen == 1
    trade = result.trades[0]
    assert trade.symbol == "USDJPY" and trade.side == "sell"
    assert trade.volume == pytest.approx(0.30)
    assert trade.open_price == pytest.approx(150.250)
    assert trade.net == pytest.approx(70.50 - 2.10)


def test_reader_sniffs_format_without_a_useful_filename():
    assert read_rows("", MT4_HTML.encode("utf-8"))
    assert read_rows("", RU_CSV.encode("utf-8"))


def test_empty_statement_reports_a_warning_not_a_crash():
    result = parse_statement(html_rows("<html><body><p>ничего</p></body></html>"))
    assert result.trades_seen == 0
    assert result.warnings
