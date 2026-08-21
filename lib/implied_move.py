"""E2 — Implied Move vs. Earnings-Historie (Code-only).

Vor Earnings: ATM-Straddle der naechsten Expiry nach dem Event = erwarteter
Move. Historische Earnings-Moves aus yfinance (Close-to-Close um Event).
P4 Pre-Event injiziert die Zahlen.

Pitfalls (SKILL.md):
- Nur Ticker mit openInterest > 100 am ATM-Strike rechnen, sonst n/a.
  .V-Ticker komplett skippen.
- Mid-Preis (bid/ask) statt lastPrice verwenden.
- Straddle enthaelt Zeitwert -> bei Expiry >7 Tage nach Event als Obergrenze.
- Earnings-Historie via Finnhub (free). Fallback: yfinance Event-Schaetzung.
"""
import yfinance as yf
from datetime import datetime, timedelta
from paths import load_config
import requests


def _next_expiry_after_earnings(expiries, earnings_date):
    """Erste Expiry, die NACH dem Earnings-Datum liegt."""
    if not expiries:
        return None
    for e in sorted(expiries):
        try:
            ed = datetime.strptime(e, "%Y-%m-%d")
            if ed.date() > earnings_date.date():
                return e
        except Exception:
            continue
    return expiries[-1]  # fallback: letzte verfuegbare


def implied_move(ticker, earnings_date=None):
    """Liefert (implied_move_pct, detail_dict) oder (None, Grund)."""
    if ".V" in ticker or ".L" in ticker or ".TO" in ticker:
        return None, "illiquider Nicht-US-Ticker (.V) -> uebersprungen"
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist is None or hist.empty:
            return None, "keine Kursdaten"
        spot = float(hist["Close"].iloc[-1])
        if not t.options:
            return None, "keine Optionen"
        exp = _next_expiry_after_earnings(t.options, earnings_date or datetime.now())
        chain = t.option_chain(exp)
        # ATM-Strike
        atm_idx = (chain.calls["strike"] - spot).abs().argsort()[:1]
        atm_call = chain.calls.iloc[atm_idx]
        atm_put = chain.puts.iloc[(chain.puts["strike"] - spot).abs().argsort()[:1]]
        oi = float(atm_call["openInterest"].iloc[0])
        if oi < 100:
            return None, f"openInterest {oi:.0f} < 100 -> unzuverlaessig (n/a)"
        # Mid-Preis
        c = atm_call
        p = atm_put
        call_mid = (float(c["bid"].iloc[0]) + float(c["ask"].iloc[0])) / 2 or float(c["lastPrice"].iloc[0])
        put_mid = (float(p["bid"].iloc[0]) + float(p["ask"].iloc[0])) / 2 or float(p["lastPrice"].iloc[0])
        straddle = call_mid + put_mid
        move = straddle / spot
        # Obergrenze wenn Expiry weit nach Event
        days_to_exp = (datetime.strptime(exp, "%Y-%m-%d") - (earnings_date or datetime.now())).days
        ceiling = days_to_exp > 7
        return move, {"spot": spot, "straddle": straddle, "expiry": exp,
                      "is_ceiling": ceiling, "open_interest": oi}
    except Exception as e:
        return None, f"Fehler: {type(e).__name__}"


def historical_earnings_moves(ticker, n=8):
    """Finnhub Earnings-Historie -> Close-to-Close-Moves via yfinance.
    Liefert (mean_abs_move, max_abs_move, anzahl, details)."""
    cfg = load_config()
    key = cfg.get("finnhub_api_key")
    events = []
    if key:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/stock/earnings",
                params={"symbol": ticker, "token": key}, timeout=15,
            )
            if r.status_code == 200:
                for e in r.json()[:n]:
                    if e.get("date"):
                        events.append(e["date"])
        except Exception:
            pass
    if not events:
        return None, None, 0, "keine Earnings-Historie (Finnhub-Key fehlt?)"
    moves = []
    for d in events:
        try:
            df = yf.Ticker(ticker).history(start=datetime.strptime(d, "%Y-%m-%d").date() - timedelta(days=2),
                                           end=datetime.strptime(d, "%Y-%m-%d").date() + timedelta(days=3),
                                           auto_adjust=True)
            if df is None or len(df) < 2:
                continue
            m = abs((df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100)
            moves.append(m)
        except Exception:
            continue
    if not moves:
        return None, None, 0, "Historie ohne Kursdaten"
    return (round(sum(moves) / len(moves), 1), round(max(moves), 1),
            len(moves), moves)


def build_e2_context(ticker, earnings_date=None):
    """Kombinierter Kontext fuer P4-Pre-Event-Injektion."""
    mv, det = implied_move(ticker, earnings_date)
    mean, mx, cnt, hist = historical_earnings_moves(ticker)
    lines = []
    if mv is not None:
        flag = " (Obergrenze, Expiry >7d nach Event)" if det.get("is_ceiling") else ""
        lines.append(f"Optionsmarkt preist ±{mv*100:.1f}% Move{flag}.")
    else:
        lines.append(f"Implied Move: n/a ({det})")
    if mean is not None:
        verdict = ""
        if mv is not None and mv * 100 < mean:
            verdict = " -> Markt unterschaetzt Event-Risiko vs. eigene Historie."
        lines.append(f"Historisch (letzte {cnt} Earnings): ±{mean}% im Schnitt, "
                     f"max ±{mx}%{verdict}")
    else:
        lines.append(f"Historie: {hist}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(build_e2_context("NVDA"))
