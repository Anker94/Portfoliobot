"""
valuation_refresh.py — aktualisiert Bewertungs-Scores + LIVE-DATA-Blöcke
in wissensdatenbank/aktien/*.md.

Nutzt yfinance für Live-Kurse + Multiples und schreibt:
- einen ## LIVE-DATA Block (strukturierte yfinance-Felder, s. Schema unten)
- die numerischen Kennzahlen im BEWERTUNG-Block (Live-Score, 52W, PEG-Score, KGV-Score)

Lebenszyklen der Felder (yfinance Ticker.info):
  - Kennzahlen, die als Ratio geliefert werden (0..1 oder größer):
    Margen, Wachstum, ROE/ROA, Insider/Inst/short, 52W-Change → in % wandeln
  - Cashflow in Lokalwaehrung (Rohbetrag aus yfinance); Einheit automatisch:
    ≥ 1 Bio → "X,X Bio CCY", ≥ 1 Mrd → "X,X Mrd CCY", ≥ 1 Mio → "X Mio CCY"
  - Multiples (KGV, PEG, EV/Rev, EV/EBITDA, P/S, MA) → direkt als Zahl
  - Analysten-PT/Range → in Lokalwaehrung, Anzahl Analysten → Integer
  - Waehrung kommt aus yfinance info.currency (Fallback "USD"), kein hartcodiertes USD

Schema (Markdown, ohne Sterne innerhalb der Werte – Parser-sicher):
## LIVE-DATA
(yfinance-Snapshot <ISO>)

**Bewertung:** KGV ttm X / fwd Y | PEG Z | P/S A | EV/Rev B | EV/EBITDA C
**Wachstum (j/j):** Earnings +X % | Revenue +Y %
**Margen:** Brutto X % | Operating Y % | Profit Z % | EBITDA A %
**Returns:** ROE X % | ROA Y % | D/E Z | Current A | Quick B
**Cashflow:** FCF X,X Mrd CCY | OCF Y,Y Bio CCY (Einheit + CCY aus yfinance)
**Analysten:** PT X CCY (low – high CCY) | recommendationKey | N Analysten
**Sentiment:** Insider X % | Institution Y % | Short Z %
**Technik:** 50d MA X | 200d MA Y | 52W-Change +Z %

Nichtschreib-Richtlinien:
  - Alle Werte ohne Markdown-Bold/Inline-Code innerhalb der Werte (nur die
    Label-Zeilen sind **fett**).
  - None → "n/a" (niemals 0 oder "—").
  - Prozentwerte mit 1 Dezimalstelle und Komma als Dezimaltrenner.
"""

import os
import re
import sys
import json
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "lib"))

from paths import load_config
import yfinance as yf

WISSENSDATENBANK_DIR = _ROOT / "wissensdatenbank" / "aktien"
LOG_PATH = _ROOT / "data" / "markt_refresh.log"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_ticker_from_filename(path: Path) -> str | None:
    return path.stem.upper()


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        v = float(val)
        return v if v == v else None  # NaN check
    except (TypeError, ValueError):
        return None


def _fmt1(val: float | None, sign: bool = False, pct: bool = False) -> str:
    """Eine Dezimalstelle, Komma als Dezimaltrenner, Optional + bei > 0."""
    if val is None:
        return "n/a"
    prefix = "+" if sign and val > 0 else ""
    s = f"{prefix}{val:.1f}".replace(".", ",")
    if pct:
        s += "%"
    return s


def _fmt_cashflow(val: float | None, currency: str = "USD") -> str:
    """
    yfinance liefert FreeCashflow/OperatingCashflow in Lokalwaehrung
    (Rohbetrag, z.B. KRW fuer SKHYY, JPY fuer DAIKY, EUR fuer SMNEY/SMAYY).
    Einheit automatisch: ≥ 1 Bio → "X,X Bio CCY", ≥ 1 Mrd → "X,X Mrd CCY",
    ≥ 1 Mio → "X Mio CCY", sonst Rohbetrag.
    """
    if val is None:
        return "n/a"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= 1_000_000_000_000:
        s = f"{abs_val / 1_000_000_000_000:.1f}".replace(".", ",")
        return f"{sign}{s} Bio {currency}"
    if abs_val >= 1_000_000_000:
        s = f"{abs_val / 1_000_000_000:.1f}".replace(".", ",")
        return f"{sign}{s} Mrd {currency}"
    if abs_val >= 1_000_000:
        s = f"{abs_val / 1_000_000:.1f}".replace(".", ",")
        return f"{sign}{s} Mio {currency}"
    s = f"{abs_val:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}{s} {currency}"


