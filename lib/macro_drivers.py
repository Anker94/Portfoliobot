"""Macro-Treiber (W2): messbare Treiber je Position.

Das Mapping ist BEISPIELHAFT (SKILL.md: nicht Ticker kopieren, Prinzip anwenden).
fetch_all_drivers() laeuft vor P3/P5 und schreibt die Treiber-JSON je Position.
"""
from data_layer import get_performance, safe_history
import json


# Beispiel-Mapping (siehe SKILL.md Tabelle). Beim /add wird vom User das
# Mapping fuer die neue Position gesetzt; hier Default-Erkennung.
DEFAULT_DRIVER_MAP = {
    "UPST": {"^TNX": "10Y-Zins", "^IRX": "13W-Zins"},
    "WFRD": {"CL=F": "WTI-Oel", "BZ=F": "Brent-Oel"},
    "ABY.V": {"GC=F": "Gold", "HG=F": "Kupfer"},
    "MKA.V": {"REMX": "Seltene-Erden-ETF"},
    "MELI": {"BRL=X": "BRL-FX", "MXN=X": "MXN-FX"},
    "FN": {"SMH": "Halbleiter-ETF", "NVDA": "NVDA"},
    "PLTR": {"ITA": "Defense-ETF"},
}


def drivers_for_ticker(ticker):
    return DEFAULT_DRIVER_MAP.get(ticker.upper(), {})


def fetch_driver_values(ticker, driver_map=None):
    """Liefert {driver_ticker: {"perf": %, "spot": x}} fuer die Treiber."""
    dm = driver_map or drivers_for_ticker(ticker)
    result = {}
    for d, label in dm.items():
        perf = get_performance(d)
        result[d] = {
            "label": label,
            "perf_5d": round(perf, 2) if perf is not None else None,
        }
    return result


def build_driver_context(ticker):
    """Liefert lesbaren Kontext-String fuer Prompt-Injektion."""
    vals = fetch_driver_values(ticker)
    parts = []
    for d, v in vals.items():
        p = v.get("perf_5d")
        if p is not None:
            richtung = "steigend" if p >= 0 else "fallend"
            parts.append(f"{v['label']} ({d}): {richtung} {abs(p):.1f}% (5T)")
    return "; ".join(parts) if parts else "keine Treiberdaten verfuegbar"


if __name__ == "__main__":
    print(build_driver_context("UPST"))
