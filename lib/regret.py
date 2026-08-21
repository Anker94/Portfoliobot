"""E7 — Exit-Outcome-Tracking / Regret-Log (Code-only).

Verkaufte Positionen laufen 90 Tage im Schatten weiter. Beantwortet:
Verkaufen wir systematisch zu frueh/zu spaet? Eliminiert Survivorship-Bias.

Logik:
- /remove setzt status='geschlossen' (db.close_position) -> exit_kurs/exit_datum.
- Monatslauf: fuer jede Schliessung <90 Tage her -> Kurs heute vs exit_kurs,
  plus SPY im selben Zeitraum. Schreibt nach_exit_30d / nach_exit_90d.
- P7-Injektion als Aggregat (Befund, kein Live-Alert).
"""
from datetime import datetime, date, timedelta
from data_layer import get_performance, safe_history
from db import get_all_positions, update_position


def _days_since(datum_iso):
    try:
        d = datetime.fromisoformat(datum_iso).date()
    except Exception:
        return None
    return (date.today() - d).days


def _spy_perf_since(datum_iso):
    """SPY-Performance seit einem Datum (via yfinance history)."""
    try:
        d = datetime.fromisoformat(datum_iso).date()
        df = safe_history("SPY", period="1y")
        if df is None:
            return None
        # naechster Handelstag nach exit
        sub = df[df.index.date >= d]
        if len(sub) < 2:
            return None
        return float((sub["Close"].iloc[-1] / sub["Close"].iloc[0] - 1) * 100)
    except Exception:
        return None


def track_exits():
    """Monatslauf: aktualisiert nach_exit_30d / nach_exit_90d fuer Schliessungen."""
    closed = get_all_positions(include_closed=True)
    closed = [p for p in closed if p.get("status") == "geschlossen"
              and p.get("exit_datum")]
    results = []
    for p in closed:
        days = _days_since(p["exit_datum"])
        if days is None or days > 90 or p.get("exit_kurs") is None:
            continue
        now = get_performance(p["ticker"])
        if now is None:
            continue
        ret = (now / p["exit_kurs"] - 1) * 100
        spy = _spy_perf_since(p["exit_datum"])
        if days >= 30:
            update_position(p["ticker"], nach_exit_30d=round(ret, 2))
        if days >= 90:
            update_position(p["ticker"], nach_exit_90d=round(ret, 2))
        results.append({
            "ticker": p["ticker"], "exit_kurs": p["exit_kurs"],
            "days": days, "perf_since_exit": round(ret, 2),
            "spy_since_exit": round(spy, 2) if spy is not None else None,
        })
    return results


def regret_summary():
    """Aggregat fuer P7-Injektion (Befund, kein Alert)."""
    closed = get_all_positions(include_closed=True)
    closed = [p for p in closed if p.get("status") == "geschlossen"]
    vals = [p["nach_exit_30d"] for p in closed if p.get("nach_exit_30d") is not None]
    if len(vals) < 5:
        return (f"Regret-Log: erst {len(vals)} Exits mit Daten "
                f"(brauche >=5 fuer Aggregat).", [])
    avg = sum(vals) / len(vals)
    # SPY-Vergleich (einfach: Durchschnitt der gespeicherten nicht verfuegbar,
    # daher nur Aktien-Seite; SPY kommt bei Vollauswertung aus track_exits)
    verdict = ("Exits kommen systematisch ZU FRUEH (Aktie steigt nach Verkauf)"
               if avg > 3 else
               "Exits kommen systematisch ZU SPAET (Aktie faellt nach Verkauf)"
               if avg < -3 else "Exit-Timing neutral")
    return (f"Letzte {len(vals)} Exits: Ø {avg:+.1f}% nach Verkauf -> {verdict}",
            vals)


if __name__ == "__main__":
    print(track_exits())
    print(regret_summary())
