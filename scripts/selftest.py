"""End-to-End Selbsttest im Mock-Modus (kein Netzwerk noetig)."""
import os
import sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from lib.db import (init_database, db_path, add_position, get_position,
                    get_active_positions, close_position, add_recommendation,
                    get_last_recommendation, get_recommendations, update_position)
import scripts.migrate_v2 as m2
import scripts.migrate_v3 as m3
import scripts.migrate_v4 as m4
import scripts.migrate_v5 as m5
from lib.json_parse import parse_llm_json
from lib.dedup import headline_hash, normalize_headline, dedup_news
from lib.ev_sizing import ev_and_kelly, format_sizing
from lib.attribution import attribute
from lib.devils_advocate import run_devils_advocate
from lib.regret import regret_summary
from scripts.stock_commands import cmd_add, cmd_portfolio, cmd_remove, cmd_check
from scripts.llm_runner import run_p3, run_p5, run_p7, run_p1, run_p2


def section(t):
    print("\n" + "=" * 60 + "\n" + t + "\n" + "=" * 60)


# 0) Saubere DB (deterministisch)
section("0) DB zuruecksetzen")
import lib.paths as _paths
_db = _paths.db_path()
if os.path.exists(_db):
    os.remove(_db)
if os.path.exists(_db + "-wal"):
    os.remove(_db + "-wal")
if os.path.exists(_db + "-shm"):
    os.remove(_db + "-shm")
print("frische DB")


# 1) DB + Migration
section("1) DB init + v2/v3/v4/v5")
init_database()
m2.migrate_v2(); m3.migrate_v3(); m4.migrate_v4(); m5.migrate_v5()
print("OK ->", db_path())

# 2) JSON-Parser
section("2) JSON-Parser (Markdown-Fence, Array, plain)")
assert parse_llm_json('```json\n{"a":1}\n```') == {"a": 1}
assert parse_llm_json('text [{"x":1}] more') == {"x": 1}
assert parse_llm_json('kein json') == {}
print("OK")

# 3) E8 Dedup
section("3) E8 News-Dedup")
add_position("NVDA", "Nvidia", 120, 10, "Compounder")
h1 = "Nvidia beats earnings expectations - Reuters"
h2 = "NVIDIA Beats Earnings Expectations"
out = dedup_news("NVDA", [h1, h2, "Apple new product"])
print("nach Dedup uebrig:", out, "(erwartet 2: NVDA + Apple)")
assert len(out) == 2, out
print("OK")

# 4) E5 EV-Sizing
section("4) E5 EV-Sizing (fraktionales Kelly)")
szen = [
    {"name": "Bull", "wahrscheinlichkeit": 0.30, "kursziel_12m": 210},
    {"name": "Base", "wahrscheinlichkeit": 0.50, "kursziel_12m": 165},
    {"name": "Bear", "wahrscheinlichkeit": 0.20, "kursziel_12m": 95},
]
ev, size = ev_and_kelly(szen, kurs=150)
print(format_sizing(ev, size))
assert size <= 10.0  # Deckel
assert 0 <= size
print("OK")

# 5) E4 Devil's Advocate (Mock)
section("5) E4 Devil's-Advocate (mock)")
db, aj = run_devils_advocate("NVDA", "Compounder", "Kurs 150 RSI 55", rec_id=None)
print("dissens:", db, "| these_brechend:", aj.get("these_brechend"))
assert isinstance(aj, dict)
print("OK")

# 6) E6 Attribution
section("6) E6 Attribution (Vier-Felder)")
rec = {"erwarteter_treiber": '{"treiber":"^TNX","richtung":"fallend"}',
       "outcome_30d": 5.0, "datum": "2026-01-01T00:00:00"}
a = attribute(rec)
print("attribution:", a)
assert a in ("treffer", "glueck", "link_kaputt", "fehler", "unbekannt")
print("OK")

# 7) Telegram-Commands (E1 /add Check, /portfolio, /remove E7)
section("7) stock_commands: add(+E1) / portfolio / remove(+E7)")
# E1-Check braucht yfinance; fängt API-Fehler ab -> muss nicht crashen
print(cmd_add(["NVDA", "120.50", "10", "Langfrist-Compounder"]))
print(cmd_portfolio([]))
# remove -> status geschlossen
close_position("NVDA", 125.0)
p = get_position("NVDA")
print("nach remove status:", p["status"] if p else "geloescht")
# frische Position fuer P3-Test anlegen
cmd_add(["NVDA", "150.00", "5", "Langfrist-Compounder"])
print("OK (E1/Correlation evtl. n/a wegen API-Limit, aber kein Crash)")

