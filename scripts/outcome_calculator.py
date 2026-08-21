"""outcome_calculator.py — W1 + E6 + E7 monatlicher Lauf (Code-only, vor P5).

Fuer jede Empfehlung >=30 Tage alt: Kurs heute vs Empfehlung, SPY im selben
Zeitraum. Schreibt outcome_30d / outcome_vs_spy_30d / outcome_checked.
Schliesst E6 Attribution (implizit via attribution.py) und E7 Regret-Tracking an.
"""
import os
import sys
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from db import get_recommendations, update_recommendation, get_all_positions
from data_layer import get_performance, safe_history
from attribution import run_attribution
from regret import track_exits


def _spy_perf_since(datum_iso):
    try:
        d = datetime.fromisoformat(datum_iso).date()
        df = safe_history("SPY", period="1y")
        if df is None:
            return None
        sub = df[df.index.date >= d]
        if len(sub) < 2:
            return None
        return (sub["Close"].iloc[-1] / sub["Close"].iloc[0] - 1) * 100
    except Exception:
        return None


def calculate_outcomes():
    recs = get_recommendations(limit=1000)
    updated = 0
    for r in recs:
        if r.get("outcome_checked"):
            continue
        try:
            d = datetime.fromisoformat(r["datum"]).date()
        except Exception:
            continue
        if (datetime.now().date() - d).days < 30:
            continue
        kurs = get_performance(r["ticker"])
        if kurs is None:
            continue
        # Kauft-Kurs aus dazugehoeriger Position (fallback: letzte Empfehlung)
        base = _base_price(r["ticker"])
        if base is None:
            continue
        ret = (kurs / base - 1) * 100
        spy = _spy_perf_since(r["datum"])
        vs_spy = (ret - spy) if spy is not None else None
        update_recommendation(r["id"], outcome_30d=round(ret, 2),
                              outcome_vs_spy_30d=round(vs_spy, 2) if vs_spy is not None else None,
                              outcome_checked=1)
        updated += 1
    return updated


def _base_price(ticker):
    """Basis-Kurs = kauf_kurs der Position (oder letzter bekannter Kurs)."""
    from db import get_position
    p = get_position(ticker)
    if p and p.get("kauf_kurs"):
        return p["kauf_kurs"]
    # Fallback: Kurs zum Empfehlungsdatum via history
    try:
        recs = get_recommendations()
        for r in recs:
            if r["ticker"] == ticker:
                d = datetime.fromisoformat(r["datum"]).date()
                df = safe_history(ticker, period="1y")
                if df is not None:
                    sub = df[df.index.date >= d]
                    if len(sub):
                        return float(sub["Close"].iloc[0])
    except Exception:
        pass
    return None


def main():
    n = calculate_outcomes()
    attrs = run_attribution()
    exits = track_exits()
    print(f"outcome_calculator: {n} Empfehlungen, Attribution {attrs}, "
          f"Exits {len(exits)}.")


if __name__ == "__main__":
    main()
