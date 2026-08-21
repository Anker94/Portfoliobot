"""trigger_engine.py — Trigger-Engine fuer das Smart-Money-Trigger-System.

Kombiniert die drei Signale (13F, Form 4, Options-Flow) mit dem
Support/Resistance-Cluster (aus fib_cluster.py) zu einer BUY/SELL-Trigger-
Entscheidung.

Logik (entschieden 2026-08-16, Nik):
  BUY-Trigger feuert wenn:
    (mind. 2 von 3 Smart-Money-Signalen alignen bullish)
    UND
    (Kurs innerhalb ±3% eines Support-Clusters)
  SELL-Trigger symmetrisch:
    (Insider-Cluster-Verkauf + Kurs an Resistance)

  Trigger-Budget: max 500 EUR pro Trigger.
  Manuelle Freigabe durch Nik (kein Standing-Listener).

Implementierungs-Status (2026-08-16):
  - Form 4 (Insider-Cluster): lib/sec_edgar.py → fertig
  - 13F (Institutionelle Holdings): queue, eigene Session
  - Options-Flow: queue (CBOE delayed / YF options)
  - Support/Resistance-Cluster: fib_cluster.analysiere() → fertig
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

# Defaults
SUPPORT_TOLERANZ_PCT = 3.0  # Kurs muss innerhalb ±3% eines Support-Clusters sein
RESISTANCE_TOLERANZ_PCT = 3.0  # symmetrisch
MIN_BULLISHE_SIGNALE = 2  # mind. 2 von 3 Smart-Money-Signalen bullish
MIN_BEARISHE_SIGNALE = 2  # symmetrisch fuer SELL
TRIGGER_BUDGET_MAX_EUR = 500.0  # entschieden 2026-08-16

# Pfad zum Trigger-Log
_TRIGGER_LOG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "trigger_log.json")
)


def trigger_check(ticker: str, days_back: int = 30,
                  user_agent: str = None) -> dict:
    """Pruefe, ob fuer `ticker` ein BUY- oder SELL-Trigger feuert.

    Liefert ein Dict mit allen Inputs und der Entscheidung. Nichts wird
    automatisch ausgefuehrt -- das ist Aufgabe von Nik nach manueller Pruefung.
    """
    ticker = ticker.upper()
    # 1) Smart-Money-Signale (Insider-Cluster + 13F-Institutionelle + Options-Flow)
    insider = _insider_cluster(ticker, days_back, user_agent)
    thirteen = _thirteen_f(ticker, days_back, user_agent)
    options = _options_flow(ticker)
    # 2) Support/Resistance-Cluster aus fib_cluster
    cluster = _support_resistance(ticker)
    # 3) Aktueller Kurs
    kurs = _spot(ticker)
    # 4) Bewertung: bullish vs. bearish
    bullish_signale = []
    bearish_signale = []
    if insider["signal"] == "bullish":
        bullish_signale.append({
            "quelle": "insider_cluster",
            "staerke": insider["staerke"],
            "details": insider["details"],
        })
    elif insider["signal"] == "bearish":
        bearish_signale.append({
            "quelle": "insider_cluster",
            "staerke": insider["staerke"],
            "details": insider["details"],
        })
    if thirteen["signal"] == "bullish":
        bullish_signale.append({
            "quelle": "13f_institutional",
            "staerke": thirteen["staerke"],
            "details": thirteen["details"],
        })
    elif thirteen["signal"] == "bearish":
        bearish_signale.append({
            "quelle": "13f_institutional",
            "staerke": thirteen["staerke"],
            "details": thirteen["details"],
        })
    if options["signal"] == "bullish":
        bullish_signale.append({
            "quelle": "options_flow",
            "staerke": options["staerke"],
            "details": options["details"],
        })
    elif options["signal"] == "bearish":
        bearish_signale.append({
            "quelle": "options_flow",
            "staerke": options["staerke"],
            "details": options["details"],
        })
    # 5) BUY-Trigger-Bedingung
    buy_fired = False
    buy_grund = None
    if (len(bullish_signale) >= MIN_BULLISHE_SIGNALE
            and cluster["naechste_unterstuetzung"] is not None
            and kurs is not None
            and _an_support_cluster(kurs, cluster["naechste_unterstuetzung"],
                                     SUPPORT_TOLERANZ_PCT)):
        naechste_u = cluster["naechste_unterstuetzung"]
        buy_fired = True
        buy_grund = (
            f"{len(bullish_signale)} bullishe Smart-Money-Signal(e) + "
            f"Kurs bei Unterstuetzung {naechste_u['zentrum']:.2f} "
            f"(Abstand {naechste_u['abstand_pct']:+.2f}%, "
            f"Staerke {naechste_u['staerke']} aus {naechste_u['anzahl_quellen']} Quellen)"
        )
    # 6) SELL-Trigger-Bedingung (symmetrisch)
    sell_fired = False
    sell_grund = None
    if (len(bearish_signale) >= MIN_BEARISHE_SIGNALE
            and cluster["naechster_widerstand"] is not None
            and kurs is not None
            and _an_resistance_cluster(kurs, cluster["naechster_widerstand"],
                                       RESISTANCE_TOLERANZ_PCT)):
        naechster_w = cluster["naechster_widerstand"]
        sell_fired = True
        sell_grund = (
            f"{len(bearish_signale)} bearishe Smart-Money-Signal(e) + "
            f"Kurs bei Widerstand {naechster_w['zentrum']:.2f} "
            f"(Abstand {naechster_w['abstand_pct']:+.2f}%, "
            f"Staerke {naechster_w['staerke']} aus {naechster_w['anzahl_quellen']} Quellen)"
        )
    return {
        "ticker": ticker,
        "stand": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kurs": kurs,
        "smart_money": {
            "insider_cluster": insider,
            "13f_institutional": thirteen,
            "options_flow": options,
            "bullish_signale": bullish_signale,
            "bearish_signale": bearish_signale,
            "anzahl_bullish": len(bullish_signale),
            "anzahl_bearish": len(bearish_signale),
        },
        "cluster": cluster,
        "entscheidung": {
            "buy_trigger_fired": buy_fired,
            "buy_trigger_grund": buy_grund,
            "sell_trigger_fired": sell_fired,
            "sell_trigger_grund": sell_grund,
            "trigger_budget_max_eur": TRIGGER_BUDGET_MAX_EUR,
        },
    }


# ----------------------------------------------------------------------
# Signal-Quellen
# ----------------------------------------------------------------------
def _insider_cluster(ticker, days_back, user_agent) -> dict:
    try:
        from sec_edgar import insider_cluster_signal
        return insider_cluster_signal(ticker, days_back=days_back,
                                       user_agent=user_agent)
    except Exception as e:
        return {"signal": "n/a", "fehler": str(e),
                "anzahl_buyer": 0, "anzahl_kauf_transaktionen": 0,
                "gesamtwert_usd": 0.0, "buyer": [],
                "periode_tage": days_back,
                "details": f"SEC-Client-Fehler: {e}"}


def _thirteen_f(ticker, days_back, user_agent) -> dict:
    """13F-Institutionelle-Holdings-Signal."""
    try:
        from thirteen_f import thirteen_f_signal, refresh_13f_snapshots
        # Erst alle Snapshots refreshen (nur einmal pro Aufruf sinnvoll, hier vereinfacht)
        # In der Praxis wuerde man refresh_13f_snapshots() manuell aufrufen
        return thirteen_f_signal(ticker, user_agent=user_agent)
    except Exception as e:
        return {"signal": "n/a", "fehler": str(e),
                "anzahl_holdings": 0, "anzahl_aenderungen": 0,
                "holdings": [], "changes": [],
                "details": f"13F-Client-Fehler: {e}"}


def _options_flow(ticker) -> dict:
    """Options-Flow-Signal ueber yfinance (Call/Put Volume + OI Ratios)."""
    try:
        from options_flow import get_options_flow
        return get_options_flow(ticker)
    except Exception as e:
        return {"signal": "n/a", "fehler": str(e),
                "call_put_volume_ratio": 0, "call_put_oi_ratio": 0,
                "call_volume": 0, "put_volume": 0,
                "details": f"Options-Client-Fehler: {e}"}


def _support_resistance(ticker) -> dict:
    """Hole Cluster-Analyse aus fib_cluster. Vereinfachte Form ohne Faktor-Drilldown."""
    try:
        import sys
        _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _ROOT not in sys.path:
            sys.path.insert(0, _ROOT)
        from fib_cluster import analysiere, formatiere
        a = analysiere(ticker, zeitfenster="1d", min_quellen=2)
        if a.get("fehler"):
            return {"fehler": a["fehler"], "naechste_unterstuetzung": None,
                    "naechster_widerstand": None}
        return {
            "kurs": a.get("kurs"),
            "rsi_14": a.get("rsi_14"),
            "atr": a.get("atr"),
            "naechste_unterstuetzung": a.get("naechste_unterstuetzung"),
            "naechster_widerstand": a.get("naechster_widerstand"),
            "doppel_formation": a.get("doppel_formation"),
            "konfluenz_score": a.get("konfluenz", {}).get("score"),
            "konfluenz_einstufung": a.get("konfluenz", {}).get("einstufung"),
            "volltext": formatiere(a),
        }
    except Exception as e:
        return {"fehler": str(e), "naechste_unterstuetzung": None,
                "naechster_widerstand": None}


def _spot(ticker) -> Optional[float]:
    try:
        from data_layer import get_performance
        return get_performance(ticker)
    except Exception:
        return None


def _an_support_cluster(kurs: float, cluster: dict, toleranz_pct: float) -> bool:
    if not cluster:
        return False
    zentrum = cluster.get("zentrum")
    if not zentrum:
        return False
    abstand_pct = abs(kurs / zentrum - 1) * 100
    return abstand_pct <= toleranz_pct


def _an_resistance_cluster(kurs: float, cluster: dict, toleranz_pct: float) -> bool:
    if not cluster:
        return False
    zentrum = cluster.get("zentrum")
    if not zentrum:
        return False
    abstand_pct = abs(kurs / zentrum - 1) * 100
    return abstand_pct <= toleranz_pct


# ----------------------------------------------------------------------
# Trigger-Log (Historientabelle)
# ----------------------------------------------------------------------
def log_trigger(ticker: str, ausloesung: str, details: dict) -> None:
    """Schreibe einen ausgeloesten Trigger in trigger_log.json.

    Wird manuell von Nik aufgerufen NACH Pruefung und Freigabe.
    """
    log = []
    if os.path.exists(_TRIGGER_LOG_PATH):
        try:
            with open(_TRIGGER_LOG_PATH, encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            log = []
    log.append({
        "ticker": ticker.upper(),
        "ausloesung": ausloesung,  # "buy" | "sell"
        "stand": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "details": details,
    })
    with open(_TRIGGER_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "PSIX"
    out = trigger_check(t)
    print(json.dumps(out, ensure_ascii=False, indent=2))