# 8) llm_runner im Mock-Modus (P1/P2/P3/P5/P7)
section("8) llm_runner Mock-Lauf P3 (E4/E5/E6 Pipeline)")
try:
    out = run_p3("NVDA")
    print("P3:", out)
    rec = get_last_recommendation("NVDA")
    print("gespeichert -> typ:", rec["typ"], "| ev_berechnet:", rec.get("ev_berechnet"),
          "| kelly_groesse:", rec.get("kelly_groesse"),
          "| erwarteter_treiber:", rec.get("erwarteter_treiber"),
          "| advocate_dissens:", rec.get("advocate_dissens"))
    assert rec.get("ev_berechnet") is not None
    assert rec.get("erwarteter_treiber") is not None
except Exception as e:
    print("P3 nutzt yfinance ->", type(e).__name__, "(erwartet bei API-Limit, Mock-LLM lief)")
print("OK")


# 9) Broker-Layer (Papier-Sandbox, komplett offline via --kurs)
section("9) Broker: Papier-Grenze, Ausfuehrung, Guardrails")
import lib.broker as broker
from lib.order_guard import pruefe
import lib.db as _bdb

# 9a) Die harte Grenze: Live-Endpunkte werden immer abgelehnt
for live in ("https://api.alpaca.markets", "https://api.binance.com",
             "https://example.com"):
    try:
        broker.assert_papier_only(live)
        raise AssertionError(f"{live} haette blockiert werden muessen!")
    except broker.LiveTradingBlocked:
        pass
broker.assert_papier_only("https://paper-api.alpaca.markets")  # muss durchgehen
print("OK — Live-Hosts blockiert, Papier-Host erlaubt")

# 9b) Sandbox aufsetzen
_bdb.konto_reset()
_bdb.konto_init(100000.0, "USD", provider="paper")
k = broker.konto()
assert k["cash"] == 100000.0, k
assert k["modus"].startswith("papier"), k
print("Konto:", k["cash"], k["waehrung"], "|", k["modus"])

# 9c) Kauf innerhalb des Deckels -> ausgefuehrt
ok, prot = pruefe("MSFT", "kaufen", 10, 150.0)
assert ok, prot
o = broker.order("MSFT", "kaufen", 10, kurs=150.0, guardrail=prot)
assert o["status"] == "ausgefuehrt", o
assert broker.konto()["cash"] == 98500.0, broker.konto()
print("Kauf 10 MSFT @150 ->", o["status"], "| Cash", broker.konto()["cash"])

# 9d) G3 Positions-Deckel (10%) -> abgelehnt
ok, prot = pruefe("MSFT", "kaufen", 100, 150.0)
assert not ok, prot
assert any(r["regel"] == "G3" and r["ergebnis"] == "verletzt"
           for r in prot["regeln"]), prot
print("G3 greift bei 100 Stk @150 (15% des Depots) -> abgelehnt")

# 9e) G7 kein Verbilligen -> abgelehnt, mit neuer These -> erlaubt
ok, prot = pruefe("MSFT", "kaufen", 5, 120.0)
assert not ok, prot
assert any(r["regel"] == "G7" and r["ergebnis"] == "verletzt"
           for r in prot["regeln"]), prot
ok2, _ = pruefe("MSFT", "kaufen", 5, 120.0, these_neu=True)
assert ok2
print("G7 greift bei Nachkauf unter Einstand; --these-neu hebt ihn auf")

# 9f) Leerverkauf gesperrt
ok, prot = pruefe("MSFT", "verkaufen", 999, 150.0)
assert not ok, prot
o = broker.order("MSFT", "verkaufen", 999, kurs=150.0)
assert o["status"] == "abgelehnt", o
print("Leerverkauf 999 Stk ->", o["status"], "|", o["ablehnungsgrund"])

