"""E5 — EV-Sizing mit fraktionalem Kelly (Code + P3-Umbau).

P3 liefert Szenarien (Wahrscheinlichkeiten summieren auf 1.0, Bear Pflicht).
Dieser Code rechnet Erwartungswert + Positionsgroesse. LLM rechnet NICHT.

Kelly-Ableitung (korrekt, dimensionlos = Bruchteil des Portfolios):
  gewinn  = Szenarien mit kursziel > kurs   (bedingte Wahrsch. p)
  verlust = Szenarien mit kursziel <= kurs  (bedingte Wahrsch. q = 1-p)
  up      = bedingter Erwartungs-Return der Gewinn-Szenarien
  |down|  = bedingter Erwartungs-Verlustbetrag der Verlust-Szenarien
  f_full  = p - q * |down| / up             (Standard-Kelly, da f>0 <=> EV>0)
  f       = max(0, f_full)                   (kein negativer Einsatz)
  size    = min(0.25 * f, deckel)            (1/4-Kelly, gedeckelt)

Guardrail-Bindung (nicht verhandelbar):
- 1/4-Kelly, nie voll (LLM-Wahrscheinlichkeiten sind ueberkonfident).
- Deckel = Positions-Deckel aus guardrails.txt (Default 0.10). Kelly kann ihn
  nie ueberschreiten.
- Gilt nur fuer NEUE Positionen/Aufstockungen bei intakter These. Hebelt nie
  'kein Verbilligen' (Guardrail 7) aus.

Hinweis: E5 erst produktiv, wenn >=15 Empfehlungen mit Szenarien-Outcomes
vorliegen UND P7 die Kalibrierung geprueft hat. Vorher: Logging-Modus.
"""
import os
import re

_ROOT = os.path.dirname(os.path.abspath(__file__))
_GUARDRAILS = os.path.join(_ROOT, "..", "prompts", "guardrails.txt")


def _load_deckel():
    try:
        with open(_GUARDRAILS, encoding="utf-8") as f:
            for line in f:
                m = re.search(r"POSITIONS_DECKEL=(\d+\.\d+)", line)
                if m:
                    return float(m.group(1))
    except Exception:
        pass
    return 0.10


def positions_deckel():
    """Oeffentlicher Zugriff auf den Deckel aus guardrails.txt (Guardrail 3).
    Genutzt von lib/order_guard.py, damit die Ausfuehrung denselben Wert
    prueft, den das Sizing benutzt — eine Quelle, nicht zwei."""
    return _load_deckel()


def ev_and_kelly(szenarien, kurs, kelly_fraktion=0.25, deckel=None,
                 min_ziel=0.10, max_mult=4.0):
    """Liefert (ev_pct, kelly_groesse_pct).

    Kelly als Bruchteil des Portfolios. size = 0 bei nicht-positivem EV.

    Plausibilitaets-Guardrails (EV-Bug-Fix):
    - Kursziele, die weiter als [kurs*min_ziel, kurs*max_mult] entfernt sind
      (also <10% oder >400% des Spots), gelten als ungueltig und werden aus
      der EV-Berechnung ausgeklammert. Ohne plausible Szenarien -> EV 0.
    - Das verhindert absurde EVs (z.B. +1090% bei Pennystocks), wenn das LLM
      universelle/falsche Kursziele liefert.
    """
    if deckel is None:
        deckel = _load_deckel()
    if not szenarien or not kurs or kurs <= 0:
        return 0.0, 0.0

    lo, hi = kurs * min_ziel, kurs * max_mult  # plausibler Korridor

    def plausibel(s):
        z = s.get("kursziel_12m")
        return isinstance(z, (int, float)) and lo <= z <= hi

    valide = [s for s in szenarien if plausibel(s)]
    if not valide:
        return 0.0, 0.0

    # Wahrscheinlichkeiten der valide Szenarien auf 1 normieren
    psum = sum(s["wahrscheinlichkeit"] for s in valide) or 1.0

    # Erwartungswert (Wahrscheinlichkeit-gewichteter 12m-Return)
    ev = sum((s["wahrscheinlichkeit"] / psum) * (s["kursziel_12m"] / kurs - 1)
             for s in valide)

    gewinn = [s for s in valide if s["kursziel_12m"] > kurs]
    verlust = [s for s in valide if s["kursziel_12m"] <= kurs]
    p = sum(s["wahrscheinlichkeit"] for s in gewinn) / psum
    q = 1.0 - p

    if p <= 0 or q <= 0:
        # kein Gewinn-Szenario -> nicht kaufen (size 0); nur EV melden
        return round(ev * 100, 2), 0.0

    up = sum(s["wahrscheinlichkeit"] * (s["kursziel_12m"] / kurs - 1)
             for s in gewinn) / (p * psum)
    down_mag = (abs(sum(s["wahrscheinlichkeit"] * (s["kursziel_12m"] / kurs - 1)
                        for s in verlust)) / (q * psum))

    # Standard-Kelly (dimensionlos)
    if up > 0:
        f_full = p - q * down_mag / up
    else:
        f_full = 0.0
    f = max(0.0, f_full)
    size = min(f * kelly_fraktion, deckel)
    return round(ev * 100, 2), round(size * 100, 2)


def format_sizing(ev_pct, size_pct, fraktion=0.25, deckel=0.10):
    return (f"EV +{ev_pct:.1f}%/12m, Größe: {size_pct:.1f}% des Portfolios "
            f"({int(fraktion*100)}%-Kelly, Deckel {int(deckel*100)}%)")


if __name__ == "__main__":
    szen = [
        {"name": "Bull", "wahrscheinlichkeit": 0.30, "kursziel_12m": 210},
        {"name": "Base", "wahrscheinlichkeit": 0.50, "kursziel_12m": 165},
        {"name": "Bear", "wahrscheinlichkeit": 0.20, "kursziel_12m": 95},
    ]
    for spot in (100, 150, 190, 200):
        ev, size = ev_and_kelly(szen, kurs=spot)
        print(f"spot={spot}: {format_sizing(ev, size)}")