def _fmt_int(val: float | None) -> str:
    """Gerundete Anzahl (z.B. Analysten)."""
    if val is None:
        return "n/a"
    return str(int(round(val)))


def _fmt_pct(val: float | None, sign: bool = False) -> str:
    """
    yfinance liefert Prozent-Felder als Dezimalbruch (0.74 = 74%).
    → mit 100 multiplizieren, 1 Dezimalstelle, Komma als Dezimaltrenner, %-Zeichen.
    """
    if val is None:
        return "n/a"
    val_x100 = val * 100
    prefix = "+" if sign and val_x100 > 0 else ""
    return f"{prefix}{val_x100:.1f}".replace(".", ",") + " %"


_RATE_LIMIT = Semaphore(2)
_RATE_SLEEP_S = 0.12

_SUFFIXES = [
    "", ".T", ".HK", ".AX", ".L", ".SI", ".KS", ".WA", ".SA", ".DE", ".PA",
    ".MI", ".AS", ".CO", ".V", ".CN", ".NE", ".KS"
]


def _try_yf_symbols(ticker: str) -> dict | None:
    tried = []
    for suf in _SUFFIXES:
        sym = ticker if suf == "" else f"{ticker}{suf}"
        if sym in tried:
            continue
        tried.append(sym)
        try:
            with _RATE_LIMIT:
                time.sleep(_RATE_SLEEP_S)
                t = yf.Ticker(sym)
                info = t.info or {}
        except Exception:
            continue
        current = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        if current is not None:
            live = {
                "symbol": sym,
                "current": current,
                "currency": (info.get("currency") or "USD").upper(),
                "high52w": _safe_float(info.get("fiftyTwoWeekHigh")),
                "low52w": _safe_float(info.get("fiftyTwoWeekLow")),
                "trailing_pe": _safe_float(info.get("trailingPE")),
                "forward_pe": _safe_float(info.get("forwardPE")),
                # PEG: beide Keys pruefen, trailingPegRatio bevorzugt
                "peg": _safe_float(info.get("trailingPegRatio") or info.get("pegRatio")),
                "price_to_sales": _safe_float(info.get("priceToSalesTrailing12Months")),
                "ev_to_revenue": _safe_float(info.get("enterpriseToRevenue")),
                "ev_to_ebitda": _safe_float(info.get("enterpriseToEbitda")),
                "forward_eps": _safe_float(info.get("forwardEps")),
                "trailing_eps": _safe_float(info.get("trailingEps")),
                "earnings_growth": _safe_float(info.get("earningsGrowth")),
                "revenue_growth": _safe_float(info.get("revenueGrowth")),
                "gross_margins": _safe_float(info.get("grossMargins")),
                "operating_margins": _safe_float(info.get("operatingMargins")),
                "profit_margins": _safe_float(info.get("profitMargins")),
                "ebitda_margins": _safe_float(info.get("ebitdaMargins")),
                "roe": _safe_float(info.get("returnOnEquity")),
                "roa": _safe_float(info.get("returnOnAssets")),
                "debt_to_equity": _safe_float(info.get("debtToEquity")),
                "current_ratio": _safe_float(info.get("currentRatio")),
                "quick_ratio": _safe_float(info.get("quickRatio")),
                "free_cashflow": _safe_float(info.get("freeCashflow")),
                "operating_cashflow": _safe_float(info.get("operatingCashflow")),
                "target_mean_price": _safe_float(info.get("targetMeanPrice")),
                "target_low_price": _safe_float(info.get("targetLowPrice")),
                "target_high_price": _safe_float(info.get("targetHighPrice")),
                "recommendation_key": info.get("recommendationKey") or "",
                "number_of_analysts": _safe_float(info.get("numberOfAnalystOpinions")),
                "held_percent_insiders": _safe_float(info.get("heldPercentInsiders")),
                "held_percent_institutions": _safe_float(info.get("heldPercentInstitutions")),
                "short_percent_of_float": _safe_float(info.get("shortPercentOfFloat")),
                "fifty_day_average": _safe_float(info.get("fiftyDayAverage")),
                "two_hundred_day_average": _safe_float(info.get("twoHundredDayAverage")),
                "fifty_two_week_change": _safe_float(info.get("fiftyTwoWeekChange")),
            }
            return live
    return None