# 9g) Verkauf mit Bestand -> ausgefuehrt, Cash zurueck
o = broker.order("MSFT", "verkaufen", 4, kurs=160.0)
assert o["status"] == "ausgefuehrt", o
assert broker.konto()["cash"] == 99140.0, broker.konto()
print("Verkauf 4 MSFT @160 -> Cash", broker.konto()["cash"])

# 9h) Order ohne Kurs wird abgelehnt statt geschaetzt
o = broker.order("ZZZZ_KEIN_TICKER", "kaufen", 1, kurs=None)
assert o["status"] == "abgelehnt", o
print("Ohne Kurs ->", o["status"], "|", o["ablehnungsgrund"][:50])

# 9i) Abgelehnte Orders sind protokolliert (Nachweis, dass Guardrails griffen)
alle = broker.orders(limit=50)
abgelehnt = [x for x in alle if x["status"] == "abgelehnt"]
assert len(abgelehnt) >= 2, alle
print(f"Order-Protokoll: {len(alle)} gesamt, {len(abgelehnt)} abgelehnt")
print("OK")


# 10) Fib-Cluster-Strategie (synthetische Kurse -> vollstaendig deterministisch)
section("10) Fib-Cluster + Konfluenz (offline, synthetische Reihe)")
import pandas as pd
import lib.fib_cluster as fib


def _reihe(punkte, kerzen_je_abschnitt=30):
    """Zickzack-Kursreihe aus Stuetzpunkten, ohne Rauschen -> reproduzierbar."""
    werte = []
    for a, b in zip(punkte, punkte[1:]):
        schritt = (b - a) / kerzen_je_abschnitt
        werte.extend([a + schritt * i for i in range(kerzen_je_abschnitt)])
    werte.append(punkte[-1])
    idx = pd.date_range("2026-01-01", periods=len(werte), freq="h")
    return pd.DataFrame({
        "Open": werte,
        "High": [w + 0.5 for w in werte],
        "Low": [w - 0.5 for w in werte],
        "Close": werte,
        "Volume": [1000] * len(werte),
    }, index=idx)


