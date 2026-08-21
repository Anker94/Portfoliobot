"""options_flow.py — Options-Flow-Signal via yfinance (kostenlos).

Liest die aktuelle Options-Chain ueber yfinance und berechnet:
  - Call/Put Volume Ratio
  - Call/Put Open-Interest Ratio
  - Unusual Activity: einzelne Strikes mit hohem Volumen vs. OI

Signal-Logik (Nik 2026-08-16, kostenlos):
  - bullish wenn Call/Put Volume Ratio > 1.5 UND Put/Call OI Ratio > 1.0
  - bearish wenn Call/Put Volume Ratio < 0.7 UND Put/Call OI Ratio > 1.5

Limitation: yfinance gibt nur den AKTUELLEN Snapshot, keine historischen
Flow-Daten. Fuer echte "ungewoehnliche Aktivitaet" waere CBOE LiveVol
(15-min delayed) besser -- kommt in einer spaeteren Session.
"""
from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf


# Signal-Schwellen (Nik 2026-08-16)
BULLISH_VOLUME_RATIO = 1.5   # call/put volume > 1.5
BULLISH_OI_RATIO = 1.0        # call/put OI > 1.0
BEARISH_VOLUME_RATIO = 0.7    # call/put volume < 0.7
BEARISH_OI_RATIO = 1.5        # call/put OI > 1.5 (puts dominating OI)


def get_options_flow(ticker: str, max_exp_days: int = 60) -> dict:
    """Holt die Options-Chain und berechnet Call/Put Ratios.

    Nutzt die naechste verfuegbare Expiration innerhalb von `max_exp_days`
    (default 60 Tage = ca. 2 Monate). Liefert Signal + Details.
    """
    ticker = ticker.upper()
    try:
        yf_t = yf.Ticker(ticker)
        exps = yf_t.options
    except Exception as e:
        return {"signal": "n/a", "fehler": f"yfinance-Fehler: {e}",
                "call_volume": 0, "put_volume": 0,
                "call_oi": 0, "put_oi": 0,
                "details": "keine Options-Daten verfuegbar"}

    if not exps:
        return {"signal": "n/a", "fehler": "keine Options-Expiration verfuegbar",
                "call_volume": 0, "put_volume": 0,
                "call_oi": 0, "put_oi": 0,
                "details": "Ticker hat keine gelisteten Options"}

    # Waehle die naechste Expiration innerhalb max_exp_days
    today = datetime.now(timezone.utc).date()
    chosen_exp = None
    for exp_str in exps:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (exp_date - today).days <= max_exp_days:
            chosen_exp = exp_str
            break
    if not chosen_exp:
        chosen_exp = exps[0]  # Fallback: erste Expiration

    # Chain laden
    try:
        chain = yf_t.option_chain(chosen_exp)
        calls = chain.calls
        puts = chain.puts
    except Exception as e:
        return {"signal": "n/a", "fehler": f"Chain-Fetch-Fehler: {e}",
                "call_volume": 0, "put_volume": 0,
                "call_oi": 0, "put_oi": 0,
                "details": "Chain nicht abrufbar"}

    if calls is None or puts is None or calls.empty or puts.empty:
        return {"signal": "n/a", "fehler": "leere Chain",
                "call_volume": 0, "put_volume": 0,
                "call_oi": 0, "put_oi": 0,
                "details": "Chain leer"}

    # Aggregiere
    call_volume = int(calls["volume"].fillna(0).sum())
    put_volume = int(puts["volume"].fillna(0).sum())
    call_oi = int(calls["openInterest"].fillna(0).sum())
    put_oi = int(puts["openInterest"].fillna(0).sum())

    # Ratios
    vol_ratio = (call_volume / put_volume) if put_volume > 0 else float("inf")
    oi_ratio = (call_oi / put_oi) if put_oi > 0 else float("inf")

    # Unusual Activity: Strikes mit hohem Volumen relativ zu OI (>5x)
    unusual_calls = []
    for _, row in calls.iterrows():
        v = row.get("volume") or 0
        oi = row.get("openInterest") or 0
        if oi > 0 and v / oi > 5 and v > 50:
            unusual_calls.append({
                "strike": float(row["strike"]),
                "volume": int(v),
                "openInterest": int(oi),
                "ratio": round(v / oi, 1),
            })
    unusual_puts = []
    for _, row in puts.iterrows():
        v = row.get("volume") or 0
        oi = row.get("openInterest") or 0
        if oi > 0 and v / oi > 5 and v > 50:
            unusual_puts.append({
                "strike": float(row["strike"]),
                "volume": int(v),
                "openInterest": int(oi),
                "ratio": round(v / oi, 1),
            })

    # Signal-Logik
    signal = "neutral"
    details = []
    if vol_ratio >= BULLISH_VOLUME_RATIO and oi_ratio >= BULLISH_OI_RATIO:
        signal = "bullish"
        staerke = "stark" if (vol_ratio >= 2.5 and oi_ratio >= 2.0) else "mittel"
        details.append(f"Call/Put Volume Ratio {vol_ratio:.2f} (>= {BULLISH_VOLUME_RATIO}) bullish")
        details.append(f"Call/Put OI Ratio {oi_ratio:.2f} (>= {BULLISH_OI_RATIO}) bullish")
    elif vol_ratio <= BEARISH_VOLUME_RATIO and oi_ratio <= BEARISH_OI_RATIO:
        signal = "bearish"
        staerke = "stark" if (vol_ratio <= 0.5 and oi_ratio <= 1.0) else "mittel"
        details.append(f"Call/Put Volume Ratio {vol_ratio:.2f} (<= {BEARISH_VOLUME_RATIO}) bearish")
        details.append(f"Call/Put OI Ratio {oi_ratio:.2f} (<= {BEARISH_OI_RATIO}) bearish")
    else:
        staerke = "neutral"
        details.append(f"Call/Put Volume Ratio {vol_ratio:.2f}, OI Ratio {oi_ratio:.2f} -- gemischt")

    if unusual_calls:
        details.append(f"{len(unusual_calls)} ungewoehnliche Call-Strikes")
    if unusual_puts:
        details.append(f"{len(unusual_puts)} ungewoehnliche Put-Strikes")

    return {
        "signal": signal,
        "staerke": staerke,
        "expiration": chosen_exp,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_put_volume_ratio": round(vol_ratio, 2),
        "call_put_oi_ratio": round(oi_ratio, 2),
        "unusual_calls": unusual_calls[:5],  # top 5
        "unusual_puts": unusual_puts[:5],
        "details": "; ".join(details),
    }


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "PSIX"
    out = get_options_flow(t)
    print(json.dumps(out, ensure_ascii=False, indent=2))
