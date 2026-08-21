"""Financials + Volumen (regelbasiert, yfinance).

Liefert eine kompakte, gecachte Struktur je Ticker:
- Bewertung: P/E, P/B, EV/EBITDA, MarketCap
- Qualitaet: Umsatzwachs, Bruttomarge, Operative Marge, Nettomarge
- Bilanz: Verschuldung/EK, Free Cash Flow, Cash
- Interesse: Volume, 30T-Schnitt, Relative Volume (RVOL)

TradingView selbst hat keine kostenlose API; yfinance liefert die gleichen
Fundamentaldaten (aus denselben Quellen) sauber und ohne ToS-Risiko.
"""
import os, json, time
from data_layer import get_info, safe_history

_CACHE = {}
_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "financials_cache.json")
_CACHE_TTL = 24 * 3600  # 1 Tag


def _load_cache():
    global _CACHE
    if not _CACHE and os.path.exists(_CACHE_FILE):
        try:
            _CACHE = json.load(open(_CACHE_FILE, encoding="utf-8"))
        except Exception:
            _CACHE = {}


def _save_cache():
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        json.dump(_CACHE, open(_CACHE_FILE, "w", encoding="utf-8"), default=str)
    except Exception:
        pass


def get_financials(ticker, force=False):
    """Liefert Dict mit Financials + Volumen (gecacht, TTL 1 Tag)."""
    _load_cache()
    now = time.time()
    e = _CACHE.get(ticker)
    if e and not force and (now - e.get("_ts", 0) < _CACHE_TTL):
        return e
    try:
        info = get_info(ticker) or {}
        h = safe_history(ticker, period="35d")
        rvol = None
        if h is not None and not h.empty and len(h) >= 30:
            avg = h["Volume"].rolling(30).mean().iloc[-1]
            if avg and avg > 0:
                rvol = round(float(h["Volume"].iloc[-1]) / float(avg), 2)
        out = {
            "ticker": ticker,
            "pe": info.get("trailingPE"),
            "pb": info.get("priceToBook"),
            "ev_ebitda": info.get("enterpriseToEbitda"),
            "market_cap": info.get("marketCap"),
            "revenue_growth": info.get("revenueGrowth"),
            "gross_margin": info.get("grossMargins"),
            "operating_margin": info.get("operatingMargins"),
            "net_margin": info.get("profitMargins"),
            "debt_equity": info.get("debtToEquity"),
            "fcf": info.get("freeCashflow"),
            "total_cash": info.get("totalCash"),
            "volume": info.get("volume"),
            "avg_volume": info.get("averageVolume"),
            "rvol": rvol,
            "_ts": now,
        }
        _CACHE[ticker] = out
        _save_cache()
        return out
    except Exception as ex:
        return {"ticker": ticker, "error": str(ex)[:80], "_ts": now}


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "MSFT"
    print(json.dumps(get_financials(t), indent=2, default=str))
