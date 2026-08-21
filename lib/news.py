"""News-Triage-Daten (P1 Vorstufe). Holt Roh-News via Finnhub + yfinance.

E8 (Dedup) wird VOR der Triage angewandt -> doppelte Headlines kommen nicht
ins LLM. Diese Datei liefert nur die Rohdaten + Normalisierung; das eigentliche
Dedup ist in lib/dedup.py.
"""
import requests
from paths import load_config


def _finnhub_news(ticker, limit=10):
    cfg = load_config()
    key = cfg.get("finnhub_api_key")
    if not key:
        return []
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": ticker, "from": "2024-01-01",
                    "to": "2030-01-01", "token": key},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json()[:limit]
        return [{"headline": i.get("headline", ""), "source": i.get("source", ""),
                 "url": i.get("url", ""), "datetime": i.get("datetime")}
                for i in items if i.get("headline")]
    except Exception:
        return []


def fetch_news(ticker, yf_news=None):
    """Kombiniert Finnhub + optional yfinance .news (Zweitquelle)."""
    news = _finnhub_news(ticker)
    if not news and yf_news:
        news = [{"headline": h, "source": "yfinance", "url": "", "datetime": None}
                for h in yf_news]
    return news


if __name__ == "__main__":
    print(fetch_news("NVDA", ["Beispiel News 1", "Beispiel News 2"]))
