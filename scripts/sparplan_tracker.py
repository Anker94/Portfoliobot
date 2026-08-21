"""sparplan_tracker.py — trackt Sparplan-Empfehlungen gegen den aktuellen Kurs.

Reines Forward-Tracking: Positionen werden bei Aktivierung ueber
portfolio_woche.py in data/sparplan-positionen.json angelegt, dieses
Script holt fuer alle dort gefuehrten Positionen den aktuellen Kurs und
berechnet die Performance seit Anlage.

Aufruf: python scripts/sparplan_tracker.py
Schreibt zusaetzlich data/sparplan-tracking.json fuer das Dashboard.

Liest ausschliesslich aus sparplan-positionen.json (vollstaendig isoliert
von der gemeinsamen DB-Tabelle), so koennen mehrere Sparplan-Track-
Definitionen parallel laufen, ohne sich zu ueberlagern.
"""
import os
import sys
import json
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import konsole_utf8
konsole_utf8()

from data_layer import get_performance

OUT_JSON = os.path.normpath(os.path.join(_ROOT, "data", "sparplan-tracking.json"))
WOCHEN_LOG = os.path.normpath(os.path.join(_ROOT, "data", "sparplan-wochen-log.json"))
POSITIONEN_JSON = os.path.normpath(os.path.join(_ROOT, "data", "sparplan-positionen.json"))