def _build_live_data_block(live: dict) -> str:
    """
    Erzeugt den ## LIVE-DATA Block als mehrzeiligen Markdown-String.
    Alle Werte werden ohne Markdown-Formatierung innerhalb der Werte geschrieben;
    nur die Label-Zeilen werden **fett** formatiert (Parser-sicher).
    """
    # Bewertung
    trailing_pe = live.get("trailing_pe")
    forward_pe = live.get("forward_pe")
    peg = live.get("peg")
    price_to_sales = live.get("price_to_sales")
    ev_to_revenue = live.get("ev_to_revenue")
    ev_to_ebitda = live.get("ev_to_ebitda")

    # Wachstum
    earnings_growth = live.get("earnings_growth")
    revenue_growth = live.get("revenue_growth")

    # Margen
    gross_margins = live.get("gross_margins")
    operating_margins = live.get("operating_margins")
    profit_margins = live.get("profit_margins")
    ebitda_margins = live.get("ebitda_margins")

    # Returns
    roe = live.get("roe")
    roa = live.get("roa")
    debt_to_equity = live.get("debt_to_equity")
    current_ratio = live.get("current_ratio")
    quick_ratio = live.get("quick_ratio")

    # Cashflow
    free_cashflow = live.get("free_cashflow")
    operating_cashflow = live.get("operating_cashflow")

    # Analysten
    target_mean_price = live.get("target_mean_price")
    target_low_price = live.get("target_low_price")
    target_high_price = live.get("target_high_price")
    recommendation_key = live.get("recommendation_key", "")
    number_of_analysts = live.get("number_of_analysts")

    # Sentiment
    held_percent_insiders = live.get("held_percent_insiders")
    held_percent_institutions = live.get("held_percent_institutions")
    short_percent_of_float = live.get("short_percent_of_float")

    # Technik
    fifty_day_average = live.get("fifty_day_average")
    two_hundred_day_average = live.get("two_hundred_day_average")
    fifty_two_week_change = live.get("fifty_two_week_change")

    lines = []

    # ---- Bewertung ----
    lines.append(
        "**Bewertung:** "
        f"KGV ttm {_fmt1(trailing_pe)} / "
        f"fwd {_fmt1(forward_pe)} | "
        f"PEG {_fmt1(peg)} | "
        f"P/S {_fmt1(price_to_sales)} | "
        f"EV/Rev {_fmt1(ev_to_revenue)} | "
        f"EV/EBITDA {_fmt1(ev_to_ebitda)}"
    )

    # ---- Wachstum ----
    lines.append(
        "**Wachstum (j/j):** "
        f"Earnings {_fmt_pct(earnings_growth, sign=True)} | "
        f"Revenue {_fmt_pct(revenue_growth, sign=True)}"
    )

    # ---- Margen ----
    lines.append(
        "**Margen:** "
        f"Brutto {_fmt_pct(gross_margins)} | "
        f"Operating {_fmt_pct(operating_margins)} | "
        f"Profit {_fmt_pct(profit_margins)} | "
        f"EBITDA {_fmt_pct(ebitda_margins)}"
    )

    # ---- Returns ----
    lines.append(
        "**Returns:** "
        f"ROE {_fmt_pct(roe)} | "
        f"ROA {_fmt_pct(roa)} | "
        f"D/E {_fmt1(debt_to_equity)} | "
        f"Current {_fmt1(current_ratio)} | "
        f"Quick {_fmt1(quick_ratio)}"
    )

    # ---- Cashflow ----
    currency = live.get("currency", "USD") or "USD"
    lines.append(
        "**Cashflow:** "
        f"FCF {_fmt_cashflow(free_cashflow, currency)} | "
        f"OCF {_fmt_cashflow(operating_cashflow, currency)}"
    )

    # ---- Analysten ----
    # Ziel-Preis-Bereich in Lokalwaehrung (aus yfinance info.currency)
    if target_low_price is not None and target_high_price is not None:
        target_range = f"{target_low_price:.2f}".replace(".", ",") + " – " + \
                       f"{target_high_price:.2f}".replace(".", ",") + f" {currency}"
    elif target_low_price is not None:
        target_range = f"{target_low_price:.2f}".replace(".", ",") + f" {currency} (nur Untergrenze)"
    elif target_high_price is not None:
        target_range = f"{target_high_price:.2f}".replace(".", ",") + f" {currency} (nur Obergrenze)"
    else:
        target_range = "n/a"

    pt_str = _fmt1(target_mean_price, sign=False)
    # recommendationKey: strong_buy / buy / hold / sell — direkt übernehmen, ohne
    # Markdown-Formatierung (Parser-sicher)
    rec_display = recommendation_key if recommendation_key else "n/a"
    n_analysts_str = _fmt_int(number_of_analysts)

    lines.append(
        "**Analysten:** "
        f"PT {pt_str} {currency} {target_range} | "
        f"{rec_display} | "
        f"{n_analysts_str} Analysten"
    )

    # ---- Sentiment ----
    lines.append(
        "**Sentiment:** "
        f"Insider {_fmt_pct(held_percent_insiders)} | "
        f"Institution {_fmt_pct(held_percent_institutions)} | "
        f"Short {_fmt_pct(short_percent_of_float)}"
    )

    # ---- Technik ----
    lines.append(
        "**Technik:** "
        f"50d MA {_fmt1(fifty_day_average)} | "
        f"200d MA {_fmt1(two_hundred_day_average)} | "
        f"52W-Change {_fmt_pct(fifty_two_week_change, sign=True)}"
    )

    return "\n".join(lines)


