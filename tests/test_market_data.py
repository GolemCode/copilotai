import asyncio
from contextlib import asynccontextmanager
from datetime import date

import pandas as pd
import pytest

from app.services import market_data
from app.services.market_data import (
    build_base_currency_panel,
    infer_currency,
    needs_full_refresh,
    normalize_currency,
    parse_download_frame,
    required_fx_tickers,
)


# ---------------------------------------------------------------------------
# Devises
# ---------------------------------------------------------------------------

def test_currency_inference_from_exchange_suffix():
    assert infer_currency("CW8.PA") == "EUR"
    assert infer_currency("XEON.DE") == "EUR"
    assert infer_currency("SPY") == "USD"
    assert infer_currency("VUSA.L") == "GBp"
    assert infer_currency("EURUSD=X") is None


def test_pence_are_converted_to_pounds():
    assert normalize_currency("GBp") == ("GBP", 0.01)
    assert normalize_currency("USD") == ("USD", 1.0)
    assert required_fx_tickers({"SPY": "USD", "CW8.PA": "EUR", "VUSA.L": "GBp"}) == ["EURGBP=X", "EURUSD=X"]


# ---------------------------------------------------------------------------
# Lecture des téléchargements yfinance
# ---------------------------------------------------------------------------

def test_parse_download_keeps_raw_and_adjusted_close():
    frame = pd.DataFrame(
        {"Close": [100.0, None, 102.0], "Adj Close": [98.0, 99.0, 100.0], "Volume": [10, 20, None]},
        index=pd.to_datetime(["2026-10-20", "2026-10-21", "2026-10-22"]).rename("Date"),
    )
    rows = parse_download_frame(frame)
    # La ligne sans cours brut est écartée ; brut et ajusté restent distincts
    assert rows == [(date(2026, 10, 20), 100.0, 98.0, 10), (date(2026, 10, 22), 102.0, 100.0, 0)]


# ---------------------------------------------------------------------------
# Recollage des cours ajustés
# ---------------------------------------------------------------------------

D1, D2, D3 = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)


def test_incremental_rows_are_appended_when_adjustment_factor_is_unchanged():
    stored = {D1: (100.0, 98.0), D2: (101.0, 99.0)}
    fresh = [(D2, 101.0, 99.0, 0), (D3, 102.0, 100.0, 0)]
    assert needs_full_refresh(stored, fresh) is False


def test_intraday_bar_price_change_does_not_trigger_refresh():
    # Barre du jour enregistrée pendant la séance (103) puis clôture (105) : même facteur, pas de dividende
    stored = {D2: (101.0, 99.0), D3: (103.0, 103.0)}
    fresh = [(D2, 101.0, 99.0, 0), (D3, 105.0, 105.0, 0)]
    assert needs_full_refresh(stored, fresh) is False


def test_dividend_triggers_full_refresh():
    # Un dividende de 1 % a été détaché : Yahoo a réduit tout l'historique ajusté de 1 %
    stored = {D1: (100.0, 98.0), D2: (101.0, 99.0)}
    fresh = [(D2, 101.0, 99.0 * 0.99, 0), (D3, 101.0, 100.0, 0)]
    assert needs_full_refresh(stored, fresh) is True


def test_data_gap_triggers_full_refresh():
    # Aucune date commune : le pipeline n'a pas tourné depuis plus de 5 séances
    assert needs_full_refresh({D1: (100.0, 98.0)}, [(D3, 102.0, 100.0, 0)]) is True


class _FakeConn:
    """Connexion asyncpg minimale en mémoire : {asset_id: {date: (close, adj, volume)}}."""

    def __init__(self, data):
        self.data = data

    async def fetch(self, query, asset_id, first_date):
        return [
            {"market_date": d, "close_price": v[0], "adjusted_close": v[1]}
            for d, v in self.data.get(asset_id, {}).items() if d >= first_date
        ]

    async def executemany(self, query, records):
        for asset_id, d, close, adj, vol in records:
            self.data.setdefault(asset_id, {})[d] = (close, adj, vol)

    async def execute(self, query, asset_id):
        self.data[asset_id] = {}

    @asynccontextmanager
    async def transaction(self):
        yield


def test_sync_rebuilds_history_after_dividend(monkeypatch):
    conn = _FakeConn({1: {D1: (100.0, 98.0, 0), D2: (101.0, 99.0, 0)}})
    full_history = [(D1, 100.0, 97.0, 0), (D2, 101.0, 98.0, 0), (D3, 101.0, 100.0, 0)]

    async def fake_download(tickers, period):
        if period == market_data.INCREMENTAL_PERIOD:
            return {"SPY": [(D2, 101.0, 98.0, 0), (D3, 101.0, 100.0, 0)]}  # facteur modifié sur D2
        return {"SPY": full_history}

    monkeypatch.setattr(market_data, "download_history", fake_download)
    summary = asyncio.run(market_data.sync_market_data(conn, {"SPY": 1}))

    assert summary["full_refresh"] == ["SPY"]
    # Tout l'historique porte désormais le nouveau facteur d'ajustement
    assert {d: v[1] for d, v in conn.data[1].items()} == {D1: 97.0, D2: 98.0, D3: 100.0}


# ---------------------------------------------------------------------------
# Conversion en EUR
# ---------------------------------------------------------------------------

def _rec(ticker, d, close, adj=None):
    return {"ticker": ticker, "market_date": d, "close_price": close, "adjusted_close": adj if adj is not None else close}


def test_usd_prices_are_converted_to_eur_with_same_day_fx():
    records = [
        _rec("SPY", D1, 550.0), _rec("SPY", D2, 561.0),
        _rec("CW8.PA", D1, 500.0), _rec("CW8.PA", D2, 505.0),
        _rec("EURUSD=X", D1, 1.10), _rec("EURUSD=X", D2, 1.122),
    ]
    adjusted, close = build_base_currency_panel(records, ["SPY", "CW8.PA"], {"SPY": "USD", "CW8.PA": "EUR"})

    spy = close["SPY"].to_list()
    assert spy == pytest.approx([550.0 / 1.10, 561.0 / 1.122])
    # +2 % en USD mais l'euro s'apprécie de 2 % : rendement nul pour un investisseur en EUR
    assert spy[1] / spy[0] - 1.0 == pytest.approx(0.0, abs=1e-12)
    assert close["CW8.PA"].to_list() == [500.0, 505.0]


def test_fx_uses_last_known_rate_when_missing_that_day():
    records = [
        _rec("SPY", D1, 550.0), _rec("SPY", D2, 555.0),
        _rec("EURUSD=X", D1, 1.10),  # pas de cotation du change le 21
    ]
    _, close = build_base_currency_panel(records, ["SPY"], {"SPY": "USD"})
    assert close["SPY"].to_list() == pytest.approx([550.0 / 1.10, 555.0 / 1.10])


def test_missing_fx_series_raises_explicit_error():
    with pytest.raises(ValueError, match="EURUSD=X"):
        build_base_currency_panel([_rec("SPY", D1, 550.0)], ["SPY"], {"SPY": "USD"})