def lade_positionen():
    if not os.path.exists(POSITIONEN_JSON):
        return {}
    try:
        with open(POSITIONEN_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def kosten_pro_ticker():
    """Liest sparplan-wochen-log.json und summiert je Ticker: EUR investiert (die tatsaechlich
    eingesetzten Betraege, unabhaengig von FX-Schwankungen danach) und den nativen Betrag
    (Waehrung am Kaufkurs) der letzten Ausfuehrung."""
    if not os.path.exists(WOCHEN_LOG):
        return {}
    try:
        with open(WOCHEN_LOG, "r", encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        return {}
    out = {}
    for woche in log.get("wochen", []):
        for k in woche.get("kaeufe", []):
            t = k["ticker"]
            if t not in out:
                out[t] = {"eur_gesamt": 0.0, "ausfuehrungen": 0, "waehrung": k.get("waehrung")}
            out[t]["eur_gesamt"] += k.get("eur", 0.0)
            out[t]["ausfuehrungen"] += 1
            out[t]["letzter_nativer_betrag"] = k.get("native_betrag")
    for v in out.values():
        v["eur_gesamt"] = round(v["eur_gesamt"], 2)
    return out

# Analystenkonsens-Kurszziele, manuell aus der Wissensdatenbank uebernommen (Stand 2026-08-16).
# Bewusst nicht per Regex aus den .md-Dateien geparst -- die Formulierungen sind Prosa und
# streuen zu stark (Median vs. Durchschnitt, verschiedene Waehrungen) fuer verlaessliches
# automatisches Parsen. Bei neuen Aktivierungen hier von Hand nachtragen, Quelle: das jeweilige
# "Kursziel"-Vorkommen im BEWERTUNG-Abschnitt der Ticker-Datei.
KURSZIELE = {
    "INOD": {"ziel": 90.0, "quelle": "Median, 5 Analysten (Spanne 75-110, Durchschnitt 122.75)"},
    "PSIX": {"ziel": 69.79, "quelle": "Strong Buy, nur 3 Analysten -- geringe Aussagekraft"},
    "ORCL": {"ziel": 246.43, "quelle": "Buy, 44 Analysten -- Vorsicht: Delta wirkt nicht an juengsten Kursverfall angepasst"},
    "CPNG": {"ziel": 23.82, "quelle": "Buy, 18 Analysten"},
    "1810.HK": {"ziel": 38.13, "quelle": "Buy, 31 Analysten -- Kursziel in HKD, nicht EUR/USD"},
}


def verkaufsranges(kauf_kurs, ziel):
    """Gestaffelte Zonen statt einem harten Trigger -- Trim-Regel statt Alles-oder-Nichts,
    damit weder zu frueh komplett verkauft (Sept-2025-Muster) noch bei einer Fortsetzung nach
    oben nichts mehr realisiert wird (Nachschieben-ohne-Sichern-Muster beim Hebel-Trade)."""
    if not kauf_kurs or not ziel or ziel <= kauf_kurs:
        return None
    strecke = ziel - kauf_kurs
    return {
        "zone_1_teilverkauf": round(kauf_kurs + strecke * 0.5, 2),
        "zone_2_teilverkauf": round(ziel, 2),
        "zone_3_rest_trailing_stop": round(ziel * 1.25, 2),
    }


def main():
    positionen = lade_positionen()
    if not positionen:
        print("Keine Positionen in sparplan-positionen.json gefunden.")
        return

    kosten = kosten_pro_ticker()

    ergebnisse = []
    gesamt_investiert_eur = 0.0
    gesamt_wert_eur = 0.0
    for ticker, p in positionen.items():
        kauf = p.get("kauf_kurs")
        aktuell = get_performance(ticker)
        perf_pct = None
        if aktuell is not None and kauf:
            perf_pct = round((aktuell / kauf - 1) * 100, 2)
        kz = KURSZIELE.get(ticker)
        ranges = verkaufsranges(kauf, kz["ziel"]) if kz else None
        abstand_zum_ziel_pct = round((kz["ziel"] / aktuell - 1) * 100, 2) if (kz and aktuell) else None
        k = kosten.get(ticker, {})
        eur_investiert = k.get("eur_gesamt")
        wert_eur = None
        if eur_investiert is not None and perf_pct is not None:
            wert_eur = round(eur_investiert * (1 + perf_pct / 100), 2)
            gesamt_investiert_eur += eur_investiert
            gesamt_wert_eur += wert_eur
        ergebnisse.append({
            "ticker": ticker,
            "name": p.get("name"),
            "saeule": p.get("saeule"),
            "angelegt_am": p.get("erste_aktivierung"),
            "kauf_kurs": kauf,
            "aktueller_kurs": aktuell,
            "performance_pct": perf_pct,
            "kursziel": kz["ziel"] if kz else None,
            "kursziel_quelle": kz["quelle"] if kz else None,
            "abstand_zum_ziel_pct": abstand_zum_ziel_pct,
            "verkaufsranges": ranges,
            "eur_investiert": eur_investiert,
            "eur_aktueller_wert": wert_eur,
            "waehrung": k.get("waehrung"),
            "ausfuehrungen": k.get("ausfuehrungen"),
        })

    ergebnisse.sort(key=lambda r: (r["performance_pct"] is None, -(r["performance_pct"] or 0)))

    print(f"Sparplan-Tracking ({len(ergebnisse)} Positionen, Stand {datetime.now().strftime('%Y-%m-%d %H:%M')}):")
    for r in ergebnisse:
        perf = f"{r['performance_pct']:+.2f}%" if r["performance_pct"] is not None else "n/a"
        ziel = f" | Ziel {r['kursziel']} ({r['abstand_zum_ziel_pct']:+.1f}%)" if r["kursziel"] else ""
        kosten_txt = f" | {r['eur_investiert']:.2f} EUR -> {r['eur_aktueller_wert']:.2f} EUR" if r["eur_investiert"] is not None else ""
        print(f"  {r['ticker']:10s} ({r['name']}) [{r['saeule']:20s}] {r['kauf_kurs']} -> {r['aktueller_kurs']}  {perf}{ziel}{kosten_txt}")

    gesamt_perf_pct = round((gesamt_wert_eur / gesamt_investiert_eur - 1) * 100, 2) if gesamt_investiert_eur else None
    print(f"\nGesamtportfolio: {gesamt_investiert_eur:.2f} EUR investiert -> {gesamt_wert_eur:.2f} EUR aktuell "
          f"({gesamt_perf_pct:+.2f}%)" if gesamt_investiert_eur else "\nGesamtportfolio: keine Kostendaten (wochen-log fehlt oder leer).")

    out = {
        "stand": datetime.now(timezone.utc).isoformat(),
        "gesamt_investiert_eur": round(gesamt_investiert_eur, 2) if gesamt_investiert_eur else None,
        "gesamt_wert_eur": round(gesamt_wert_eur, 2) if gesamt_investiert_eur else None,
        "gesamt_performance_pct": gesamt_perf_pct,
        "positionen": ergebnisse,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nGeschrieben: {OUT_JSON}")


if __name__ == "__main__":
    main()
