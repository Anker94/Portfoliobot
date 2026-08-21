"""E3 — Short-Interest & Days-to-Cover (in daily_check.py integriert).

yfinance t.info liefert shortPercentOfFloat, sharesShort, sharesShortPriorMonth,
dateShortInterest (gratis aus FINRA, ~2x monatlich). Alert bei Veraenderung
> +30% / < -30% MoM. In P3/P4 als Kontext injizieren.

Pitfall: info-Felder fehlen bei Nicht-US-Tickern -> dict.get() mit None.
Daten bis 2 Wochen alt -> dateShortInterest immer ausgeben.
"""
from data_layer import get_info
from db import get_active_positions, update_position, get_position


def fetch_short_interest(ticker):
    """Liefert Dict mit Werten oder None bei Fehler/fehlenden Feldern."""
    info = get_info(ticker)
    spi = info.get("shortPercentOfFloat")
    shares = info.get("sharesShort")
    prior = info.get("sharesShortPriorMonth")
    ds = info.get("dateShortInterest")
    if spi is None and shares is None:
        return None  # Nicht-US oder keine Daten
    result = {
        "short_percent_float": (spi * 100) if isinstance(spi, (int, float)) else None,
        "shares_short": shares,
        "shares_short_prior_month": prior,
        "date_short_interest": ds,
    }
    # Days-to-Cover geschaetzt: sharesShort / avg_daily_volume
    adv = info.get("averageVolume")
    if shares and adv:
        result["days_to_cover"] = round(shares / adv, 2)
    else:
        result["days_to_cover"] = None
    return result


def detect_change(ticker):
    """Vergleicht mit in DB gespeichertem Stand (treiber-JSON).
    Liefert (alert_text, None) bei relevanter Veraenderung."""
    cur = fetch_short_interest(ticker)
    if cur is None:
        return None
    pos = get_position(ticker)
    prev = {}
    if pos and pos.get("treiber"):
        import json
        try:
            prev = json.loads(pos["treiber"]).get("short_interest", {})
        except Exception:
            prev = {}
    alert = None
    if prev and prev.get("shares_short") and cur["shares_short"]:
        ratio = cur["shares_short"] / max(prev["shares_short"], 1)
        if ratio > 1.3 or ratio < 0.7:
            direction = "UP" if ratio > 1 else "DOWN"
            pct = abs(ratio - 1) * 100
            alert = (f"Short-Interest {ticker} {direction} {pct:.0f}% MoM auf "
                     f"{cur['short_percent_float']:.0f}% des Float "
                     f"(Days-to-Cover {cur['days_to_cover']}, Stand {cur['date_short_interest']})")
    # Stand in DB speichern (treiber-JSON erweitern)
    _store(ticker, cur)
    return alert


def _store(ticker, si):
    import json
    pos = get_position(ticker)
    if not pos:
        return
    treiber = {}
    if pos.get("treiber"):
        try:
            treiber = json.loads(pos["treiber"])
        except Exception:
            treiber = {}
    treiber["short_interest"] = si
    update_position(ticker, treiber=json.dumps(treiber, ensure_ascii=False))


def build_e3_context(ticker):
    si = fetch_short_interest(ticker)
    if si is None:
        return "Short-Interest: n/a (keine Daten / Nicht-US)"
    lines = [f"Short-Interest: {si['short_percent_float']:.0f}% des Float "
             f"(Stand {si['date_short_interest']}, bis 2 Wo alt)"]
    if si["days_to_cover"]:
        lines.append(f"Days-to-Cover: {si['days_to_cover']}")
    lines.append("Crowded Short: Squeeze-Potenzial bei guten News, aber viele "
                 "Profis wetten dagegen (beides nennen).")
    return " | ".join(lines)


if __name__ == "__main__":
    print(build_e3_context("GME"))
