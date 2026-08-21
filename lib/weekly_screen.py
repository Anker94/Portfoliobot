"""Wöchentliches Screening (regelbasiert, deterministisch).

Liefert Top-5-Kaufen / Top-5-Verkaufen über alle aktiven Positionen.
Scoring nutzt ausschliesslich die BESTEHENDEN Modelle + neue Volumen/Financials:

BUY-SCORE (0..100):
  + EV (12m)         : ev_berechnet, auf 0..40 gemappt (0%->0, >=30%->40)
  + Kelly-Gewicht    : kelly_groesse_pct, auf 0..20 (0->0, >=10%->20)
  + Regime-Fit       : +15 wenn these-Typ zu Regime passt (Growth im Growth>Value,
                       defensiv im Risk-off), sonst 0; -10 bei klarem Kontra
  + Volumen-Interesse: RVOL>=1.2 -> +10, >=0.8 -> +5, sonst 0
  + Qualitaet        : tiefe Verschuldung (<50) + gute Marge (>30%) -> +15
  - These-Bruch      : advocate_dissens -> -30 (Guardrail 2)

SELL-SCORE (0..100):
  + Typ drang        : VERKAUFEN +50, REDUZIEREN +30
  + These-Bruch      : advocate_dissens -> +25 (Guardrail 2)
  + Regime-Kontra    : Aktie läuft gegen Regime -> +15
  + Negative EV      : ev<0 -> +10
  + Schlechte Finanz  : hohe Verschuldung(>100) oder negative Marge -> +10
  - (Kauf-Signale schwächen SELL ab)

Der Score ist EXPLAINABLE: jedes Blatt wird mitgeliefert.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_active_positions
from regime import regime_vector, describe_regime
from financials import get_financials


def _clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def _last_rec(ticker):
    """Letzte Empfehlung je Ticker aus DB (dict) oder None."""
    import sqlite3, os
    db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "stock.db")
    try:
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        r = c.execute(
            "SELECT typ, ev_berechnet, kelly_groesse, advocate_dissens "
            "FROM empfehlungen WHERE ticker=? ORDER BY id DESC LIMIT 1",
            (ticker,)).fetchone()
        c.close()
        return dict(r) if r else None
    except Exception:
        return None


def score_position(pos, regime):
    t = pos["ticker"]
    # Letzte Empfehlung aus DB joinen (typ/ev/kelly/dissens)
    rec = _last_rec(t)
    typ = (rec.get("typ") if rec else None) or (pos.get("typ")) or "HALTEN"
    typ = typ.upper()
    ev = rec.get("ev_berechnet") if rec else None
    kelly = rec.get("kelly_groesse") if rec else None
    dissens = bool(rec.get("advocate_dissens")) if rec else False

    fin = get_financials(t)
    rvol = fin.get("rvol")
    de = fin.get("debt_equity")
    nm = fin.get("net_margin")

    parts = []

    # --- BUY ---
    buy = 0
    ev_v = 0 if ev is None else max(0, min(40, ev / 30 * 40))
    buy += ev_v
    parts.append(("EV", round(ev_v, 1), f"ev={ev}"))
    k_v = 0 if kelly is None else max(0, min(20, kelly / 10 * 20))
    buy += k_v
    parts.append(("Kelly", round(k_v, 1), f"kelly={kelly}"))
    # Regime-Fit
    gr = regime["growth_vs_value"].get("signal")
    ro = regime["risk_on_off"].get("signal")
    fit = 0
    if typ in ("KAUFEN", "AUFSTOCKEN"):
        if gr == 1:
            fit = 15
        elif gr == -1:
            fit = -10
    elif typ == "HALTEN":
        fit = 5 if gr == 1 else 0
    buy += fit
    parts.append(("Regime-Fit", fit, f"growth_signal={gr}"))
    # Volumen
    vol_v = 0
    if rvol is not None:
        vol_v = 10 if rvol >= 1.2 else (5 if rvol >= 0.8 else 0)
    buy += vol_v
    parts.append(("Volumen(RVOL)", vol_v, f"rvol={rvol}"))
    # Qualitaet
    q_v = 0
    if de is not None and de < 50:
        q_v += 8
    if nm is not None and nm > 0.30:
        q_v += 7
    buy += q_v
    parts.append(("Qualitaet", q_v, f"de={de}, netmargin={nm}"))
    if dissens:
        buy -= 30
        parts.append(("Dissens-Abzug", -30, "advocate these-brechend"))
    buy = _clamp(buy)

    # --- SELL ---
    sell = 0
    if typ == "VERKAUFEN":
        sell += 50
    elif typ == "REDUZIEREN":
        sell += 30
    parts.append(("Typ-Drang", 50 if typ == "VERKAUFEN" else 30 if typ == "REDUZIEREN" else 0, typ))
    if dissens:
        sell += 25
        parts.append(("These-Bruch", 25, "advocate these-brechend"))
    if ev is not None and ev < 0:
        sell += 10
        parts.append(("Negative EV", 10, f"ev={ev}"))
    if de is not None and de > 100:
        sell += 10
        parts.append(("Hohe Verschuldung", 10, f"de={de}"))
    if nm is not None and nm < 0:
        sell += 10
        parts.append(("Negative Marge", 10, f"nm={nm}"))
    sell = _clamp(sell)

    return {
        "ticker": t,
        "name": pos.get("name"),
        "typ": typ,
        "ev": ev,
        "kelly": kelly,
        "rvol": rvol,
        "buy_score": round(buy, 1),
        "sell_score": round(sell, 1),
        "parts": parts,
        "dissens": dissens,
    }


def run_screen():
    positions = get_active_positions()
    regime = regime_vector()
    regime_str = describe_regime(regime)
    rows = [score_position(p, regime) for p in positions]
    buys = sorted(rows, key=lambda r: r["buy_score"], reverse=True)[:5]
    sells = sorted(rows, key=lambda r: r["sell_score"], reverse=True)[:5]
    return {
        "regime": regime_str,
        "count": len(rows),
        "top_buy": buys,
        "top_sell": sells,
        "all": sorted(rows, key=lambda r: r["buy_score"] - r["sell_score"], reverse=True),
    }


if __name__ == "__main__":
    import pprint
    s = run_screen()
    print("REGIME:", s["regime"])
    print("\nTOP 5 KAUFEN:")
    for r in s["top_buy"]:
        print(f"  {r['ticker']:10} buy={r['buy_score']:5} sell={r['sell_score']:5} {r['typ']} ev={r['ev']}")
    print("\nTOP 5 VERKAUFEN:")
    for r in s["top_sell"]:
        print(f"  {r['ticker']:10} sell={r['sell_score']:5} buy={r['buy_score']:5} {r['typ']} ev={r['ev']}")