def _calc_score_components(data: dict) -> dict:
    """Calculate 4 score components from live data, same logic as dashboard/app.py."""
    comps = {}
    current = data.get("current")
    high = data.get("high52w")
    low = data.get("low52w")

    if current is not None and high is not None and low is not None and high > low:
        pos = (current - low) / (high - low) * 100
        comps["52w_position_pct"] = round(max(0.0, min(100.0, pos)), 1)

    peg = data.get("peg")
    if peg is not None and peg > 0:
        if peg <= 1:
            s = 60 + (1 - peg) * 40
        else:
            s = max(0, 60 - (peg - 1) * 40)
        comps["peg"] = round(max(0.0, min(100.0, s)), 1)

    kgv = data.get("forward_pe") or data.get("trailing_pe")
    if kgv is not None and kgv > 0:
        if kgv <= 15:
            s = 60 + (15 - kgv) * (40 / 10)
        else:
            s = max(0, 60 - (kgv - 15) * (60 / 25))
        comps["kgv"] = round(max(0.0, min(100.0, s)), 1)

    if comps:
        score = sum(comps.values()) / len(comps)
        score = max(0.0, min(100.0, round(score, 1)))
    else:
        score = None

    if score is None:
        zone = "no-data"
    elif score < 25:
        zone = "stark-ueberteuert"
    elif score < 40:
        zone = "teuer"
    elif score < 60:
        zone = "fair"
    elif score < 75:
        zone = "guenstig"
    else:
        zone = "stark-guenstig"

    return {"components": comps, "score": score, "zone": zone}


def _build_numeric_lines(data: dict, valuation: dict) -> list[str]:
    comps = valuation.get("components", {})
    score = valuation.get("score")
    zone = valuation.get("zone", "no-data")
    pos = comps.get("52w_position_pct")
    peg_score = comps.get("peg")
    kgv_score = comps.get("kgv")

    lines = [
        f"- **Live-Score:** {score if score is not None else 'n/a'} / 100 → Zone: {zone}\n",
    ]
    if pos is not None:
        lines.append(f"- **52W-Position:** {pos} %\n")
    if peg_score is not None:
        lines.append(f"- **PEG-Score:** {peg_score} / 100\n")
    if kgv_score is not None:
        lines.append(f"- **KGV-Score:** {kgv_score} / 100\n")

    lines.append(f"- **Stand automatische Bewertung:** {_now_iso()}\n")
    lines.append("\n")
    return lines


