"""End-to-end: import a statement through the HTTP API and read the dashboard back."""

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]

STATEMENT = """<html><body>
<div>Account: 4242 Name: Tester Currency: USD</div>
<table>
<tr><td>Ticket</td><td>Open Time</td><td>Type</td><td>Size</td><td>Item</td><td>Price</td>
<td>S / L</td><td>T / P</td><td>Close Time</td><td>Price</td><td>Commission</td>
<td>Taxes</td><td>Swap</td><td>Profit</td></tr>
<tr><td>1</td><td>2025.01.06 09:00</td><td>buy</td><td>0.10</td><td>EURUSD</td><td>1.10000</td>
<td>1.09000</td><td>1.12000</td><td>2025.01.06 11:00</td><td>1.11000</td><td>-0.70</td>
<td>0.00</td><td>0.00</td><td>100.00</td></tr>
<tr><td>2</td><td>2025.01.07 09:00</td><td>sell</td><td>0.10</td><td>XAUUSD</td><td>2300.00</td>
<td>2310.00</td><td>2280.00</td><td>2025.01.07 15:00</td><td>2305.00</td><td>-0.70</td>
<td>0.00</td><td>-1.00</td><td>-50.00</td></tr>
</table>
<table><tr><td>Balance:</td><td>10047.60</td></tr></table>
</body></html>"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A fresh app instance bound to a throwaway database file."""
    monkeypatch.setenv("TRADINGAPP_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("TRADINGAPP_TOKEN", raising=False)
    import backend.app.main as main

    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


def upload(client, payload=STATEMENT, name="statement.html"):
    return client.post("/api/import/file",
                       files={"file": (name, payload.encode("utf-8"), "text/html")})


def test_health_starts_empty(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["trades"] == 0
    assert body["auth_required"] is False and body["authenticated"] is True


def test_import_then_dashboard(client):
    response = upload(client)
    assert response.status_code == 200
    assert response.json()["trades_added"] == 2

    data = client.get("/api/dashboard").json()
    assert data["summary"]["trades"] == 2
    assert data["summary"]["net_profit"] == pytest.approx(100 - 0.7 - 50 - 0.7 - 1.0)
    assert data["account"]["login"] == "4242"
    assert data["currency"] == "USD"
    # Balance came from the statement, so the opening figure is derived, not guessed.
    assert data["summary"]["opening_balance_estimated"] is False
    assert data["summary"]["opening_balance"] == pytest.approx(10000.0, abs=0.01)
    assert len(data["symbols"]["rows"]) == 2
    assert data["risk"]["findings"]


def test_reimport_updates_instead_of_duplicating(client):
    upload(client)
    again = upload(client).json()
    assert again["trades_added"] == 0
    assert client.get("/api/health").json()["trades"] == 2


def test_reimport_corrects_changed_numbers(client):
    upload(client)
    corrected = STATEMENT.replace("<td>100.00</td>", "<td>120.00</td>")
    result = upload(client, corrected).json()
    assert result["trades_updated"] == 1
    assert client.get("/api/health").json()["trades"] == 2
    assert client.get("/api/summary").json()["net_profit"] == pytest.approx(67.6)


def test_filters_narrow_the_result(client):
    upload(client)
    only_eur = client.get("/api/summary?symbol=EURUSD").json()
    assert only_eur["trades"] == 1 and only_eur["net_profit"] == pytest.approx(99.3)

    by_date = client.get("/api/summary?from=2025-01-07&to=2025-01-07").json()
    assert by_date["trades"] == 1 and by_date["net_profit"] == pytest.approx(-51.7)


def test_trades_endpoint_sorts_and_pages(client):
    upload(client)
    body = client.get("/api/trades?sort=net&desc=false&limit=1").json()
    assert body["total"] == 2 and len(body["rows"]) == 1
    assert body["rows"][0]["symbol"] == "XAUUSD"


def test_csv_export_contains_a_header_and_every_trade(client):
    upload(client)
    text = client.get("/api/export/trades.csv").text
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[0].startswith("ticket,symbol,side")
    assert len(lines) == 3


def test_upload_of_a_file_without_trades_is_rejected(client):
    response = upload(client, "<html><body>no table here</body></html>", "junk.html")
    assert response.status_code == 422
    assert "отчёт" in response.json()["detail"]


def test_mt5_sync_without_a_terminal_returns_503(client):
    response = client.post("/api/sync/mt5")
    assert response.status_code == 503
    assert "MetaTrader5" in response.json()["detail"]


def test_clearing_requires_confirmation(client):
    upload(client)
    assert client.delete("/api/trades").status_code == 400
    assert client.delete("/api/trades?confirm=true").json()["deleted"] == 2
    assert client.get("/api/health").json()["trades"] == 0


def test_token_protects_the_api_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAPP_DB", str(tmp_path / "secure.db"))
    monkeypatch.setenv("TRADINGAPP_TOKEN", "s3cret")
    import backend.app.main as main

    importlib.reload(main)
    with TestClient(main.app) as client:
        assert client.get("/api/summary").status_code == 401
        ok = client.get("/api/summary", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200

        # /api/health answers anonymously so uptime checks keep working, but it
        # must not hand a stranger the trade count or the path to the database.
        anonymous = client.get("/api/health").json()
        assert anonymous == {"status": "ok", "auth_required": True, "authenticated": False}

        authorised = client.get("/api/health", headers={"Authorization": "Bearer s3cret"}).json()
        assert authorised["authenticated"] is True and "trades" in authorised

        # A wrong token must look exactly like no token at all.
        wrong = client.get("/api/health", headers={"Authorization": "Bearer nope"}).json()
        assert wrong == anonymous


def test_agent_push_ingests_the_same_way_a_statement_does(client):
    payload = {
        "account": {"login": "77001", "name": "Agent", "currency": "USD",
                    "balance": 5200.0, "equity": 5200.0},
        "trades": [{
            "ticket": "900", "symbol": "GBPUSD", "side": "buy", "volume": 1.0,
            "open_time": "2025-05-01T08:00:00+00:00",
            "close_time": "2025-05-01T11:00:00+00:00",
            "open_price": 1.25, "close_price": 1.254,
            "profit": 400.0, "commission": -7.0, "swap": -0.5,
            "sl": 1.245, "tp": 1.26, "magic": 12, "comment": "ea",
        }],
        "open_positions": [{
            "ticket": "901", "symbol": "EURUSD", "side": "sell", "volume": 0.5,
            "open_time": "2025-05-02T08:00:00+00:00", "open_price": 1.08,
            "current_price": 1.079, "profit": 50.0,
        }],
    }
    result = client.post("/api/import/push", json=payload).json()
    assert result["trades_added"] == 1

    account = client.get("/api/account").json()
    assert account["account"]["login"] == "77001"
    assert len(account["open_positions"]) == 1
    assert account["open_positions"][0]["symbol"] == "EURUSD"

    trade = client.get("/api/trades").json()["rows"][0]
    assert trade["magic"] == 12
    assert trade["net"] == pytest.approx(392.5)
