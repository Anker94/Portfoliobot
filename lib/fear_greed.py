"""Fear & Greed Proxy (A4): VIX-Percentile + SPY-SMA125-Abstand.

Kein LLM, reine Code-Berechnung aus yfinance. Wird in P2 und P5 als
Kontraindikator injiziert.
"""
from data_layer import safe_history, get_index_data
import numpy as np


def vix_percentile(lookback_days=252):
    """VIX aktuell vs seine eigenen letzten `lookback_days` Werte als Percentile."""
    df = safe_history("^VIX", period=f"{lookback_days+5}d")
    if df is None or len(df) < 20:
        return None
    ser = df["Close"].dropna()
    cur = ser.iloc[-1]
    pct = float((ser < cur).mean() * 100)
    return {"vix": float(cur), "percentile": round(pct, 1)}


def spy_distance_sma125():
    """SPY-Kurs relativ zu SMA125 (Abstand in %). Negativ = unter SMA = Angst."""
    df = safe_history("SPY", period="1y")
    if df is None or len(df) < 125:
        return None
    close = df["Close"]
    sma = close.rolling(125).mean().iloc[-1]
    dist = float((close.iloc[-1] / sma - 1) * 100)
    return {"spy": float(close.iloc[-1]), "sma125": float(sma),
            "distance_pct": round(dist, 2)}


def fear_greed_index():
    """Kombiniert beide Signale zu einem 0-100 'Fear & Greed'-Proxy.
    Tief = Fear, Hoch = Greed."""
    vp = vix_percentile()
    sd = spy_distance_sma125()
    if vp is None or sd is None:
        return None
    # VIX-Percentile: hoeher = mehr Angst -> invertiert
    vix_score = 100 - vp["percentile"]
    # SPY ueber SMA125 -> Greed (clamp -10..+10 % -> 50..100)
    sma_score = 50 + max(-50, min(50, sd["distance_pct"] * 5))
    idx = round(0.5 * vix_score + 0.5 * sma_score, 1)
    label = ("Extreme Fear" if idx < 20 else "Fear" if idx < 40 else
             "Neutral" if idx < 60 else "Greed" if idx < 80 else "Extreme Greed")
    return {"index": idx, "label": label, "vix_pct": vp["percentile"],
            "spy_vs_sma": sd["distance_pct"]}


if __name__ == "__main__":
    print(fear_greed_index())
