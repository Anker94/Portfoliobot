"""sparplan_perf_refresh.py — aktualisiert Performance-Felder in sparplan-tracking*.json.

Schreibt nur berechnete Performance-Felder:
  positionen[].aktueller_kurs
  positionen[].performance_pct
  positionen[].eur_aktueller_wert
  gesamt_wert_eur
  gesamt_performance_pct
  stand

Read-Only-Quellen bleiben unberuehrt.
"""
import os
import sys
import json
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import load_config
from data_layer import get_performance

SPARPLAN_FILES = {
    "claude": "sparplan-tracking.json",
    "mavis": "sparplan-tracking-mavis.json",
    "banker": "sparplan-tracking-banker.json",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _refresh_file(name: str, filename: str) -> dict:
    path = os.path.join(_ROOT, "data", filename)
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return {"file": filename, "status": "MISSING"}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"file": filename, "status": "JSON_FAIL", "error": str(e)}

    positions = data.get("positionen", [])
    if not positions:
        return {"file": filename, "status": "NO_POSITIONS"}

    updated = 0
    failed = 0
    for p in positions:
        ticker = p.get("ticker")
        if not ticker:
            failed += 1
            continue
        try:
            kurs = get_performance(ticker)
        except Exception:
            kurs = None

        if kurs is None:
            failed += 1
            continue

        p["aktueller_kurs"] = kurs
        kauf = p.get("kauf_kurs")
        if kauf and kauf > 0:
            p["performance_pct"] = round((kurs - kauf) / kauf * 100, 2)
        else:
            p["performance_pct"] = 0.0

        eur_invested = p.get("eur_investiert")
        if eur_invested is not None and kauf and kauf > 0:
            # Approximation: Währungsverhältnis bleibt ~konstant über kurze Zeit
            p["eur_aktueller_wert"] = round(eur_invested * (1 + p["performance_pct"] / 100), 2)
        updated += 1

    if updated > 0:
        total_invested = sum(p.get("eur_investiert", 0) for p in positions)
        total_value = sum(p.get("eur_aktueller_wert", 0) for p in positions)
        data["gesamt_investiert_eur"] = round(total_invested, 2)
        data["gesamt_wert_eur"] = round(total_value, 2)
        if total_invested > 0:
            data["gesamt_performance_pct"] = round((total_value - total_invested) / total_invested * 100, 2)
        else:
            data["gesamt_performance_pct"] = 0.0
        data["stand"] = _now_iso()

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return {"file": filename, "status": "WRITE_FAIL", "error": str(e), "updated": updated, "failed": failed}

    return {"file": filename, "status": "OK", "updated": updated, "failed": failed}


def main() -> None:
    results = []
    for name, filename in SPARPLAN_FILES.items():
        results.append(_refresh_file(name, filename))

    ok = sum(1 for r in results if r.get("status") == "OK")
    fail = sum(1 for r in results if r.get("status") != "OK")
    print(f"Refresh {_now_iso()}: {ok} OK, {fail} FAIL")

    log_path = os.path.join(_ROOT, "data", "markt_refresh.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    line = json.dumps({"zeit": _now_iso(), "sparplan_results": results}, ensure_ascii=False)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    main()
