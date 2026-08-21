"""Fibonacci-Cluster mit Indikator-Konfluenz (Zeitfenster 4h und hoeher).

Idee: Ein einzelnes Fibonacci-Level ist beliebig — bei genug Swings liegt
irgendein Level ueberall. Aussagekraft entsteht erst, wenn Level aus
MEHREREN unabhaengigen Swings auf dasselbe Preisband fallen (Cluster) und
zusaetzlich ein davon unabhaengiger Indikator dieselbe Stelle markiert
(Konfluenz).

Berechnet werden je Swing-Leg a->b:
  Retracement r:  b - (b - a) * r     r in 0.236 0.382 0.5 0.618 0.786
  Extension   e:  a + (b - a) * e     e in 1.272 1.414 1.618 2.0 2.618
Beide Formeln sind vorzeichensicher, gelten also fuer Auf- und Abwaertslegs.
(0.5 ist keine Fibonacci-Zahl, sondern Konvention — hier bewusst mit
geringerem Gewicht enthalten.)

Cluster = Level aus >= 2 VERSCHIEDENEN Legs innerhalb eines Toleranzbandes
(max(0.5 * ATR, 0.75% des Kurses)). Level aus demselben Leg zaehlen nicht als
Bestaetigung — sie sind per Konstruktion korreliert.

Konfluenz-Faktoren (unabhaengig von Fibonacci):
  - RSI-14 im ueberkauften/ueberverkauften Bereich
  - Doppeltop / Doppelboden aus derselben Pivot-Struktur
  - Naehe des aktuellen Kurses zum Cluster

WISSENSCHAFTLICHE EINORDNUNG (nicht wegkuerzen)
-----------------------------------------------
Fuer Fibonacci-Retracements als eigenstaendigen Praediktor gibt es keine
belastbare empirische Evidenz; publizierte Tests finden ueberwiegend keinen
Effekt gegenueber zufaellig gezogenen Levels. Der Wert dieses Moduls liegt
deshalb NICHT in der Zahlenmystik, sondern darin, dass es eine vorab
festgelegte, deterministische Regel erzeugt, die man backtesten kann. Solange
der Backtest-Harness fehlt, sind die Ausgaben Hypothesen, keine Signale mit
bekannter Trefferquote.

Dieses Modul gibt AUSSCHLIESSLICH Analysen aus. Es ruft nie lib/broker.py.
Die Verbindung Signal -> Order trifft ein Mensch.
"""
import math

import pandas as pd

RETRACEMENTS = [0.236, 0.382, 0.5, 0.618, 0.786]
EXTENSIONS = [1.272, 1.414, 1.618, 2.0, 2.618]

# Projektionen einer Seitwaertsrange: gemessen von Hoch bzw. Tief in
# Vielfachen der Rangehoehe. 1.0 ist der klassische Measured Move.
RANGE_PROJEKTIONEN = [0.618, 1.0, 1.272, 1.618, 2.0, 2.618]
RANGE_INTERN = [0.236, 0.382, 0.5, 0.618, 0.786]

# Gewichte: die "starken" Verhaeltnisse (Goldener Schnitt und sein Quadrat)
# zaehlen mehr als die schwaecheren Konventionen. 1.0 steht fuer den
# Measured Move und ist die am besten begruendbare Projektion ueberhaupt.
GEWICHTE = {
    1.0: 1.0,
    0.618: 1.0, 1.618: 1.0,
    0.382: 0.7, 2.618: 0.7, 1.272: 0.6,
    0.5: 0.5, 0.786: 0.5, 1.414: 0.5,
    0.236: 0.4, 2.0: 0.4,
}

# yfinance kennt kein 4h-Intervall. Alles oberhalb 1h wird aus 1h-Kerzen
# resampled; darum die Zuordnung Zeitfenster -> (yf-Intervall, yf-Zeitraum,
# Resample-Regel). 1h ist bei yfinance auf 730 Tage Historie begrenzt.
ZEITFENSTER = {
    "1h":  ("1h", "730d", None),
    "4h":  ("1h", "730d", "4h"),
    "8h":  ("1h", "730d", "8h"),
    "1d":  ("1d", "5y", None),
    "1wk": ("1wk", "10y", None),
}


