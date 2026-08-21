"""E1 — Korrelations- / Klumpenrisiko-Matrix (Code-only).

Bei /add: Warnung wenn neue Position >0.7 mit bestehendem Cluster korreliert.
Monatlich: volle Matrix + Cluster-Erkennung (Paare >0.7) -> in P5 injizieren.

Pitfalls (SKILL.md):
- Illiquide .V-Ticker haben Luecken -> min_periods=60 im corr(), sonst NaN
  als "zu wenig Daten" kennzeichnen.
- Stress-Korrelation: nur Tage mit SPY-Move < -1% (Crash-Korrelation).
- Einzelner Ticker -> kein MultiIndex -> abfangen.
"""
import numpy as np
import pandas as pd
from data_layer import download_closes
from db import get_active_positions, update_position


def correlation_matrix(tickers, period="6mo", min_periods=60):
    """Liefert (corr_df, warnungen). warnungen = Liste fehlender Ticker."""
    if len(tickers) < 2:
        return None, []
    data = download_closes(tickers, period=period)
    if data is None:
        return None, tickers[:]
    returns = data.pct_change().dropna()
    warnings = []
    if len(returns) < min_periods:
        warnings.append(f"Nur {len(returns)} Tage Daten (min_periods={min_periods})")
    corr = returns.corr(min_periods=min_periods)
    # NaN-Zeilen als Warnung
    for t in tickers:
        if t in corr.index and corr[t].isna().all():
            warnings.append(f"{t}: zu wenig Daten fuer Korrelation")
    return corr, warnings


def stress_correlation(tickers, period="6mo"):
    """Korrelation NUR ueber Tage mit SPY-Move <= -1% (ehrlich im Crash)."""
    if "SPY" not in tickers:
        tickers = list(tickers) + ["SPY"]
    data = download_closes(tickers, period=period)
    if data is None or "SPY" not in data.columns:
        return None
    spy_ret = data["SPY"].pct_change()
    mask = spy_ret <= -0.01
    sub = data[mask].pct_change().dropna()
    if len(sub) < 10:
        return None
    return sub.corr()


def detect_clusters(tickers, corr, threshold=0.7):
    """Paarweise >threshold = ein Cluster. Liefert {cluster_id: [ticker...]}."""
    if corr is None:
        return {}
    import itertools
    adj = {t: set() for t in tickers}
    for a, b in itertools.combinations(tickers, 2):
        try:
            if corr.loc[a, b] > threshold:
                adj[a].add(b)
                adj[b].add(a)
        except Exception:
            continue
    # Zusammenhaengende Komponenten
    clusters = {}
    seen = set()
    cid = 0
    for t in tickers:
        if t in seen or not adj[t]:
            continue
        stack = [t]
        comp = set()
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            comp.add(n)
            stack.extend(adj[n] - seen)
        if len(comp) >= 2:
            clusters[f"C{cid}"] = sorted(comp)
            cid += 1
    return clusters


def cluster_weights(clusters, positions):
    """Gewicht jedes Clusters in % des Portfolios (aus stueckzahl*kurs)."""
    # Marktwert pro Position
    value = {}
    total = 0.0
    for p in positions:
        v = (p.get("stueckzahl") or 0) * (p.get("kauf_kurs") or 0)
        value[p["ticker"]] = v
        total += v
    if total == 0:
        return {}
    out = {}
    for cid, members in clusters.items():
        w = sum(value.get(m, 0) for m in members) / total
        out[cid] = round(w * 100, 1)
    return out


def add_correlation_check(new_ticker, all_tickers, period="6mo"):
    """Bei /add: liefert (max_korr, warnung_text oder None)."""
    others = [t for t in all_tickers if t != new_ticker]
    if len(others) < 1:
        return 0.0, None
    corr, warns = correlation_matrix([new_ticker] + others, period=period)
    if corr is None:
        return 0.0, None
    row = corr.loc[new_ticker].drop(new_ticker, errors="ignore")
    if row.empty:
        return 0.0, None
    max_c = float(row.max())
    high = row[row > 0.7]
    if len(high) == 0:
        return max_c, None
    parts = [f"{t} ({row[t]:.2f})" for t in high.index]
    msg = (f"⚠️ {new_ticker} korreliert stark mit: {', '.join(parts)}. "
           f"Klumpenrisiko pruefen!")
    return max_c, msg


def monthly_matrix_for_p5(period="6mo"):
    """Vollstaendige Matrix + Cluster + Gewichte als P5-Injektions-Text."""
    positions = get_active_positions()
    tickers = [p["ticker"] for p in positions]
    if len(tickers) < 2:
        return "Zu wenig Positionen fuer Korrelationsmatrix.", {}
    corr, warns = correlation_matrix(tickers, period=period)
    clusters = detect_clusters(tickers, corr)
    weights = cluster_weights(clusters, positions)
    # DB aktualisieren: max_korrelation + cluster_id je Position
    if corr is not None:
        for p in positions:
            t = p["ticker"]
            if t in corr.index:
                mx = float(corr.loc[t].drop(t, errors="ignore").max())
                cid = next((c for c, m in clusters.items() if t in m), None)
                update_position(t, max_korrelation=mx, cluster_id=cid)
    lines = [f"Korrelationsmatrix ({period}):"]
    if warns:
        lines.append("Warnungen: " + "; ".join(warns))
    if clusters:
        for cid, members in clusters.items():
            lines.append(f"Cluster {cid}: {', '.join(members)} "
                         f"({weights.get(cid, 0)}% des Portfolios)")
    else:
        lines.append("Keine Klumpen-Cluster (alle Paare <=0.7).")
    # Stress-Korrelation
    sc = stress_correlation(tickers, period=period)
    if sc is not None:
        lines.append("Stress-Korrelation (nur SPY-Move <= -1% Tage): "
                     f"max paarweise = {sc.where(~np.eye(len(sc), dtype=bool)).max().max():.2f}")
    return "\n".join(lines), {"clusters": clusters, "weights": weights,
                              "corr": corr}


if __name__ == "__main__":
    # Beispiel (ohne echte DB): nur Funktionstest mit Symbolen
    c, w = correlation_matrix(["NVDA", "SMH", "UPST"])
    print("corr shape:", None if c is None else c.shape)
    print("clusters:", detect_clusters(["NVDA", "SMH", "UPST"], c))
