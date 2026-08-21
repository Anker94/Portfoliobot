"""Guardrail-Pruefung vor jeder Order.

Bindeglied zwischen prompts/guardrails.txt (die Regeln, die das LLM binden)
und lib/broker.py (die Ausfuehrung). Ohne dieses Modul waeren die Guardrails
nur Prompt-Text — hier werden sie zu Code, den kein Modell wegargumentieren
kann.

Geprueft werden:
  G3  Positions-Deckel  — harte Ablehnung
  G7  Kein Verbilligen  — harte Ablehnung, ausser explizit neue These
  L   Leerverkauf       — harte Ablehnung (Sandbox kennt keine Shorts)
  E1  Klumpenrisiko     — nur Warnung (Korrelation braucht Netz, darf nicht
                          blockieren, wenn yfinance gerade 429 liefert)

Prinzip: eine verletzte Regel lehnt die Order AB, sie kappt sie nicht.
Automatisches Zurechtstutzen wuerde die Absicht des Nutzers still
veraendern — die Ablehnung mit Begruendung ist ehrlicher.
"""
import db
from ev_sizing import positions_deckel


def pruefe(ticker, seite, stueckzahl, kurs, depotwert=None, these_neu=False):
    """Liefert (ok, protokoll).

    protokoll = {"ok": bool, "regeln": [{regel, ergebnis, detail}, ...]}
    Das Protokoll wird mit der Order in `orders.guardrail_json` gespeichert —
    auch bei Ablehnung, damit spaeter nachvollziehbar ist, was gegriffen hat.
    """
    ticker = ticker.upper()
    seite = seite.lower()
    regeln = []

    def _add(regel, ok, detail):
        regeln.append({"regel": regel,
                       "ergebnis": "bestanden" if ok else "verletzt",
                       "detail": detail})
        return ok

    ok = True
    pos = db.bestand_get(ticker)
    bestand_stk = (pos or {}).get("stueckzahl") or 0.0
    einstand = (pos or {}).get("durchschnittskurs") or 0.0

    # --- Sanity ---
    if not kurs or kurs <= 0:
        ok &= _add("KURS", False, "kein gueltiger Kurs — Order nicht bewertbar")
        return False, {"ok": False, "regeln": regeln}
    if not stueckzahl or stueckzahl <= 0:
        ok &= _add("MENGE", False, "Stueckzahl muss > 0 sein")
        return False, {"ok": False, "regeln": regeln}

    volumen = kurs * stueckzahl

    if seite == "verkaufen":
        # --- L: kein Leerverkauf ---
        ok &= _add("L", stueckzahl <= bestand_stk,
                   f"Bestand {bestand_stk:g}, Verkauf {stueckzahl:g}")
        return bool(ok), {"ok": bool(ok), "regeln": regeln}

    # --- G3: Positions-Deckel ---
    deckel = positions_deckel()
    if depotwert is None:
        depotwert = _depotwert()
    if depotwert and depotwert > 0:
        anteil = (bestand_stk * kurs + volumen) / depotwert
        ok &= _add("G3", anteil <= deckel,
                   f"Position waere {anteil*100:.1f}% des Depots "
                   f"(Deckel {deckel*100:.0f}%)")
    else:
        _add("G3", True, "kein Depotwert ermittelbar — Deckel nicht pruefbar")

    # --- G7: kein Verbilligen ---
    if bestand_stk > 0 and einstand > 0 and kurs < einstand:
        ok &= _add("G7", bool(these_neu),
                   f"Nachkauf unter Einstand ({kurs:.2f} < {einstand:.2f}) — "
                   f"nur mit ausdruecklich neuer Faktenlage"
                   + ("; als neue These deklariert" if these_neu else ""))
    else:
        _add("G7", True, "kein Nachkauf unter Einstand")

    # --- E1: Klumpenrisiko (nur Warnung) ---
    andere = [b["ticker"] for b in db.bestand_all() if b["ticker"] != ticker]
    if andere:
        try:
            from correlation import add_correlation_check
            max_c, warn = add_correlation_check(ticker, andere)
            regeln.append({"regel": "E1", "ergebnis": "warnung" if warn else "bestanden",
                           "detail": warn or f"max. Korrelation {max_c}"})
        except Exception as e:
            regeln.append({"regel": "E1", "ergebnis": "nicht_pruefbar",
                           "detail": f"{type(e).__name__} (API-Ausfall)"})

    return bool(ok), {"ok": bool(ok), "regeln": regeln}


def _depotwert():
    """Depotwert der Sandbox (Cash + Bestand zu Einstandskursen als Untergrenze).
    Keine Netzabhaengigkeit: der Deckel muss auch bei API-Ausfall pruefbar sein."""
    k = db.konto_get()
    if not k:
        return None
    wert = sum((b["stueckzahl"] or 0) * (b["durchschnittskurs"] or 0)
               for b in db.bestand_all())
    return (k["cash"] or 0) + wert


def format_protokoll(protokoll):
    """Menschenlesbare Zusammenfassung fuer CLI/Telegram."""
    zeichen = {"bestanden": "✅", "verletzt": "⛔", "warnung": "⚠️",
               "nicht_pruefbar": "➖"}
    zeilen = [f"{zeichen.get(r['ergebnis'], '?')} {r['regel']}: {r['detail']}"
              for r in protokoll.get("regeln", [])]
    return "\n".join(zeilen)
