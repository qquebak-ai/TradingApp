"""FastAPI application: statement import, MT5 sync, and the statistics API."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .analytics.breakdowns import by_symbol, by_time
from .analytics.core import equity_curve, starting_balance, summary
from .analytics.risk import risk_report
from .config import Settings
from .ingest import mt5_live
from .ingest.readers import read_rows
from .ingest.statement import parse_statement
from .models import Account, Trade

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

settings = Settings.from_env()
conn = db.connect(settings.db_path)

app = FastAPI(
    title="TradingApp",
    description="Статистика торгового счёта MetaTrader 4/5",
    version="1.0.0",
)


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    """No-op unless TRADINGAPP_TOKEN is set; then every /api call must carry it."""
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Неверный или отсутствующий токен")


class Filters(BaseModel):
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    symbol: Optional[str] = None
    magic: Optional[int] = None


def get_filters(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    symbol: Optional[str] = Query(None),
    magic: Optional[int] = Query(None),
) -> Filters:
    return Filters(date_from=date_from, date_to=date_to, symbol=symbol, magic=magic)


def _load(filters: Filters) -> list[Trade]:
    return db.load_trades(
        conn,
        date_from=filters.date_from,
        date_to=filters.date_to,
        symbol=filters.symbol,
        magic=filters.magic,
    )


def _opening_balance(trades: list[Trade]) -> tuple[float, bool]:
    """Opening balance for the filtered window, and whether it is an estimate."""
    account = db.get_account(conn)
    if account and account.balance > 0:
        all_trades = db.load_trades(conn)
        overall_open = starting_balance(all_trades, account.balance)
        # Roll forward to the start of the filtered window so percentages line up.
        first = trades[0].close_time if trades else None
        if first is None:
            return overall_open, False
        before = sum(t.net for t in all_trades if t.close_time < first)
        return overall_open + before, False
    return starting_balance(trades, None), True


def _store(result, source: str) -> dict:
    account = result.account
    if account and account.login:
        for trade in result.trades:
            trade.account_login = account.login
        db.save_account(conn, account)
    added, updated = db.upsert_trades(conn, result.trades)
    result.trades_added, result.trades_updated = added, updated
    if account and account.login and result.open_positions:
        db.replace_open_positions(conn, account.login, result.open_positions)
    db.log_import(conn, source, added, updated, "; ".join(result.warnings))
    return result.to_dict()


def _authenticated(authorization: Optional[str]) -> bool:
    return not settings.api_token or authorization == f"Bearer {settings.api_token}"


@app.get("/api/health")
def health(authorization: Optional[str] = Header(default=None)) -> dict:
    """Open endpoint, so uptime checks work — but it tells an anonymous caller
    nothing beyond "this is a TradingApp and it wants a token"."""
    if not _authenticated(authorization):
        return {"status": "ok", "auth_required": True, "authenticated": False}
    trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    return {
        "status": "ok",
        "auth_required": bool(settings.api_token),
        "authenticated": True,
        "trades": trades,
        "db": str(settings.db_path),
        "mt5_configured": settings.mt5_login is not None or settings.mt5_path is not None,
        "last_import": db.last_import(conn),
    }


@app.get("/api/account", dependencies=[Depends(require_token)])
def account_state() -> dict:
    account = db.get_account(conn)
    trades = db.load_trades(conn)
    opening, estimated = _opening_balance(trades)
    return {
        "account": account.to_dict() if account else None,
        "opening_balance": round(opening, 2),
        "opening_balance_estimated": estimated,
        "open_positions": db.get_open_positions(conn),
        "trades": len(trades),
    }


@app.get("/api/filters", dependencies=[Depends(require_token)])
def filter_options() -> dict:
    first, last = db.date_bounds(conn)
    magics = [
        row[0] for row in conn.execute("SELECT DISTINCT magic FROM trades ORDER BY magic")
    ]
    return {
        "symbols": db.distinct_symbols(conn),
        "magics": magics,
        "date_from": first[:10] if first else None,
        "date_to": last[:10] if last else None,
    }


@app.get("/api/summary", dependencies=[Depends(require_token)])
def summary_endpoint(filters: Filters = Depends(get_filters)) -> dict:
    trades = _load(filters)
    opening, estimated = _opening_balance(trades)
    data = summary(trades, opening)
    data["opening_balance_estimated"] = estimated
    return data


@app.get("/api/equity", dependencies=[Depends(require_token)])
def equity_endpoint(filters: Filters = Depends(get_filters)) -> dict:
    trades = _load(filters)
    opening, _ = _opening_balance(trades)
    return {"opening_balance": round(opening, 2), "points": equity_curve(trades, opening)}


@app.get("/api/symbols", dependencies=[Depends(require_token)])
def symbols_endpoint(filters: Filters = Depends(get_filters)) -> dict:
    return by_symbol(_load(filters))


@app.get("/api/time", dependencies=[Depends(require_token)])
def time_endpoint(filters: Filters = Depends(get_filters)) -> dict:
    return by_time(_load(filters))


@app.get("/api/risk", dependencies=[Depends(require_token)])
def risk_endpoint(filters: Filters = Depends(get_filters)) -> dict:
    return risk_report(_load(filters))


@app.get("/api/trades", dependencies=[Depends(require_token)])
def trades_endpoint(
    filters: Filters = Depends(get_filters),
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    sort: str = Query("close_time"),
    desc: bool = Query(True),
) -> dict:
    trades = _load(filters)
    keys = {
        "close_time": lambda t: t.close_time,
        "open_time": lambda t: t.open_time,
        "net": lambda t: t.net,
        "symbol": lambda t: t.symbol,
        "volume": lambda t: t.volume,
        "duration": lambda t: t.duration_minutes,
    }
    trades.sort(key=keys.get(sort, keys["close_time"]), reverse=desc)
    window = trades[offset : offset + limit]
    return {
        "total": len(trades),
        "offset": offset,
        "limit": limit,
        "rows": [t.to_dict() for t in window],
    }


@app.get("/api/dashboard", dependencies=[Depends(require_token)])
def dashboard(filters: Filters = Depends(get_filters)) -> dict:
    """Everything the UI needs in one request, so a filter change is one round trip."""
    trades = _load(filters)
    opening, estimated = _opening_balance(trades)
    account = db.get_account(conn)
    stats = summary(trades, opening)
    stats["opening_balance_estimated"] = estimated
    return {
        "summary": stats,
        "equity": equity_curve(trades, opening),
        "symbols": by_symbol(trades),
        "time": by_time(trades),
        "risk": risk_report(trades),
        "account": account.to_dict() if account else None,
        "open_positions": db.get_open_positions(conn),
        "currency": account.currency if account else "USD",
    }


@app.post("/api/import/file", dependencies=[Depends(require_token)])
async def import_file(file: UploadFile = File(...)) -> dict:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Пустой файл")
    try:
        rows = read_rows(file.filename or "", payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = parse_statement(rows, source="statement")
    if not result.trades:
        raise HTTPException(
            status_code=422,
            detail=result.warnings[0] if result.warnings else "Сделок не найдено",
        )
    return _store(result, "statement")


class TradePayload(BaseModel):
    """Wire format for the Windows agent, mirroring the Trade dataclass."""

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


class AccountPayload(BaseModel):
    login: str
    name: str = ""
    server: str = ""
    currency: str = "USD"
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0
    leverage: int = 0


class PushPayload(BaseModel):
    account: AccountPayload
    trades: list[TradePayload] = Field(default_factory=list)
    open_positions: list[dict] = Field(default_factory=list)


@app.post("/api/import/push", dependencies=[Depends(require_token)])
def import_push(payload: PushPayload) -> dict:
    """Ingest endpoint for `tools/mt5_agent.py` running next to the terminal."""
    from .models import ImportResult, OpenPosition

    account = Account(**payload.account.model_dump(), source="mt5_agent",
                      updated_at=datetime.now(timezone.utc))
    trades = [
        Trade(**t.model_dump(), account_login=account.login, source="mt5_agent")
        for t in payload.trades
    ]
    positions = []
    for raw in payload.open_positions:
        try:
            positions.append(OpenPosition(**raw))
        except TypeError:
            continue
    result = ImportResult(
        source="mt5_agent",
        trades=trades,
        trades_seen=len(trades),
        account=account,
        open_positions=positions,
    )
    return _store(result, "mt5_agent")


@app.post("/api/sync/mt5", dependencies=[Depends(require_token)])
def sync_mt5() -> dict:
    """Read the local MT5 terminal directly (Windows only)."""
    mt5_settings = mt5_live.MT5Settings(
        login=settings.mt5_login,
        password=settings.mt5_password,
        server=settings.mt5_server,
        terminal_path=settings.mt5_path,
        history_days=settings.mt5_history_days,
    )
    try:
        result = mt5_live.sync(mt5_settings)
    except mt5_live.MT5Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _store(result, "mt5_live")


@app.get("/api/export/trades.csv", dependencies=[Depends(require_token)])
def export_csv(filters: Filters = Depends(get_filters)) -> StreamingResponse:
    trades = _load(filters)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["ticket", "symbol", "side", "volume", "open_time", "close_time", "open_price",
         "close_price", "sl", "tp", "profit", "commission", "swap", "fee", "net",
         "duration_minutes", "risk_money", "magic", "comment"]
    )
    for t in trades:
        writer.writerow(
            [t.ticket, t.symbol, t.side, t.volume, t.open_time.isoformat(),
             t.close_time.isoformat(), t.open_price, t.close_price, t.sl or "", t.tp or "",
             t.profit, t.commission, t.swap, t.fee, round(t.net, 2),
             round(t.duration_minutes, 1),
             round(r, 2) if (r := t.risk_money()) else "", t.magic, t.comment]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="trades.csv"'},
    )


@app.delete("/api/trades", dependencies=[Depends(require_token)])
def clear(confirm: bool = Query(False)) -> dict:
    if not confirm:
        raise HTTPException(status_code=400, detail="Нужен параметр confirm=true")
    removed = db.clear_trades(conn)
    return {"deleted": removed}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
