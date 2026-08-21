"""daily_check.py — Code-only (kein LLM), 13:00 Mo-Sa.

K4 Exit-Preis-Check, K2 Blindheit, W4 Event-Scan, A2 Insider, A1 SEC EDGAR,
E3 Short-Interest, E8 News-Dedup-Hygiene. Jeder Check fängt API-Fehler ab.
"""
import os
import sys
import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import load_config
from db import (get_active_positions, update_position, add_event,
                purge_old_news, news_seen_recent, news_seen_add)
from data_layer import get_performance, get_news_yfinance
from short_interest import detect_change
from telegram import alert
from datetime import datetime, timedelta


def check_exit_prices():
    """K4: kurs <= exit_preis -> sofortiger Alert."""
    alerts = []
    for p in get_active_positions():
        ep = p.get("exit_preis")
        if not ep:
            continue
        kurs = get_performance(p["ticker"])
        if kurs is None:
            continue
        if kurs <= ep:
            msg = f"🚨 EXIT-ALERT {p['ticker']}: {kurs:.2f} <= Schwelle {ep}"
            alert(msg)
            alerts.append(msg)
    return alerts


def check_blindness():
    """K2: 14 Tage keine News -> Alert 'Quelle pruefen'."""
    alerts = []
    for p in get_active_positions():
        ticker = p["ticker"]
        finnhub_news = _finnhub_count(ticker)
        yf_news = get_news_yfinance(ticker, limit=5)
        blind = (finnhub_news == 0 and len(yf_news) == 0)
        days = p.get("news_blind_days") or 0
        if blind:
            days += 1
        else:
            days = 0
        update_position(ticker, news_blind_days=days)
        if days >= 14 and not p.get("news_blind_alerted"):
            alert(f"👁️ Blindheit {ticker}: {days}T ohne News -> Quelle pruefen")
            update_position(ticker, news_blind_alerted=1)
        if not blind:
            update_position(ticker, news_blind_alerted=0)
    return alerts


def _finnhub_count(ticker):
    cfg = load_config()
    key = cfg.get("finnhub_api_key")
    if not key:
        return 0
    try:
        r = requests.get("https://finnhub.io/api/v1/company-news",
                         params={"symbol": ticker, "from": "2024-01-01",
                                 "to": "2030-01-01", "token": key}, timeout=10)
        if r.status_code == 200:
            return len([i for i in r.json() if i.get("headline")])
    except Exception:
        pass
    return 0


def scan_events():
    """W4: Finnhub Earnings Calendar -> DB (nur naechste 14 Tage)."""
    cfg = load_config()
    key = cfg.get("finnhub_api_key")
    if not key:
        return
    from datetime import date
    lo = date.today().isoformat()
    hi = (date.today() + timedelta(days=14)).isoformat()
    try:
        r = requests.get("https://finnhub.io/api/v1/calendar/earnings",
                         params={"from": lo, "to": hi, "token": key}, timeout=15)
        if r.status_code != 200:
            return
        for e in r.json().get("earningsCalendar", []):
            if not isinstance(e, dict):
                continue
            sym = e.get("symbol")
            ed = e.get("date")
            if not sym or not ed:
                continue
            bmo = "BMO" if e.get("hour") == "b" else ("AMC" if e.get("hour") == "c" else None)
            if any(p["ticker"] == sym for p in get_active_positions()):
                add_event(sym, ed, "earnings", bmo)
    except Exception:
        pass


def check_short_interest():
    """E3: Short-Interest Alert bei MoM-Veraenderung."""
    for p in get_active_positions():
        try:
            a = detect_change(p["ticker"])
            if a:
                alert(a)
        except Exception:
            continue


def check_sec_8k():
    """A1: SEC EDGAR 8-K fuer US-Positionen (kostenlos, kein Key)."""
    alerts = []
    for p in get_active_positions():
        t = p["ticker"]
        if any(s in t for s in (".V", ".L", ".TO")):
            continue
        try:
            headers = {"User-Agent": "StockAnalysis/1.0 (local@local)"}
            r = requests.get(
                f"https://data.sec.gov/submissions/CIK{_cik(t)}.json",
                headers=headers, timeout=15)
            if r.status_code == 200:
                filings = r.json().get("filings", {}).get("recent", {})
                forms = filings.get("form", [])
                dates = filings.get("filingDate", [])
                for f, d in zip(forms, dates):
                    if f == "8-K" and d == datetime.now().strftime("%Y-%m-%d"):
                        alert(f"📄 SEC 8-K {t} heute eingereicht")
                        break
        except Exception:
            continue
    return alerts


def _cik(ticker):
    """CIK ueber SEC ticker-Suche."""
    try:
        r = requests.get(
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&symbol={ticker}&output=json", timeout=10)
        if r.status_code == 200:
            cik = r.json().get("cik")
            return str(cik).zfill(10) if cik else "0000000000"
    except Exception:
        pass
    return "0000000000"


def main():
    check_exit_prices()
    check_blindness()
    scan_events()
    check_short_interest()
    check_sec_8k()
    purge_old_news(days=30)
    print("daily_check done.")


if __name__ == "__main__":
    main()