def lade_kerzen(ticker, zeitfenster="4h"):
    """OHLC-DataFrame im gewuenschten Zeitfenster. None bei API-Ausfall.

    Kein Kurs wird geschaetzt: liefert die API nichts, kommt None zurueck.
    """
    if zeitfenster not in ZEITFENSTER:
        raise ValueError(f"Zeitfenster {zeitfenster} unbekannt "
                         f"(erlaubt: {', '.join(ZEITFENSTER)})")
    intervall, zeitraum, regel = ZEITFENSTER[zeitfenster]
    try:
        from data_layer import safe_history
        df = safe_history(ticker, period=zeitraum, interval=intervall)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    if regel:
        df = resample(df, regel)
    return df


def resample(df, regel="4h"):
    """1h-Kerzen zu groesseren Kerzen zusammenfassen (OHLC-korrekt)."""
    out = df.resample(regel).agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
        "Volume": "sum",
    }).dropna(subset=["Open", "High", "Low", "Close"])
    return out


# --------------------------------------------------------------------------
# Swing-Struktur
# --------------------------------------------------------------------------
def pivots(df, spanne=3):
    """Pivot-Hochs/Tiefs: Extremwert im Fenster +/- `spanne` Kerzen.

    Liefert (hochs, tiefs) als Listen von (index, preis).
    """
    hochs, tiefs = [], []
    h = df["High"].values
    t = df["Low"].values
    n = len(df)
    for i in range(spanne, n - spanne):
        fenster_h = h[i - spanne:i + spanne + 1]
        if h[i] >= fenster_h.max():
            hochs.append((i, float(h[i])))
        fenster_t = t[i - spanne:i + spanne + 1]
        if t[i] <= fenster_t.min():
            tiefs.append((i, float(t[i])))
    return hochs, tiefs


def swings(df, spanne=3, max_legs=6, mit_laufendem_leg=True):
    """Alternierende Swing-Legs (a->b) aus der Pivot-Struktur.

    Ein Pivot laesst sich erst bestaetigen, wenn `spanne` Kerzen danach
    vorliegen — der juengste Wendepunkt fehlt in der bestaetigten Struktur
    also immer. Genau von dort aus zieht man in der Praxis aber die
    Retracements. `mit_laufendem_leg` haengt deshalb den laufenden,
    unbestaetigten Swing an, markiert mit bestaetigt=False.

    Liefert die juengsten `max_legs` Legs als Liste von dicts.
    """
    hochs, tiefs = pivots(df, spanne)
    punkte = [(i, p, "hoch") for i, p in hochs] + [(i, p, "tief") for i, p in tiefs]
    punkte.sort(key=lambda x: x[0])
    if not punkte:
        return []

    # Alternierung erzwingen: bei zwei gleichartigen Punkten in Folge den
    # extremeren behalten. Ohne das entstehen Pseudo-Legs innerhalb einer
    # Bewegung, die den Cluster kuenstlich aufblaehen wuerden.
    gefiltert = [punkte[0]]
    for p in punkte[1:]:
        letzter = gefiltert[-1]
        if p[2] == letzter[2]:
            besser = (p[1] > letzter[1]) if p[2] == "hoch" else (p[1] < letzter[1])
            if besser:
                gefiltert[-1] = p
        else:
            gefiltert.append(p)

    legs = []
    for a, b in zip(gefiltert, gefiltert[1:]):
        legs.append({
            "von_index": a[0], "bis_index": b[0],
            "von": a[1], "bis": b[1],
            "richtung": "aufwaerts" if b[1] > a[1] else "abwaerts",
            "spanne": abs(b[1] - a[1]),
            "bestaetigt": True,
        })

    if mit_laufendem_leg and gefiltert:
        laufend = _laufendes_leg(df, gefiltert[-1])
        if laufend:
            legs.append(laufend)

    return legs[-max_legs:]


def _laufendes_leg(df, letzter_pivot):
    """Leg vom juengsten bestaetigten Pivot bis zum aktuellen Extrem.

    Liefert None, wenn seit dem Pivot nichts Nennenswertes passiert ist
    (< 0.5% Bewegung) — sonst entstuenden Level aus Rauschen.
    """
    i, preis, art = letzter_pivot
    rest = df.iloc[i + 1:]
    if len(rest) < 2 or preis <= 0:
        return None
    if art == "tief":
        ziel = float(rest["High"].max())
        ziel_index = i + 1 + int(rest["High"].values.argmax())
    else:
        ziel = float(rest["Low"].min())
        ziel_index = i + 1 + int(rest["Low"].values.argmin())
    if abs(ziel - preis) / preis < 0.005:
        return None
    return {
        "von_index": i, "bis_index": ziel_index,
        "von": preis, "bis": ziel,
        "richtung": "aufwaerts" if ziel > preis else "abwaerts",
        "spanne": abs(ziel - preis),
        "bestaetigt": False,
    }


