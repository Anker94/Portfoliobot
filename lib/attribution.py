"""E6 — Decision-Journal & Thesen-Attribution (Code + DB).

Jede Empfehlung speichert erwarteter_treiber (P3 liefert ihn im JSON).
Beim Outcome-Check (outcome_calculator) wird die Treiber-Zeitreihe gezogen und
die Vier-Felder-Matrix bestimmt. P7 bekommt die Matrix statt roher Trefferquote.

Vier Felder:
  aufgegangen + Treiber ok        -> treffer (Reasoning bestaetigt)
  aufgegangen + Treiber falsch    -> glueck (Warnung, zaehlt NICHT als Treffer)
  nicht aufgegangen + Treiber ok  -> link_kaputt (These-Review)
  nicht aufgegangen + Treiber falsch -> fehler (sauberer Fehler)
"""
from data_layer import safe_history
from datetime import datetime, timedelta
from db import get_recommendations, update_recommendation


def _treiber_series(treiber_dict, since_iso, days=35):
    """Zieht Treiber-Zeitreihe seit Empfehlung; liefert True wenn Treiber
    wie erwartet lief (richtung erfuellt)."""
    if not isinstance(treiber_dict, dict):
        return None
    sym = treiber_dict.get("treiber")
    richtung = treiber_dict.get("richtung")  # "steigend"/"fallend"
    if not sym or richtung not in ("steigend", "fallend"):
        return None
    try:
        d = datetime.fromisoformat(since_iso).date()
    except Exception:
        return None
    df = safe_history(sym, period="2mo")
    if df is None or len(df) < 5:
        return None
    sub = df[df.index.date >= d]
    if len(sub) < 2:
        return None
    chg = sub["Close"].iloc[-1] / sub["Close"].iloc[0] - 1
    if richtung == "steigend":
        return chg > 0
    return chg < 0


def attribute(rec):
    """Bestimmt attribution fuer eine Empfehlung mit Outcome.
    rec muss Felder: erwarteter_treiber (text/json), outcome_30d, datum haben."""
    erwartet = rec.get("erwarteter_treiber")
    if isinstance(erwartet, str):
        import json
        try:
            erwartet = json.loads(erwartet)
        except Exception:
            erwartet = None
    outcome = rec.get("outcome_30d")
    if outcome is None:
        return None
    treiber_ok = _treiber_series(erwartet, rec.get("datum"))
    aufgegangen = outcome > 0
    if aufgegangen and treiber_ok:
        return "treffer"
    if aufgegangen and treiber_ok is False:
        return "glueck"
    if (not aufgegangen) and treiber_ok:
        return "link_kaputt"
    if (not aufgegangen) and treiber_ok is False:
        return "fehler"
    return "unbekannt"


def run_attribution():
    """Monatslauf: alle Empfehlungen mit Outcome attributieren."""
    recs = get_recommendations(limit=500)
    counts = {"treffer": 0, "glueck": 0, "link_kaputt": 0, "fehler": 0,
              "unbekannt": 0}
    for r in recs:
        if r.get("outcome_checked"):
            a = attribute(r)
            if a and a in counts:
                counts[a] += 1
                update_recommendation(r["id"], attribution=a)
    return counts


def summarize_for_p7():
    counts = run_attribution()
    total = sum(v for k, v in counts.items() if k != "unbekannt")
    echte = counts["treffer"]
    # "glueck" zaehlt NICHT als Treffer (siehe SKILL.md)
    line = (f"Attribution: {counts['treffer']} echte Treffer, "
            f"{counts['glueck']} 'richtig aus falschem Grund' (Warnung, "
            f"zaehlt NICHT), {counts['link_kaputt']} Link-kaputt, "
            f"{counts['fehler']} saubere Fehler (von {total} bewertet).")
    return line, counts


if __name__ == "__main__":
    print(summarize_for_p7())