def _update_bewertung_and_add_live_data(text: str, live: dict, valuation: dict) -> str:
    """
    Aktualisiert den BEWERTUNG-Block (zeitübliche numerische Zeilen) und
    fügt einen ## LIVE-DATA Block unmittelbar davor ein (zwischen
    letztem BEWERTUNG-Inhalt und Live-Score-Zeilen).

    Falls kein BEWERTUNG-Block existiert, wird kein LIVE-DATA-Block erstellt
    (das Live-Score-Update passiert ohnehin nicht → betroffene Dateien bleiben
    unverändert).

    Idempotenz: Falls bereits ein ## LIVE-DATA Block in text enthalten ist,
    wird nur der Zeitstempel + die Live-Score-Zeilen aktualisiert; kein
    zweiter Block wird eingefügt.
    """
    # Idempotenz: bereits vorhandenen LIVE-DATA-Block nur aktualisieren
    if "## LIVE-DATA" in text:
        return _update_existing_live_data_block(text, live, valuation)

    lines = text.splitlines(keepends=True)
    out_lines = []
    in_bewertung = False
    live_data_inserted = False
    score_lines_inserted = False

    skip_prefixes = (
        "- **Live-Score:**",
        "- **52W-Position:**",
        "- **PEG-Score:**",
        "- **KGV-Score:**",
        "- **Stand automatische Bewertung:**",
    )

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("## BEWERTUNG"):
            in_bewertung = True
            out_lines.append(line)
            continue

        if in_bewertung and stripped.startswith("## "):
            # Wir verlassen BEWERTUNG — füge (wenn noch nicht) Live-Data + Score ein
            if not score_lines_inserted:
                # LIVE-DATA Block (nur wenn wir Nummern einfügen werden)
                if not live_data_inserted:
                    live_block = _build_live_data_block(live)
                    out_lines.append("## LIVE-DATA\n")
                    out_lines.append(f"(yfinance-Snapshot {_now_iso()})\n\n")
                    out_lines.append(live_block)
                    out_lines.append("\n\n")
                    live_data_inserted = True
                out_lines.extend(_build_numeric_lines(live, valuation))
                score_lines_inserted = True
            in_bewertung = False
            out_lines.append(line)
            continue

        if in_bewertung:
            if any(stripped.startswith(p) for p in skip_prefixes):
                continue
            out_lines.append(line)
        else:
            out_lines.append(line)

    # Ende der Datei erreicht, noch im BEWERTUNG?
    if in_bewertung and not score_lines_inserted:
        if not live_data_inserted:
            live_block = _build_live_data_block(live)
            out_lines.append("## LIVE-DATA\n")
            out_lines.append(f"(yfinance-Snapshot {_now_iso()})\n\n")
            out_lines.append(live_block)
            out_lines.append("\n\n")
            live_data_inserted = True
        out_lines.extend(_build_numeric_lines(live, valuation))
        score_lines_inserted = True

    return "".join(out_lines)


def _update_existing_live_data_block(text: str, live: dict, valuation: dict) -> str:
    """
    Aktualisiert einen bereits vorhandenen ## LIVE-DATA Block:
    Entfernt ALLE vorhandenen ## LIVE-DATA Blöcke (mehrere möglich durch
    Doppel-Läufe) und fügt einen einzigen neuen ein, damit der Prozent-Bug-Fix
    und andere Änderungen in allen Files wirksam werden.
    """
    lines = text.splitlines(keepends=True)
    out_lines = []
    in_bewertung = False
    skip_mode = False  # True when skipping an old LIVE-DATA block
    inserted = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("## BEWERTUNG"):
            in_bewertung = True
            out_lines.append(line)
            continue

        if stripped.startswith("## "):
            # We've hit a section header
            if skip_mode:
                # Ending skip mode - but only if this isn't another LIVE-DATA
                if "## LIVE-DATA" in stripped:
                    # Still skipping (another LIVE-DATA block)
                    continue
                else:
                    skip_mode = False
                    # We just exited a LIVE-DATA block - now insert new one
                    # before this section header (unless already inserted)
                    if not inserted:
                        out_lines.append("## LIVE-DATA\n")
                        out_lines.append(f"(yfinance-Snapshot {_now_iso()})\n\n")
                        out_lines.append(_build_live_data_block(live))
                        out_lines.append("\n\n")
                        out_lines.extend(_build_numeric_lines(live, valuation))
                        inserted = True
                    in_bewertung = False
                    out_lines.append(line)
                    continue
            else:
                # Not in skip mode - normal processing
                if in_bewertung:
                    if "## LIVE-DATA" in stripped:
                        # This is an existing LIVE-DATA block - enter skip mode
                        skip_mode = True
                        continue
                    elif not inserted:
                        # This is another section header - insert before it
                        out_lines.append("## LIVE-DATA\n")
                        out_lines.append(f"(yfinance-Snapshot {_now_iso()})\n\n")
                        out_lines.append(_build_live_data_block(live))
                        out_lines.append("\n\n")
                        out_lines.extend(_build_numeric_lines(live, valuation))
                        inserted = True
                    in_bewertung = False
                    out_lines.append(line)
                    continue
                else:
                    out_lines.append(line)
                    continue

        if skip_mode:
            continue  # Skip everything inside old LIVE-DATA block

        if in_bewertung:
            out_lines.append(line)
        else:
            out_lines.append(line)

    # Handle end of file
    if in_bewertung and not inserted:
        out_lines.append("## LIVE-DATA\n")
        out_lines.append(f"(yfinance-Snapshot {_now_iso()})\n\n")
        out_lines.append(_build_live_data_block(live))
        out_lines.append("\n\n")
        out_lines.extend(_build_numeric_lines(live, valuation))

    return "".join(out_lines)