def levels_fuer_leg(leg, quelle):
    """Alle Fib-Level eines Legs als Liste von dicts.

    `quelle` identifiziert den Ursprung (Leg-Nummer oder Range-Kennung) und
    entscheidet spaeter, ob zwei Level sich gegenseitig bestaetigen duerfen.
    """
    a, b = leg["von"], leg["bis"]
    out = []
    for r in RETRACEMENTS:
        out.append({"preis": b - (b - a) * r, "verhaeltnis": r,
                    "art": "retracement", "quelle": quelle,
                    "gewicht": GEWICHTE.get(r, 0.4)})
    for e in EXTENSIONS:
        out.append({"preis": a + (b - a) * e, "verhaeltnis": e,
                    "art": "extension", "quelle": quelle,
                    "gewicht": GEWICHTE.get(e, 0.4)})
    return out


# --------------------------------------------------------------------------
# Seitwaertsphasen (Range) und ihre Projektionen
# --------------------------------------------------------------------------
def seitwaerts_phasen(df, fenster=40, max_drift=0.35, min_kreuzungen=4,
                      min_hoehe_atr=2.0, max_phasen=3):
    """Findet Seitwaertsphasen: wenig Netto-Bewegung bei vielen Ausschwankungen.

    Kriterien (alle skalenfrei, damit sie fuer 20-EUR- wie 800-EUR-Titel
    gelten):
      drift       = |Close_ende - Close_start| / Rangehoehe  <= max_drift
                    In einer Range ist die Netto-Bewegung klein gegen die
                    Spanne, in einem Trend nicht.
      kreuzungen  = wie oft der Kurs die Mittellinie durchschlaegt
                    >= min_kreuzungen. Das ist die eigentliche Definition
                    von "seitwaerts mit Ausschwankungen" — ohne dieses
                    Kriterium wuerde auch eine ruhige Flaute als Range gelten.
      hoehe/ATR   >= min_hoehe_atr, sonst ist die "Range" nur Rauschen.

    Ueberlappende Fenster werden zu einer Phase verschmolzen.
    Liefert die juengsten `max_phasen` Phasen.
    """
    if len(df) < fenster + 5:
        return []
    atr = atr_wert(df) or 0.0
    schritt = max(fenster // 4, 1)
    treffer = []

    for start in range(0, len(df) - fenster + 1, schritt):
        w = df.iloc[start:start + fenster]
        hoch = float(w["High"].max())
        tief = float(w["Low"].min())
        hoehe = hoch - tief
        if hoehe <= 0:
            continue
        if atr and hoehe / atr < min_hoehe_atr:
            continue
        drift = abs(float(w["Close"].iloc[-1]) - float(w["Close"].iloc[0])) / hoehe
        if drift > max_drift:
            continue
        mitte = (hoch + tief) / 2.0
        ueber = (w["Close"] > mitte).astype(int)
        kreuzungen = int((ueber.diff().abs() == 1).sum())
        if kreuzungen < min_kreuzungen:
            continue
        treffer.append({"von_index": start, "bis_index": start + fenster - 1,
                        "drift": drift, "kreuzungen": kreuzungen})

    if not treffer:
        return []

    # Ueberlappende Fenster zu Phasen verschmelzen
    phasen = []
    aktuell = dict(treffer[0])
    for t in treffer[1:]:
        if t["von_index"] <= aktuell["bis_index"]:
            aktuell["bis_index"] = max(aktuell["bis_index"], t["bis_index"])
            aktuell["kreuzungen"] = max(aktuell["kreuzungen"], t["kreuzungen"])
            aktuell["drift"] = min(aktuell["drift"], t["drift"])
        else:
            phasen.append(aktuell)
            aktuell = dict(t)
    phasen.append(aktuell)

    out = []
    for p in phasen[-max_phasen:]:
        w = df.iloc[p["von_index"]:p["bis_index"] + 1]
        hoch = float(w["High"].max())
        tief = float(w["Low"].min())
        out.append({
            "von_index": p["von_index"], "bis_index": p["bis_index"],
            "dauer": p["bis_index"] - p["von_index"] + 1,
            "hoch": round(hoch, 4), "tief": round(tief, 4),
            "hoehe": round(hoch - tief, 4),
            "mitte": round((hoch + tief) / 2.0, 4),
            "kreuzungen": p["kreuzungen"],
            "drift": round(p["drift"], 3),
            "laufend": p["bis_index"] >= len(df) - 3,
        })
    return out


def range_levels(phase, quelle):
    """Projektionen einer Seitwaertsrange.

    Die Rangehoehe ist die Messgroesse — anders als bei einem frei gewaehlten
    Swing-Leg ist sie objektiv bestimmt (Hoch und Tief der Phase). Deshalb
    sind diese Projektionen die am besten begruendbaren Fib-Level ueberhaupt.

    Nach oben:  hoch + hoehe * p     (Ausbruchsziele)
    Nach unten: tief - hoehe * p     (Zusammenbruchsziele)
    Intern:     tief + hoehe * r     (Unterstuetzung/Widerstand innerhalb)
    """
    hoch, tief, hoehe = phase["hoch"], phase["tief"], phase["hoehe"]
    out = []
    for p in RANGE_PROJEKTIONEN:
        out.append({"preis": hoch + hoehe * p, "verhaeltnis": p,
                    "art": "range_projektion_oben", "quelle": quelle,
                    "gewicht": GEWICHTE.get(p, 0.4)})
        out.append({"preis": tief - hoehe * p, "verhaeltnis": p,
                    "art": "range_projektion_unten", "quelle": quelle,
                    "gewicht": GEWICHTE.get(p, 0.4)})
    for r in RANGE_INTERN:
        out.append({"preis": tief + hoehe * r, "verhaeltnis": r,
                    "art": "range_intern", "quelle": quelle,
                    "gewicht": GEWICHTE.get(r, 0.4) * 0.7})
    return out


# --------------------------------------------------------------------------
# Cluster
# --------------------------------------------------------------------------
def toleranzband(kurs, atr=None):
    """Breite des Bandes, in dem Level als 'dasselbe Niveau' gelten."""
    kandidaten = [kurs * 0.0075]
    if atr:
        kandidaten.append(atr * 0.5)
    return max(kandidaten)


def cluster(levels, kurs, atr=None, min_quellen=2, min_staerke=0.0,
            verschmelzen=True):
    """Gruppiert Level zu Clustern.

    Nur Cluster aus >= min_quellen VERSCHIEDENEN Quellen (Legs oder Ranges)
    zaehlen — Level derselben Quelle sind per Konstruktion korreliert.

    `verschmelzen`: zweite Stufe, die benachbarte Cluster zusammenfasst.
    Ohne sie zerfaellt eine breite Zone in scheinbar praezise Einzelniveaus,
    die dichter liegen als der Titel an einem Tag schwankt — das taeuscht
    eine Genauigkeit vor, die die Daten nicht hergeben.
    """
    if not levels:
        return []
    band = toleranzband(kurs, atr)
    sortiert = sorted(levels, key=lambda x: x["preis"])

    gruppen, aktuell = [], [sortiert[0]]
    for lv in sortiert[1:]:
        if lv["preis"] - aktuell[0]["preis"] <= band:
            aktuell.append(lv)
        else:
            gruppen.append(aktuell)
            aktuell = [lv]
    gruppen.append(aktuell)

    out = []
    for g in gruppen:
        quellen = {lv["quelle"] for lv in g}
        if len(quellen) < min_quellen:
            continue
        preise = [lv["preis"] for lv in g]
        # Gewichteter Mittelpunkt: starke Verhaeltnisse ziehen das Zentrum
        gew_summe = sum(lv["gewicht"] for lv in g)
        if gew_summe < min_staerke:
            continue
        zentrum = sum(lv["preis"] * lv["gewicht"] for lv in g) / gew_summe
        out.append({
            "zentrum": round(zentrum, 4),
            "von": round(min(preise), 4),
            "bis": round(max(preise), 4),
            "staerke": round(gew_summe, 2),
            "anzahl_level": len(g),
            "anzahl_quellen": len(quellen),
            "quellen": sorted(str(q) for q in quellen),
            "lage": "unterstuetzung" if zentrum < kurs else "widerstand",
            "abstand_pct": round((zentrum / kurs - 1) * 100, 2),
            "verhaeltnisse": sorted({lv["verhaeltnis"] for lv in g}),
        })

    if verschmelzen:
        out = verschmelze(out, kurs, atr)
    return sorted(out, key=lambda c: c["staerke"], reverse=True)


def verschmelze(cluster_liste, kurs, atr=None):
    """Fasst Cluster zusammen, deren Baender sich beruehren oder naeher als
    eine Bandbreite liegen.

    Begruendung: zwei Zonen, die enger beieinander liegen als die
    Tagesschwankung, sind handelspraktisch dieselbe Zone. Sie getrennt zu
    zaehlen macht die Cluster-Zahl beeindruckend und die Aussage schlechter.
    """
    if not cluster_liste:
        return []
    band = toleranzband(kurs, atr)
    nach_preis = sorted(cluster_liste, key=lambda c: c["von"])

    zonen = [dict(nach_preis[0])]
    for c in nach_preis[1:]:
        letzte = zonen[-1]
        if c["von"] <= letzte["bis"] + band:
            gesamt = letzte["staerke"] + c["staerke"]
            letzte["zentrum"] = round(
                (letzte["zentrum"] * letzte["staerke"]
                 + c["zentrum"] * c["staerke"]) / gesamt, 4) if gesamt else letzte["zentrum"]
            letzte["von"] = min(letzte["von"], c["von"])
            letzte["bis"] = max(letzte["bis"], c["bis"])
            letzte["staerke"] = round(gesamt, 2)
            letzte["anzahl_level"] += c["anzahl_level"]
            letzte["quellen"] = sorted(set(letzte["quellen"]) | set(c["quellen"]))
            letzte["anzahl_quellen"] = len(letzte["quellen"])
            letzte["verhaeltnisse"] = sorted(set(letzte["verhaeltnisse"])
                                             | set(c["verhaeltnisse"]))
            letzte["lage"] = ("unterstuetzung" if letzte["zentrum"] < kurs
                              else "widerstand")
            letzte["abstand_pct"] = round((letzte["zentrum"] / kurs - 1) * 100, 2)
        else:
            zonen.append(dict(c))
    return zonen


# --------------------------------------------------------------------------
# Konfluenz: Doppeltop / Doppelboden
# --------------------------------------------------------------------------
def doppel_formation(df, spanne=3, toleranz_pct=1.5, min_abstand=5):
    """Erkennt Doppeltop / Doppelboden in der juengeren Pivot-Struktur.

    Zwei gleichartige Extrema innerhalb `toleranz_pct` und mindestens
    `min_abstand` Kerzen auseinander. Liefert dict oder None.
    """
    hochs, tiefs = pivots(df, spanne)

    def _suche(punkte, art):
        letzte = punkte[-4:]
        for i in range(len(letzte)):
            for j in range(i + 1, len(letzte)):
                (i1, p1), (i2, p2) = letzte[i], letzte[j]
                if i2 - i1 < min_abstand:
                    continue
                if p1 <= 0:
                    continue
                abweichung = abs(p2 - p1) / p1 * 100
                if abweichung <= toleranz_pct:
                    return {"art": art, "niveau": round((p1 + p2) / 2, 4),
                            "abweichung_pct": round(abweichung, 2),
                            "kerzen_auseinander": i2 - i1}
        return None

    return _suche(hochs, "doppeltop") or _suche(tiefs, "doppelboden")


def rsi_wert(df, fenster=14):
    try:
        from ta.momentum import RSIIndicator
        return float(RSIIndicator(df["Close"], window=fenster).rsi().iloc[-1])
    except Exception:
        return None


def atr_wert(df, fenster=14):
    try:
        from ta.volatility import AverageTrueRange
        return float(AverageTrueRange(df["High"], df["Low"], df["Close"],
                                      window=fenster).average_true_range().iloc[-1])
    except Exception:
        return None


# --------------------------------------------------------------------------
# Gesamtanalyse
# --------------------------------------------------------------------------
def analysiere(ticker, zeitfenster="4h", df=None, spanne=3, max_legs=6,
               min_quellen=2, min_staerke=0.0, verschmelzen=True,
               mit_ranges=True):
    """Vollstaendige Cluster-/Konfluenz-Analyse.

    `df` kann uebergeben werden (Tests, Backtest) — dann kein Netzaufruf.
    Liefert dict; bei fehlenden Daten {'fehler': ...}.

    `mit_ranges`: Seitwaertsphasen werden erkannt und ihre Projektionen als
    eigene Quellen eingespeist. Ein Swing-Extension, das auf eine
    Range-Projektion faellt, ist echte Konfluenz aus zwei unabhaengigen
    Konstruktionen — nicht dasselbe Level zweimal gezaehlt.
    """
    if df is None:
        df = lade_kerzen(ticker, zeitfenster)
    if df is None or len(df) < 50:
        return {"ticker": ticker, "zeitfenster": zeitfenster,
                "fehler": "zu wenig Kursdaten (API-Ausfall oder junger Titel)"}

    kurs = float(df["Close"].iloc[-1])
    atr = atr_wert(df)
    rsi = rsi_wert(df)
    legs = swings(df, spanne=spanne, max_legs=max_legs)
    if len(legs) < 2:
        return {"ticker": ticker, "zeitfenster": zeitfenster, "kurs": kurs,
                "fehler": "keine ausreichende Swing-Struktur"}

    levels = []
    for nr, leg in enumerate(legs):
        levels.extend(levels_fuer_leg(leg, f"leg{nr}"))

    phasen = seitwaerts_phasen(df) if mit_ranges else []
    for nr, ph in enumerate(phasen):
        levels.extend(range_levels(ph, f"range{nr}"))

    cl = cluster(levels, kurs, atr, min_quellen=min_quellen,
                 min_staerke=min_staerke, verschmelzen=verschmelzen)
    doppel = doppel_formation(df, spanne=spanne)

    unterstuetzung = [c for c in cl if c["lage"] == "unterstuetzung"]
    widerstand = [c for c in cl if c["lage"] == "widerstand"]
    naechste_u = max(unterstuetzung, key=lambda c: c["zentrum"]) if unterstuetzung else None
    naechster_w = min(widerstand, key=lambda c: c["zentrum"]) if widerstand else None

    konfluenz = _konfluenz(kurs, atr, rsi, doppel, naechste_u, naechster_w,
                           phasen)

    return {
        "ticker": ticker.upper(),
        "zeitfenster": zeitfenster,
        "kerzen": len(df),
        "stand": str(df.index[-1]),
        "kurs": round(kurs, 4),
        "atr": round(atr, 4) if atr else None,
        "rsi_14": round(rsi, 1) if rsi else None,
        "legs": legs,
        "seitwaerts_phasen": phasen,
        "cluster": cl,
        "naechste_unterstuetzung": naechste_u,
        "naechster_widerstand": naechster_w,
        "doppel_formation": doppel,
        "konfluenz": konfluenz,
    }


def _konfluenz(kurs, atr, rsi, doppel, unterstuetzung, widerstand, phasen=None):
    """Sammelt unabhaengige Faktoren, die auf dieselbe Stelle zeigen.

    `phasen` sind die erkannten Seitwaertsranges. Sie zaehlen bewusst nur, wenn
    der Kurs an einer KANTE steht — die Range-Mitte ist keine Information, dort
    steht ein Kurs die meiste Zeit ohnehin. Ein Faktor, der fast immer feuert,
    verwaessert den Score und macht die Einstufung unbrauchbar.
    """
    faktoren = []
    band = toleranzband(kurs, atr)

    for zone, richtung in ((unterstuetzung, "Unterstuetzung"),
                           (widerstand, "Widerstand")):
        if not zone:
            continue
        nah = abs(kurs - zone["zentrum"]) <= band * 2
        if nah:
            faktoren.append({
                "faktor": f"Kurs an Fib-Cluster ({richtung})",
                "detail": f"{zone['zentrum']:.2f} aus {zone['anzahl_quellen']} Quellen, "
                          f"Staerke {zone['staerke']}",
                "gewicht": min(zone["staerke"] / 3.0, 1.5),
            })

    # Kurs an der Kante einer Seitwaertsrange. Nur die juengste Phase zaehlt:
    # eine Range von vor 200 Kerzen sagt ueber das Jetzt nichts mehr, und
    # mehrere Phasen einzeln zu zaehlen wuerde denselben Umstand mehrfach
    # gewichten.
    for ph in (phasen or [])[-1:]:
        for kante, richtung in ((ph["hoch"], "Oberkante"), (ph["tief"], "Unterkante")):
            if abs(kurs - kante) <= band * 2:
                faktoren.append({
                    "faktor": f"Kurs an {richtung} einer Seitwaertsrange",
                    "detail": f"{ph['tief']:.2f}-{ph['hoch']:.2f} ueber {ph['dauer']} Kerzen"
                              + (", laufend" if ph.get("laufend") else ""),
                    # Eine laufende Range wiegt schwerer als eine abgeschlossene:
                    # bei ihr ist die Kante gerade jetzt eine Entscheidungsstelle.
                    "gewicht": 1.0 if ph.get("laufend") else 0.6,
                })
                break

    if rsi is not None:
        if rsi <= 30 and unterstuetzung:
            faktoren.append({"faktor": "RSI ueberverkauft", "detail": f"RSI {rsi:.1f}",
                             "gewicht": 1.0})
        elif rsi >= 70 and widerstand:
            faktoren.append({"faktor": "RSI ueberkauft", "detail": f"RSI {rsi:.1f}",
                             "gewicht": 1.0})

    if doppel:
        zone = unterstuetzung if doppel["art"] == "doppelboden" else widerstand
        if zone and abs(doppel["niveau"] - zone["zentrum"]) <= band * 2:
            faktoren.append({
                "faktor": f"{doppel['art']} am selben Niveau",
                "detail": f"{doppel['niveau']:.2f}, Abweichung "
                          f"{doppel['abweichung_pct']}%",
                "gewicht": 1.2,
            })
        elif doppel:
            faktoren.append({
                "faktor": f"{doppel['art']} (abseits des Clusters)",
                "detail": f"{doppel['niveau']:.2f}",
                "gewicht": 0.3,
            })

    score = round(sum(f["gewicht"] for f in faktoren), 2)
    if score >= 2.5:
        einstufung = "stark"
    elif score >= 1.5:
        einstufung = "mittel"
    elif score > 0:
        einstufung = "schwach"
    else:
        einstufung = "keine"
    return {"faktoren": faktoren, "score": score, "einstufung": einstufung}


def formatiere(a):
    """Menschenlesbare Ausgabe fuer CLI/Telegram."""
    if a.get("fehler"):
        return f"❌ {a['ticker']} ({a.get('zeitfenster')}): {a['fehler']}"
    z = [f"📐 {a['ticker']} · {a['zeitfenster']} · {a['kerzen']} Kerzen · "
         f"Stand {a['stand'][:16]}",
         f"   Kurs {a['kurs']:.2f} · RSI-14 {a['rsi_14']} · ATR {a['atr']}"]
    offen = [l for l in a.get("legs", []) if not l.get("bestaetigt", True)]
    z.append(f"   Legs: {len(a.get('legs', []))}"
             + (f" (davon {len(offen)} laufend/unbestaetigt)" if offen else ""))

    for zone, pfeil in ((a.get("naechster_widerstand"), "▲ Widerstand"),
                        (a.get("naechste_unterstuetzung"), "▼ Unterstuetzung")):
        if zone:
            z.append(f"   {pfeil}: {zone['zentrum']:.2f} "
                     f"({zone['abstand_pct']:+.2f}%) · Staerke {zone['staerke']} "
                     f"· {zone['anzahl_quellen']} Quellen · "
                     f"Verhaeltnisse {zone['verhaeltnisse']}")

    if a.get("doppel_formation"):
        d = a["doppel_formation"]
        z.append(f"   ⧉ {d['art']} bei {d['niveau']:.2f} "
                 f"(Abweichung {d['abweichung_pct']}%)")

    k = a["konfluenz"]
    z.append(f"   Konfluenz: {k['einstufung'].upper()} (Score {k['score']})")
    for f in k["faktoren"]:
        z.append(f"     · {f['faktor']} — {f['detail']}")
    if not k["faktoren"]:
        z.append("     · keine — Kurs steht in keiner markierten Zone")

    z.append("   ⓘ Hypothese, keine validierte Trefferquote (Backtest fehlt).")
    return "\n".join(z)


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    zf = sys.argv[2] if len(sys.argv) > 2 else "4h"
    print(formatiere(analysiere(t, zf)))
