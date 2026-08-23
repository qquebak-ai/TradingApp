"""Generate a realistic MT4-style HTML statement for demos and tests.

The synthetic trader has deliberate habits — a weak session, a losing symbol,
missing stops on a few trades and oversized entries right after losses — so the
dashboard has something real to find. Run:

    python3 tools/make_demo_statement.py samples/demo_mt4_statement.html
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

SYMBOLS = {
    "EURUSD": {"pip": 0.0001, "pip_value": 10.0, "edge": 0.58},
    "GBPUSD": {"pip": 0.0001, "pip_value": 10.0, "edge": 0.54},
    "XAUUSD": {"pip": 0.10, "pip_value": 10.0, "edge": 0.44},
    "USDJPY": {"pip": 0.01, "pip_value": 9.1, "edge": 0.51},
}
BASE_PRICE = {"EURUSD": 1.0850, "GBPUSD": 1.2670, "XAUUSD": 2320.0, "USDJPY": 151.30}

HEADER = """<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<title>Statement</title></head><body bgcolor="#FFFFFF">
<div align="center"><b>Demo Broker Ltd</b><br>
Account: 5104928 &nbsp; Name: Demo Trader &nbsp; Currency: USD &nbsp; Leverage: 1:100<br>
</div>
<table border="1" cellpadding="3" cellspacing="0">
<tr align="center" bgcolor="#C0C0C0">
<td>Ticket</td><td>Open Time</td><td>Type</td><td>Size</td><td>Item</td><td>Price</td>
<td>S / L</td><td>T / P</td><td>Close Time</td><td>Price</td><td>Commission</td>
<td>Taxes</td><td>Swap</td><td>Profit</td></tr>
"""

FOOTER = """</table>
<table><tr><td>Closed Trade P/L:</td><td>{net:.2f}</td></tr>
<tr><td>Balance:</td><td>{balance:.2f}</td></tr>
<tr><td>Equity:</td><td>{balance:.2f}</td></tr></table>
</body></html>
"""


def _digits(symbol: str) -> int:
    return 2 if symbol in ("XAUUSD", "USDJPY") else 5


def generate(count: int = 260, seed: int = 7) -> tuple[list[dict], float]:
    rng = random.Random(seed)
    trades: list[dict] = []
    cursor = datetime(2025, 1, 6, 8, 15)
    ticket = 41_000_000
    last_was_loss = False

    while len(trades) < count:
        cursor += timedelta(minutes=rng.choice([35, 55, 90, 140, 260, 480]))
        if cursor.weekday() >= 5:
            cursor += timedelta(days=2)
        hour = cursor.hour
        if hour < 7 or hour > 21:
            cursor = cursor.replace(hour=8, minute=rng.randint(0, 55)) + timedelta(days=1)
            continue

        symbol = rng.choices(list(SYMBOLS), weights=[40, 25, 25, 10])[0]
        spec = SYMBOLS[symbol]
        side = rng.choice(["buy", "sell"])

        # The habits: normal risk is 0.10 lots, but a loss inside the last half hour
        # tempts a double-size entry, and the 19:00-21:00 stretch trades badly.
        oversized = last_was_loss and rng.random() < 0.45
        volume = round(0.10 * (2.5 if oversized else rng.choice([1.0, 1.0, 1.0, 1.2])), 2)

        edge = spec["edge"]
        if hour >= 19:
            edge -= 0.18
        if oversized:
            edge -= 0.12
        win = rng.random() < edge

        stop_pips = rng.choice([18, 22, 25, 30])
        target_pips = stop_pips * rng.choice([1.2, 1.5, 1.8, 2.0])
        has_stop = rng.random() > (0.30 if oversized else 0.08)

        entry = BASE_PRICE[symbol] * (1 + rng.uniform(-0.012, 0.012))
        pip = spec["pip"]
        direction = 1 if side == "buy" else -1

        if win:
            move_pips = target_pips * rng.uniform(0.6, 1.05)
            held = timedelta(minutes=int(rng.uniform(20, 240)))
        else:
            overrun = rng.uniform(1.0, 1.35) if not has_stop else rng.uniform(0.9, 1.08)
            move_pips = -stop_pips * overrun
            # Losers get nursed: they stay open far longer than winners.
            held = timedelta(minutes=int(rng.uniform(90, 900)))

        exit_price = entry + direction * move_pips * pip
        gross = move_pips * spec["pip_value"] * (volume / 0.10) * 0.1
        commission = -round(volume * 7.0, 2)
        swap = round(-abs(rng.gauss(0.8, 0.6)) * (held.days + 1), 2) if held > timedelta(hours=8) else 0.0

        digits = _digits(symbol)
        trades.append(
            {
                "ticket": ticket,
                "open_time": cursor,
                "side": side,
                "volume": volume,
                "symbol": symbol,
                "open_price": round(entry, digits),
                "sl": round(entry - direction * stop_pips * pip, digits) if has_stop else 0,
                "tp": round(entry + direction * target_pips * pip, digits),
                "close_time": cursor + held,
                "close_price": round(exit_price, digits),
                "commission": commission,
                "taxes": 0.0,
                "swap": swap,
                "profit": round(gross, 2),
            }
        )
        ticket += 1
        last_was_loss = gross + commission + swap < 0
        cursor += held if held < timedelta(hours=6) else timedelta(hours=2)

    trades.sort(key=lambda t: t["close_time"])
    net = sum(t["profit"] + t["commission"] + t["swap"] + t["taxes"] for t in trades)
    return trades, net


def render(trades: list[dict], net: float, opening_balance: float = 10_000.0) -> str:
    rows = []
    for t in trades:
        digits = _digits(t["symbol"])
        rows.append(
            "<tr align=right><td>{ticket}</td><td>{open_time:%Y.%m.%d %H:%M}</td>"
            "<td>{side}</td><td>{volume:.2f}</td><td>{symbol}</td>"
            "<td>{open_price:.{d}f}</td><td>{sl}</td><td>{tp:.{d}f}</td>"
            "<td>{close_time:%Y.%m.%d %H:%M}</td><td>{close_price:.{d}f}</td>"
            "<td>{commission:.2f}</td><td>{taxes:.2f}</td><td>{swap:.2f}</td>"
            "<td>{profit:.2f}</td></tr>".format(
                d=digits,
                **{**t, "sl": f"{t['sl']:.{digits}f}" if t["sl"] else "0"},
            )
        )
    return HEADER + "\n".join(rows) + FOOTER.format(net=net, balance=opening_balance + net)


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "samples/demo_mt4_statement.html")
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 260
    trades, net = generate(count)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(trades, net), encoding="utf-8")
    print(f"{len(trades)} сделок → {target} (итог {net:.2f})")


if __name__ == "__main__":
    main()
