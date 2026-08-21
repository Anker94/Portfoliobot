"""Regime-Modul (P7, woechentlich, Code-only): 6 Signale aus yfinance.

Liefert einen Regime-Vektor, der in jede P3-Analyse injiziert wird.
Wechsel-Alerts erst nach 2-Wochen-Bestaetigung (kein Whipsaw) -> hier nur
Berechnung; Persistenz/Bestaetigung in regime_history (migrate_v3).
"""
from data_layer import safe_history, get_index_data


def _sma_ratio(ticker, win):
    df = safe_history(ticker, period="1y")
    if df is None or len(df) < win:
        return None
    c = df["Close"]
    return float(c.iloc[-1] / c.rolling(win).mean().iloc[-1])


def _perf(ticker, days):
    df = safe_history(ticker, period=f"{days+5}d")
    if df is None or len(df) < days:
        return None
    c = df["Close"]
    return float((c.iloc[-1] / c.iloc[-days] - 1) * 100)


def regime_vector():
    """Berechnet 6 Signale. Jedes: name -> {"wert": x, "signal": +/-1}."""
    out = {}

    # 1) Risk-on/off
    spy = _sma_ratio("SPY", 200)
    vix = safe_history("^VIX", period="1mo")
    vix_now = float(vix["Close"].iloc[-1]) if vix is not None and not vix.empty else None
    out["risk_on_off"] = {
        "spy_vs_sma200": round(spy, 3) if spy else None,
        "vix": vix_now,
        "signal": 1 if (spy and spy > 1) else (-1 if spy else 0),
    }

    # 2) Growth vs Value
    vug = _perf("VUG", 140)
    vtv = _perf("VTV", 140)
    out["growth_vs_value"] = {
        "vug_20w": round(vug, 1) if vug is not None else None,
        "vtv_20w": round(vtv, 1) if vtv is not None else None,
        "signal": (1 if (vug and vtv and vug > vtv) else -1 if vug is not None else 0),
    }

    # 3) Zins-Regime
    tnx = safe_history("^TNX", period="1mo")
    out["zins_regime"] = {
        "zins_10y": float(tnx["Close"].iloc[-1]) if tnx is not None and not tnx.empty else None,
        "richtung": "steigend" if (tnx is not None and len(tnx) > 5 and
                                   tnx["Close"].iloc[-1] > tnx["Close"].iloc[-5]) else "fallend",
    }

    # 4) Gold-Regime
    gld = _perf("GLD", 60)
    spy_p = _perf("SPY", 60)
    out["gold_regime"] = {
        "gld_12w": round(gld, 1) if gld is not None else None,
        "signal": (1 if (gld and spy_p and gld > spy_p) else -1 if gld is not None else 0),
    }

    # 5) Krypto-Regime
    btc = _sma_ratio("BTC-USD", 200) if safe_history("BTC-USD", period="1y") is not None else None
    out["krypto_regime"] = {"btc_vs_sma200": round(btc, 3) if btc else None,
                            "signal": 1 if (btc and btc > 1) else -1 if btc else 0}

    # 6) Sektor-Rotation (11 SPDR, 4W-Momentum)
    sectors = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV",
               "XLY", "XLC", "XLRE"]
    rankings = {}
    for s in sectors:
        p = _perf(s, 28)
        rankings[s] = p if p is not None else -999
    ranked = sorted(rankings.items(), key=lambda kv: kv[1], reverse=True)
    out["sektor_rotation"] = {
        "top3": [r[0] for r in ranked[:3]],
        "bottom3": [r[0] for r in ranked[-3:]],
    }
    return out


def describe_regime(vec):
    """Menschlesbarer Kontext fuer Prompt."""
    if not vec:
        return "Regime: keine Daten"
    parts = []
    ro = vec["risk_on_off"].get("signal")
    parts.append(f"Risk-{'on' if ro == 1 else 'off' if ro == -1 else 'neutral'}")
    zr = vec["zins_regime"].get("richtung")
    parts.append(f"Zinsen {zr}")
    gr = vec["growth_vs_value"].get("signal")
    parts.append(f"Growth>{'Value' if gr == 1 else 'Value>Growth' if gr == -1 else '='}")
    parts.append(f"Sektor-Top: {','.join(vec['sektor_rotation']['top3'])}")
    return "Regime: " + " | ".join(parts)


if __name__ == "__main__":
    v = regime_vector()
    print(describe_regime(v))
