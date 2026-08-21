"""portfolio_woche.py — fuehrt einen woechentlichen Sparplan-Einkauf im Paper-Depot aus.

Manuell auszuloesen (kein Standing-Listener). Aufruf z.B. "Sparplan-Woche
buchen", dann wird dieses Script ausgefuehrt.

Was es tut:
  1. Nimmt die aktuell in der Aktivierung hinterlegte Saeulen-Gewichtung.
  2. Rechnet das EUR-Wochenbudget je Position in die Notierungswaehrung um
     (aktueller FX-Kurs via yfinance), holt den aktuellen Kurs, kauft
     (akkumuliert in einer eigenen Positions-Ablage, siehe unten).
  3. Schreibt einen Log-Eintrag in data/sparplan-wochen-log.json
     (Grundlage fuer "Gesamt investiert" -- unabhaengig von FX-Schwankungen
     bei der Kursverfolgung, weil hier die tatsaechlich eingesetzten
     EUR-Betraege archiviert werden, nicht aus Positionsdaten zurueckgerechnet).

Eigene Positionen liegen in data/sparplan-positionen.json -- vollstaendig
isoliert von der gemeinsamen DB-Tabelle. So koennen mehrere Sparplan-Track-
Definitionen parallel laufen, ohne sich zu ueberlagern.

Aufruf: python scripts/portfolio_woche.py [--dry-run]
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import konsole_utf8
konsole_utf8()

import yfinance as yf
from data_layer import get_performance, get_info

LOG_JSON = os.path.normpath(os.path.join(_ROOT, "data", "sparplan-wochen-log.json"))
POSITIONEN_JSON = os.path.normpath(os.path.join(_ROOT, "data", "sparplan-positionen.json"))
WOCHEN_BUDGET_EUR = 100.0


def lade_positionen():
    if not os.path.exists(POSITIONEN_JSON):
        return {}
    try:
        with open(POSITIONEN_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def speichere_positionen(pos):
    with open(POSITIONEN_JSON, "w", encoding="utf-8") as f:
        json.dump(pos, f, ensure_ascii=False, indent=2)


def akkumuliere(pos, ticker, name, saeule, kurs, stueck):
    """Wie cmd_add's Logik (gewichteter neuer Einstandskurs), aber in der eigenen,
    isolierten Ablage statt der geteilten DB."""
    if ticker in pos:
        alt = pos[ticker]
        neu_stueck = alt["stueckzahl"] + stueck
        alt_wert = alt["stueckzahl"] * alt["kauf_kurs"]
        neu_wert = alt_wert + stueck * kurs
        neu_kurs = neu_wert / neu_stueck if neu_stueck else kurs
        pos[ticker]["stueckzahl"] = round(neu_stueck, 6)
        pos[ticker]["kauf_kurs"] = round(neu_kurs, 4)
        pos[ticker]["saeule"] = saeule
    else:
        pos[ticker] = {
            "name": name, "saeule": saeule, "kauf_kurs": round(kurs, 4),
            "stueckzahl": round(stueck, 6), "erste_aktivierung": datetime.now().strftime("%Y-%m-%d"),
        }

# Aktivierungsliste (Stand 2026-08-16 -- korrigierte erste echte Empfehlung nach vollem
# 50-Titel-Re-Screen mit hartem Momentum-Gate, siehe investmentthese.md "Aktivierungsliste").
# Vorherige Version (INOD/ORCL/CPNG/1810.HK) war fehlerhaft -- INOD lag bei ~33% der
# 52W-Range statt <20%, ORCL erfuellt das Momentum-Gate nicht (Kurs < SMA50 trotz RSI>50).
# Nach strikter Anwendung aller 5 Kriterien (Tief<20% + Konsens + Analysten-Qualitaet +
# Kursziel-Verlaesslichkeit + hartes Momentum-Gate RSI>=50 UND Kurs>SMA50) bestand nur PSIX
# sauber. RZLV bestand das Momentum-Gate mechanisch (RSI 55,4, Kurs>SMA50), aber das Kursziel
# (+293% auf nur 5 Analysten) wirkte unverlaesslich -- offen gelassen fuer Niks Entscheidung.
# Nik hat sich am 2026-08-16 explizit fuer die Aufnahme entschieden (informierte Entscheidung
# trotz der dokumentierten Kursziel-Vorsicht). KI-Infra und China/Asien haben weiterhin keinen
# Kandidaten, der alle Kriterien erfuellt -- deren Budget bleibt ungenutzt.
# Von Hand synchron halten -- Aenderungssperre: fruehestens alle 2 Wochen UND erst nach
# mindestens 2 Ausfuehrungen der aktuellen Konfiguration.
AKTIVIERUNG = [
    {"ticker": "PSIX", "saeule": "Klima/Energie", "eur": 25.00, "waehrung": "USD"},
    {"ticker": "RZLV", "saeule": "Software-Resilienz", "eur": 25.00, "waehrung": "USD"},
]

FX_TICKER = {"USD": "EURUSD=X", "HKD": "EURHKD=X", "EUR": None}


def fx_kurs(waehrung):
    if waehrung == "EUR":
        return 1.0
    t = FX_TICKER[waehrung]
    df = yf.Ticker(t).history(period="5d")
    return float(df["Close"].iloc[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts kaufen/loggen")
    args = ap.parse_args()

    summe = sum(a["eur"] for a in AKTIVIERUNG)
    if summe < WOCHEN_BUDGET_EUR - 0.01:
        print(f"Hinweis: nur {summe:.2f} von {WOCHEN_BUDGET_EUR:.2f} EUR aktiviert "
              f"({WOCHEN_BUDGET_EUR - summe:.2f} EUR bleiben ungenutzt -- keine Saeule ohne "
              f"qualifizierten Kandidaten wird zwangsbespart, siehe Sparplan-Gewichtung-Regel).")
    elif summe > WOCHEN_BUDGET_EUR + 0.01:
        print(f"WARNUNG: Aktivierungsliste summiert auf {summe:.2f} EUR, mehr als das "
              f"Budget von {WOCHEN_BUDGET_EUR:.2f} EUR.")

    kaeufe = []
    for a in AKTIVIERUNG:
        fx = fx_kurs(a["waehrung"])
        native_betrag = a["eur"] * fx
        kurs = get_performance(a["ticker"])
        if kurs is None:
            print(f"  {a['ticker']}: kein Kurs erhalten, uebersprungen.")
            continue
        stueck = native_betrag / kurs
        kaeufe.append({
            "ticker": a["ticker"], "saeule": a["saeule"], "eur": a["eur"],
            "waehrung": a["waehrung"], "fx_kurs": fx, "native_betrag": round(native_betrag, 2),
            "kurs": kurs, "stueck": round(stueck, 6),
        })
        print(f"  {a['ticker']:10s} [{a['saeule']:20s}] {a['eur']:.2f} EUR -> "
              f"{native_betrag:.2f} {a['waehrung']} @ {kurs:.2f} = {stueck:.4f} Stk")

    if args.dry_run:
        print("\n--dry-run: nichts gekauft, nichts geloggt.")
        return

    positionen = lade_positionen()
    for k in kaeufe:
        info = get_info(k["ticker"]) or {}
        name = info.get("shortName") or k["ticker"]
        akkumuliere(positionen, k["ticker"], name, k["saeule"], k["kurs"], k["stueck"])
    speichere_positionen(positionen)

    log = {"wochen": []}
    if os.path.exists(LOG_JSON):
        try:
            with open(LOG_JSON, "r", encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            log = {"wochen": []}
    log["wochen"].append({
        "datum": datetime.now(timezone.utc).isoformat(),
        "budget_eur": WOCHEN_BUDGET_EUR,
        "eingesetzt_eur": round(summe, 2),
        "kaeufe": kaeufe,
    })
    with open(LOG_JSON, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    # WICHTIG: "eingesetzt_eur" (tatsaechlich gekauft), nicht "budget_eur" (Obergrenze) --
    # sonst wird bei Teil-Deployment (leere Saeulen) die Gesamtsumme systematisch zu hoch
    # ausgewiesen. budget_eur bleibt im Log als Kontext, wie viel maximal moeglich gewesen waere.
    gesamt_investiert = sum(w.get("eingesetzt_eur", w["budget_eur"]) for w in log["wochen"])
    gesamt_budget = sum(w["budget_eur"] for w in log["wochen"])
    print(f"\nWoche gebucht. Tatsaechlich eingesetzt bisher: {gesamt_investiert:.2f} EUR "
          f"von {gesamt_budget:.2f} EUR Budget ({len(log['wochen'])} Wochen). Log: {LOG_JSON}")


if __name__ == "__main__":
    main()
