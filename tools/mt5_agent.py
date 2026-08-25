"""Windows-side agent: read the MT5 terminal and push the history to a dashboard.

Use this when the dashboard runs somewhere other than the machine with MetaTrader —
a home server, a NAS, a second computer. On the Windows box with the terminal:

    pip install MetaTrader5
    python tools/mt5_agent.py --url http://192.168.1.50:8420 --interval 300

With the dashboard on the same machine you do not need this at all: press
"Синхронизировать MT5" in the interface, which calls the same code in-process.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import load_dotenv  # noqa: E402
from backend.app.ingest.mt5_live import MT5Settings, MT5Unavailable, sync  # noqa: E402

# Re-send a few days of already-known trades on every pass: swap accrues and
# a broker can correct a close after the fact, and the server updates in place.
OVERLAP_DAYS = 3


def push(url: str, token: str | None, result, trades) -> dict:
    payload = {
        "account": {
            "login": result.account.login,
            "name": result.account.name,
            "server": result.account.server,
            "currency": result.account.currency,
            "balance": result.account.balance,
            "equity": result.account.equity,
            "margin": result.account.margin,
            "free_margin": result.account.free_margin,
            "leverage": result.account.leverage,
        },
        "trades": [
            {
                "ticket": t.ticket, "symbol": t.symbol, "side": t.side, "volume": t.volume,
                "open_time": t.open_time.isoformat(), "close_time": t.close_time.isoformat(),
                "open_price": t.open_price, "close_price": t.close_price,
                "profit": t.profit, "commission": t.commission, "swap": t.swap,
                "fee": t.fee, "sl": t.sl, "tp": t.tp, "magic": t.magic,
                "comment": t.comment,
            }
            for t in trades
        ],
        "open_positions": [p.to_dict() for p in result.open_positions],
    }

    request = urllib.request.Request(
        url.rstrip("/") + "/api/import/push",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def run_once(args, high_water: dict) -> int:
    settings = MT5Settings(
        login=int(args.login) if args.login else None,
        password=args.password,
        server=args.server,
        terminal_path=args.terminal,
        history_days=args.days,
    )
    try:
        result = sync(settings)
    except MT5Unavailable as exc:
        print(f"[MT5] {exc}", file=sys.stderr)
        return 2

    for warning in result.warnings:
        print(f"[!] {warning}", file=sys.stderr)

    # After the first full upload, only the recent tail needs re-sending.
    trades = result.trades
    seen = high_water.get("close_time")
    if seen is not None:
        cutoff = seen - timedelta(days=OVERLAP_DAYS)
        trades = [t for t in result.trades if t.close_time >= cutoff]

    try:
        response = push(args.url, args.token, result, trades)
    except urllib.error.HTTPError as exc:
        print(f"[HTTP {exc.code}] {exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"[сеть] Дашборд недоступен по адресу {args.url}: {exc}", file=sys.stderr)
        return 4

    if result.trades:
        high_water["close_time"] = max(t.close_time for t in result.trades)

    stamp = datetime.now().strftime("%H:%M:%S")
    print(
        f"[{stamp}] Счёт {result.account.login}: в терминале {result.trades_seen} сделок, "
        f"отправлено {len(trades)}, добавлено {response.get('trades_added')}, "
        f"обновлено {response.get('trades_updated')}, "
        f"открыто сейчас {len(result.open_positions)}",
        flush=True,
    )
    return 0


def main() -> int:
    # Настройки читаются из .env, как и у самого дашборда; флаги их перебивают.
    load_dotenv()

    parser = argparse.ArgumentParser(description="Отправляет историю MT5 в TradingApp")
    parser.add_argument("--url", default=os.environ.get("TRADINGAPP_URL", "http://127.0.0.1:8420"),
                        help="адрес дашборда (или TRADINGAPP_URL в .env)")
    parser.add_argument("--token", default=os.environ.get("TRADINGAPP_TOKEN") or None,
                        help="токен доступа (или TRADINGAPP_TOKEN в .env)")
    parser.add_argument("--login", default=None, help="номер счёта (иначе берётся активный)")
    parser.add_argument("--password", default=None)
    parser.add_argument("--server", default=None, help="сервер брокера")
    parser.add_argument("--terminal", default=None, help="путь к terminal64.exe")
    parser.add_argument("--days", type=int, default=3650, help="глубина истории в днях")
    parser.add_argument("--interval", type=int, default=0,
                        help="повторять каждые N секунд (0 — один раз и выйти)")
    args = parser.parse_args()

    high_water: dict = {}

    if not args.interval:
        return run_once(args, high_water)

    print(f"Отправляю историю на {args.url} каждые {args.interval} с. Ctrl+C — остановить.")
    while True:
        try:
            run_once(args, high_water)
        except Exception as exc:  # никакая разовая ошибка не должна ронять агента
            print(f"[!] Сбой цикла: {exc}", file=sys.stderr)
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("Остановлено.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
