"""portfolio_data_refresh.py — aktualisiert aktuelle Kurse fuer aktive Positionen.

Nutzt ausschliesslich data_layer.get_performance(), schreibt in stock.db:
  positionen.aktueller_kurs
  positionen.kurs_stand

Read-Only-Quellen bleiben unberuehrt. Schreibzugriff nur auf berechnete Felder.
"""
import os
import sys
import json
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import db_path, load_config
from db import get_active_positions, update_position
from data_layer import get_performance


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> None:
    positions = get_active_positions()
    if not positions:
        print("Keine aktiven Positionen.")
        return

    results = []
    for p in positions:
        ticker = p["ticker"]
        try:
            kurs = get_performance(ticker)
        except Exception as e:
            kurs = None
            err = str(e)
        if kurs is None:
            results.append({"ticker": ticker, "status": "FAIL", "error": err if 'err' in locals() else "None"})
            continue
        try:
            update_position(ticker, aktueller_kurs=kurs, kurs_stand=_now_iso())
            results.append({"ticker": ticker, "status": "OK", "kurs": kurs})
        except Exception as e:
            results.append({"ticker": ticker, "status": "DB_FAIL", "error": str(e)})

    ok = sum(1 for r in results if r["status"] == "OK")
    fail = sum(1 for r in results if r["status"] != "OK")
    print(f"Refresh {_now_iso()}: {ok} OK, {fail} FAIL")

    log_path = os.path.join(_ROOT, "data", "markt_refresh.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    line = json.dumps({"zeit": _now_iso(), "results": results}, ensure_ascii=False)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    main()