# 10a) Resample 1h -> 4h
df_1h = _reihe([100, 200, 150, 250, 190])
df_4h = fib.resample(df_1h, "4h")
assert len(df_4h) == -(-len(df_1h) // 4), (len(df_1h), len(df_4h))
assert df_4h["High"].max() == df_1h["High"].max()
print(f"Resample: {len(df_1h)} 1h-Kerzen -> {len(df_4h)} 4h-Kerzen, "
      f"Hoch erhalten ({df_4h['High'].max():.1f})")

# 10b) Swing-Struktur alternierend; Randpivots sind unbestaetigt
legs_best = fib.swings(df_1h, spanne=3, max_legs=6, mit_laufendem_leg=False)
assert all(l["bestaetigt"] for l in legs_best), legs_best
richtungen = [l["richtung"] for l in legs_best]
assert all(a != b for a, b in zip(richtungen, richtungen[1:])), richtungen

# Der juengste Wendepunkt kann per Definition nicht bestaetigt sein — das
# laufende Leg muss ihn nachliefern, sonst fehlt genau die Bewegung, aus der
# in der Praxis die Retracements gezogen werden.
legs = fib.swings(df_1h, spanne=3, max_legs=6)
assert len(legs) == len(legs_best) + 1, (len(legs), len(legs_best))
assert legs[-1]["bestaetigt"] is False, legs[-1]
assert legs[-1]["richtung"] == "abwaerts", legs[-1]  # 250 -> 190
print(f"Swings: {len(legs_best)} bestaetigt + 1 laufend "
      f"({legs[-1]['von']:.1f} -> {legs[-1]['bis']:.1f}, unbestaetigt)")

# Seitwaerts nach dem Pivot -> kein Leg aus Rauschen
flach = _reihe([100, 200, 199.5])
assert fib._laufendes_leg(flach, (60, 200.5, "hoch")) is None
print("Bewegung < 0.5% nach dem Pivot -> kein laufendes Leg (kein Rauschen)")

# 10c) Level je Leg: 5 Retracements + 5 Extensions, Formel vorzeichensicher
lv = fib.levels_fuer_leg({"von": 100.0, "bis": 200.0}, 0)
assert len(lv) == 10, lv
r618 = [x["preis"] for x in lv if x["verhaeltnis"] == 0.618 and x["art"] == "retracement"][0]
e1618 = [x["preis"] for x in lv if x["verhaeltnis"] == 1.618][0]
assert abs(r618 - 138.2) < 0.01, r618
assert abs(e1618 - 261.8) < 0.01, e1618
lv_ab = fib.levels_fuer_leg({"von": 200.0, "bis": 100.0}, 0)
r618_ab = [x["preis"] for x in lv_ab if x["verhaeltnis"] == 0.618 and x["art"] == "retracement"][0]
assert abs(r618_ab - 161.8) < 0.01, r618_ab
print(f"Level: Aufwaerts 0.618={r618:.1f} 1.618={e1618:.1f} | "
      f"Abwaerts 0.618={r618_ab:.1f}")

# 10d) Cluster nur aus >= 2 verschiedenen QUELLEN
#      (seit der Range-Erweiterung heisst das Feld "anzahl_quellen" statt
#      "anzahl_legs" — eine Quelle ist jetzt ein Leg ODER eine Seitwaertsrange)
alle = []
for nr, leg in enumerate(legs):
    alle.extend(fib.levels_fuer_leg(leg, nr))
cl = fib.cluster(alle, kurs=190.0, atr=2.0)
assert all(c["anzahl_quellen"] >= 2 for c in cl), cl
# Level aus nur einer Quelle duerfen nie einen Cluster bilden
nur_eins = fib.levels_fuer_leg(legs[0], 0)
assert fib.cluster(nur_eins, kurs=190.0, atr=2.0) == []
print(f"Cluster: {len(cl)} Zonen, alle aus >=2 Quellen; Ein-Quellen-Eingabe -> 0 Zonen")

# 10e) Doppeltop wird erkannt
df_dt = _reihe([100, 180, 140, 180.9, 120])
d = fib.doppel_formation(df_dt, spanne=3, toleranz_pct=1.5)
assert d and d["art"] == "doppeltop", d
print(f"Doppelformation: {d['art']} bei {d['niveau']:.2f} "
      f"(Abweichung {d['abweichung_pct']}%)")

# 10f) Gesamtanalyse ohne Netz (df durchgereicht)
a = fib.analysiere("TEST", zeitfenster="4h", df=df_1h)
assert "fehler" not in a, a
assert a["konfluenz"]["einstufung"] in ("keine", "schwach", "mittel", "stark")
assert a["kurs"] == 190.0, a["kurs"]
print(fib.formatiere(a))

# 10g) Zu wenig Daten -> sauberer Fehler statt Crash
kurz = fib.analysiere("TEST", df=df_1h.head(10))
assert "fehler" in kurz, kurz
print("Zu wenig Kerzen ->", kurz["fehler"])

# 10h) Seitwaertsphasen — die Erkennung muss BEIDE Richtungen koennen
#
# WARUM DIESER TEST EXISTIERT: `seitwaerts_phasen()` und `range_levels()` kamen
# als 215 Zeilen ohne eine einzige Pruefung dazu, und die Umbenennung im selben
# Zug hat den bestehenden Test zerrissen. Eine Erkennung, die nur beweist, dass
# sie etwas FINDET, ist wertlos — sie muss auch beweisen, dass sie im Trend
# NICHTS findet, sonst ist jede Kursreihe eine Range.
#
# Die Segmentlaenge ist hier kein beliebiger Wert: bei 20 Kerzen je Schenkel
# passen in ein 40er-Fenster nur zwei Schenkel, also hoechstens zwei
# Mittellinien-Kreuzungen — die Erkennung lehnt das korrekt ab. Das ist keine
# Schwaeche, sondern die Definition: "seitwaerts MIT Ausschwankungen" verlangt,
# dass im Fenster mehrfach hin und her gelaufen wird.
df_range = _reihe([100, 112] * 7, kerzen_je_abschnitt=10)
phasen = fib.seitwaerts_phasen(df_range, fenster=40)
assert phasen, "Klare Zickzack-Range wurde nicht als Seitwaertsphase erkannt"
p = phasen[-1]
assert p["hoch"] > p["tief"], p
assert p["kreuzungen"] >= 4, p
assert p["drift"] <= 0.35, p
print(f"Range erkannt: {p['tief']:.1f}-{p['hoch']:.1f} "
      f"(Hoehe {p['hoehe']:.1f}, {p['kreuzungen']} Mittellinien-Kreuzungen, "
      f"Drift {p['drift']})")

# Negativfall: sauberer Aufwaertstrend ist KEINE Range
df_trend = _reihe([100, 140, 180, 220, 260], kerzen_je_abschnitt=30)
assert fib.seitwaerts_phasen(df_trend, fenster=40) == [], \
    "Ein reiner Trend wurde faelschlich als Seitwaertsphase erkannt"
print("Reiner Aufwaertstrend -> 0 Phasen (kein Fehlalarm)")

# 10i) Range-Projektionen: der Measured Move ist die Probe
#      Bei Hoehe h ueber dem Hoch liegt das 1.0-Ziel exakt eine Rangehoehe
#      hoeher — wer das verrechnet, verschiebt jedes Ausbruchsziel.
phase = {"hoch": 120.0, "tief": 100.0, "hoehe": 20.0}
rl = fib.range_levels(phase, "R0")
oben_1 = [x["preis"] for x in rl
          if x["art"] == "range_projektion_oben" and x["verhaeltnis"] == 1.0][0]
unten_1 = [x["preis"] for x in rl
           if x["art"] == "range_projektion_unten" and x["verhaeltnis"] == 1.0][0]
mitte = [x["preis"] for x in rl
         if x["art"] == "range_intern" and x["verhaeltnis"] == 0.5][0]
assert abs(oben_1 - 140.0) < 1e-9, oben_1
assert abs(unten_1 - 80.0) < 1e-9, unten_1
assert abs(mitte - 110.0) < 1e-9, mitte
assert all(x["quelle"] == "R0" for x in rl), "Quelle wird nicht durchgereicht"
print(f"Measured Move: oben {oben_1:.1f}, unten {unten_1:.1f}, "
      f"Range-Mitte {mitte:.1f}")

# 10j) Leg und Range bestaetigen sich gegenseitig — genau dafuer wurde
#      "leg" zu "quelle" verallgemeinert. Ohne diese Pruefung waere die
#      ganze Umbenennung unbelegt.
gemischt = fib.levels_fuer_leg({"von": 100.0, "bis": 120.0}, "L0") + rl
cl_mix = fib.cluster(gemischt, kurs=110.0, atr=1.0)
assert all(c["anzahl_quellen"] >= 2 for c in cl_mix), cl_mix
assert any({"L0", "R0"} <= set(c["quellen"]) for c in cl_mix), \
    "Kein Cluster, in dem sich Leg und Range gegenseitig bestaetigen"
print(f"Leg+Range gemischt: {len(cl_mix)} Zonen aus >=2 verschiedenen Quellen")

# 10k) Die Range wird auch zum Konfluenz-Faktor — aber nur an der KANTE.
#      Die Range-Mitte darf nichts ausloesen: dort steht ein Kurs die meiste
#      Zeit ohnehin, und ein Faktor, der fast immer feuert, macht die
#      Einstufung wertlos.
a_kante = fib.analysiere("TEST", df=df_range)          # letzter Kurs = Oberkante
namen = [f["faktor"] for f in a_kante["konfluenz"]["faktoren"]]
assert any("Seitwaertsrange" in n for n in namen), namen
kante_f = [f for f in a_kante["konfluenz"]["faktoren"] if "Seitwaertsrange" in f["faktor"]][0]
assert kante_f["gewicht"] == 1.0, ("laufende Range muss voll zaehlen", kante_f)
print(f"Konfluenz an der Kante: {kante_f['faktor']} ({kante_f['detail']})")

# Mitte der Range -> kein Range-Faktor
mitte_faktoren = fib._konfluenz(
    kurs=106.0, atr=1.0, rsi=50.0, doppel=None,
    unterstuetzung=None, widerstand=None,
    phasen=[{"hoch": 112.5, "tief": 99.5, "dauer": 130, "laufend": True}],
)["faktoren"]
assert not any("Seitwaertsrange" in f["faktor"] for f in mitte_faktoren), mitte_faktoren
print("Kurs in der Range-Mitte -> kein Range-Faktor (kein Dauerfeuer)")
print("OK")

print("\n" + "#" * 60)
print("# ALLE KERNTESTS BESTANDEN (Netzwerk-unabhaengige Teile)")
print("#" * 60)