def _refresh_file(path: Path, apply: bool) -> dict:
    result = {"file": path.name, "status": "OK"}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"file": path.name, "status": "READ_FAIL", "error": str(e)}

    ticker = _extract_ticker_from_filename(path)
    if not ticker:
        return {"file": path.name, "status": "NO_TICKER"}

    live = _try_yf_symbols(ticker)
    if not live:
        return {"file": path.name, "status": "NO_LIVE_DATA", "ticker": ticker}

    valuation = _calc_score_components(live)

    # Prüfe, ob überhaupt ein BEWERTUNG-Block existiert – sonst keine Änderung
    if "## BEWERTUNG" not in text:
        result["score"] = valuation.get("score")
        result["zone"] = valuation.get("zone")
        result["symbol"] = live.get("symbol")
        result["has_bewertung_block"] = False
        result["updated"] = False
        result["has_live_data_block"] = "## LIVE-DATA" in text
        return result

    # Idempotenz-Check entfernt: nach Bug-Fix (Prozent × 100) sollen alle
    # bestehenden LIVE-DATA-Blöcke aktualisiert werden. Vorher verhinderte die
    # Prüfung writes auf bereits vorhandene Blöcke (220 Files mit alten Werten).
    # if "## LIVE-DATA" in text:
    #     result["score"] = valuation.get("score")
    #     result["zone"] = valuation.get("zone")
    #     result["symbol"] = live.get("symbol")
    #     result["has_bewertung_block"] = True
    #     result["updated"] = False
    #     result["has_live_data_block"] = True
    #     return result

    new_text = _update_bewertung_and_add_live_data(text, live, valuation)

    if apply and new_text != text:
        try:
            path.write_text(new_text, encoding="utf-8")
            result["updated"] = True
            result["has_live_data_block"] = "## LIVE-DATA" in new_text
        except Exception as e:
            return {"file": path.name, "status": "WRITE_FAIL", "error": str(e)}
    else:
        result["updated"] = False
        result["has_live_data_block"] = "## LIVE-DATA" in new_text

    result["ticker"] = ticker
    result["score"] = valuation.get("score")
    result["zone"] = valuation.get("zone")
    result["symbol"] = live.get("symbol")
    result["has_bewertung_block"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Valuation refresh + LIVE-DATA for wissensdatenbank")
    parser.add_argument("--apply", action="store_true", help="Actually write changes")
    args = parser.parse_args()

    if not WISSENSDATENBANK_DIR.exists():
        print(f"FAIL: {WISSENSDATENBANK_DIR} missing")
        sys.exit(1)

    files = sorted(WISSENSDATENBANK_DIR.glob("*.md"))
    files = [f for f in files if f.name.upper() != "README.MD"]

    results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_refresh_file, path, args.apply): path for path in files}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                path = futures[future]
                results.append({"file": path.name, "status": "EXEC_FAIL", "error": str(e)})

    ok = sum(1 for r in results if r.get("status") == "OK")
    fail = sum(1 for r in results if r.get("status") != "OK")
    updated = sum(1 for r in results if r.get("updated"))
    with_live_data = sum(1 for r in results if r.get("has_live_data_block"))
    no_bewertung = sum(1 for r in results if not r.get("has_bewertung_block"))

    log_entry = {
        "zeit": _now_iso(),
        "apply": args.apply,
        "ok": ok,
        "fail": fail,
        "updated": updated,
        "with_live_data": with_live_data,
        "no_bewertung_block": no_bewertung,
        "results": results[:10],  # cap log size
    }
    os.makedirs(LOG_PATH.parent, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    print(f"Valuation {_now_iso()}: {ok} OK, {fail} FAIL, {updated} updated, "
          f"{with_live_data} mit LIVE-DATA, {no_bewertung} ohne BEWERTUNG-Block "
          f"(apply={args.apply})")

    if updated > 0:
        sys.exit(0)
    elif fail > 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
