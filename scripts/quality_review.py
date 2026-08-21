"""
Wiki-Quality-Review — 222 Wiki-Files auf Datenqualitäts-Probleme prüfen.

Erzeugt Flags für:
  - fehlende LIVE-DATA-Blöcke (Rohgerüste)
  - stale Data (Stand-Datum > 7 Tage)
  - Werte ausserhalb des realistischen Bereichs (Margen > 100%, etc.)
  - Kurs ausserhalb 52W-Range
  - unbekannte recommendationKey-Enums
  - PEG/KGV/P/S mit Werten, wo n/a erwartet wird (negativ/zero)
  - Inkonsistenzen zwischen handgeschriebenem BEWERTUNG und LIVE-DATA
  - TODO-Marker (pending research)
  - Formatfehler (fehlende %-Zeichen, falsche Dezimaltrenner)

Output:
  - Wiki-Quality-Review-2026-08-21.md (Report)
  - stdout: Zusammenfassung + erste 30 Flagged-Files

Autor: Portfoliobot-Community
Schema-Referenz: Wiki-Daten-Schema.md
"""

import re
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Korrekte Pfade (scripts/quality_review.py steht unter scripts/)
# scripts/ -> Projekt-Root
_SCRIPT_DIR = Path(__file__).resolve().parent          # scripts/
_PROJECT_DIR = _SCRIPT_DIR.parent                     # Projekt-Root (portfoliobot/)

WIKI_DIR = _PROJECT_DIR / "wissensdatenbank" / "aktien"
REPORT_PATH = _PROJECT_DIR / "Wiki-Quality-Review-latest.md"
SCHEMA_PATH = _PROJECT_DIR / "wissensdatenbank" / "Wiki-Daten-Schema.md"
JSON_PATH = _PROJECT_DIR / "quality_review_results.json"

# Bekannte recommendationKey-Enums
RECOMMENDATION_ENUM = {"strong_buy", "buy", "hold", "underperform", "sell"}

# Felder, die als Prozent geschrieben werden (yfinance Dezimalbruch × 100)
PCT_FIELDS_LIVE = {
    "Earnings": "earningsGrowth",
    "Revenue": "revenueGrowth",
    "Brutto": "grossMargins",
    "Operating": "operatingMargins",
    "Profit": "profitMargins",
    "EBITDA": "ebitdaMargins",
    "ROE": "returnOnEquity",
    "ROA": "returnOnAssets",
    "Insider": "heldPercentInsiders",
    "Institution": "heldPercentInstitutions",
    "Short": "shortPercentOfFloat",
    "52W-Change": "fiftyTwoWeekChange",
}

# Felder, die als Zahl geschrieben werden (KEIN Prozent)
NON_PCT_LIVE = {
    "KGV ttm": "trailing_pe",
    "fwd": "forward_pe",
    "PEG": "peg",
    "P/S": "price_to_sales",
    "EV/Rev": "ev_to_revenue",
    "EV/EBITDA": "ev_to_ebitda",
    "D/E": "debt_to_equity",
    "Current": "current_ratio",
    "Quick": "quick_ratio",
    "50d MA": "fifty_day_average",
    "200d MA": "two_hundred_day_average",
    "PT": "target_mean_price",
}

# Sub-Parser für zusammengesetzte Label-Zeilen.
# Diese werden in `extract_live_data_block` nach dem Haupt-Parse extrahiert
# und als Einzelfelder in das Ergebnis-Dict übernommen, damit `check_file`
# direkt auf `live_block.get("Brutto")` etc. zugreifen kann.
#
# Jeder Eintrag: (zusammengesetztes Label, [(Feldname, Regex)], ...)

_COMPOUND_EXTRACTORS: list[tuple[str, list[tuple[str, str]]]] = [

    # Bewertung: "KGV ttm 33,2 / fwd 16,7 | PEG 0,6 | P/S 6,0 | EV/Rev 14,2 | EV/EBITDA 16,6"
    ("Bewertung", [
        ("KGV",    r"KGV\s*ttm\s+([+-]?[0-9]?\d+[.,]?\d*)"),
        ("KGV fwd", r"fwd\s+([+-]?[0-9]?\d+[.,]?\d*)"),
        ("PEG",    r"PEG\s+([+-]?[0-9]?\d+[.,]?\d*)"),
        ("P/S",    r"P/S\s+([+-]?[0-9]?\d+[.,]?\d*)"),
        ("EV/Rev", r"EV/Rev\s+([+-]?[0-9]?\d+[.,]?\d*)"),
        ("EV/EBITDA", r"EV/EBITDA\s+([+-]?[0-9]?\d+[.,]?\d*)"),
    ]),

    # Wachstum: "Earnings +20,0 % | Revenue +10,0 %"
    ("Wachstum (j/j)", [
        ("Earnings", r"Earnings\s+([+−]?\d+[.,]\d+)"),
        ("Revenue",  r"Revenue\s+([+−]?\d+[.,]\d+)"),
    ]),

    # Margen: "Brutto 74,1 % | Operating 65,6 % | Profit 63,0 % | EBITDA 65,3 %"
    ("Margen", [
        ("Brutto",    r"Brutto\s+([+-]?\d+[.,]\d+)"),
        ("Operating", r"Operating\s+([+-]?\d+[.,]\d+)"),
        ("Profit",    r"Profit\s+([+-]?\d+[.,]\d*)"),
        ("EBITDA",    r"EBITDA\s+([+-]?\d+[.,]\d*)"),
    ]),

    # Returns: "ROE 114,3 % | ROA 52,7 % | D/E 6,6 | Current 3,4 | Quick 2,1"
    ("Returns", [
        ("ROE",     r"ROE\s+([+-]?\d+[.,]\d+)"),
        ("ROA",     r"ROA\s+([+-]?\d+[.,]\d+)"),
        ("D/E",     r"D/E\s+([+-]?\d+[.,]\d*)"),
        ("Current", r"Current\s+([+-]?\d+[.,]\d*)"),
        ("Quick",   r"Quick\s+([+-]?\d+[.,]\d*)"),
    ]),

    # Cashflow: "FCF 1,0 Mrd USD | OCF 2,0 Mrd USD"  (Rohbetrag bereits eingelesen)
    ("Cashflow", []),

    # Analysten: "PT 120,0 USD 100,00 – 140,00 USD | buy | 10 Analysten"
    ("Analysten", [
        ("PT",            r"PT\s+([+-]?\d+[.,]\d+)\s*USD"),
        ("recommendationKey", r"\|\s+(\w+)\s*\|"),
    ]),

    # Sentiment: "Insider 2,0 % | Institution 60,0 % | Short 0,5 %"
    ("Sentiment", [
        ("Insider",    r"Insider\s+([+-]?\d+[.,]\d+)"),
        ("Institution",r"Institution\s+([+-]?\d+[.,]\d*)"),
        ("Short",      r"Short\s+([+-]?\d+[.,]\d*)"),
    ]),

    # Technik: "50d MA 123,4 | 200d MA 120,0 | 52W-Change +15,0 %"
    ("Technik", [
        ("50d MA",       r"50d\s+MA\s+([+-]?[0-9]?\d+[.,]?\d*)"),
        ("200d MA",      r"200d\s+MA\s+([+-]?[0-9]?\d+[.,]?\d*)"),
        ("52W-Change",   r"52W-Change\s+([+-]?[0-9]?\d+[.,]?\d*)"),
    ]),
]


def parse_pct_value(text: str) -> float | None:
    """Extrahiert Prozent-Wert aus '74,1 %' oder '+214,5 %' oder 'n/a'."""
    if text.strip().lower() in ("n/a", ""):
        return None
    m = re.search(r"([+\-]?[\d.,]+)\s*%", text)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_number_value(text: str) -> float | None:
    """Extrahiert Zahl aus '33,2' oder '16,7' oder 'n/a' oder '304,1 USD'."""
    if text.strip().lower() in ("n/a", ""):
        return None
    m = re.search(r"([+\-]?[\d.,]+)", text)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def extract_live_data_block(text: str) -> dict[str, str]:
    """Extrahiert den gesamten ## LIVE-DATA Block als Dict {Label: Wert-Zeile}."""
    m = re.search(r"## LIVE-DATA\s*\n\(yfinance-Snapshot\s+(\S+)\)\s*\n(.*?)(?=\n\n|\n## |\Z)", text, re.DOTALL)
    if not m:
        return {}
    timestamp = m.group(1)
    block_text = m.group(2)
    result: dict[str, str] = {"_timestamp": timestamp, "_block": block_text}
    for line in block_text.split("\n"):
        # Pattern: **Label:** Wert1 | Wert2 | ...
        lm = re.match(r"\*\*([^:]+):\*\*\s*(.*)", line.strip())
        if lm:
            label = lm.group(1).strip()
            value = lm.group(2).strip()
            result[label] = value

    # Sub-Parsing: zusammengesetzte Zeilen in Einzelfelder zerlegen
    # "Bewertung: KGV ttm 33,2 / fwd 16,7 | PEG 0,6 | ..."
    #   → result["KGV"] = "33,2", result["KGV fwd"] = "16,7", etc.
    for comp_label, field_extractors in _COMPOUND_EXTRACTORS:
        if comp_label in result:
            raw = result[comp_label]
            for field_name, field_re in field_extractors:
                m = re.search(field_re, raw)
                if m:
                    val = m.group(1).strip()
                    # PCT-Felder: %-Zeichen nachhängen, falls im
                    # Original-Text vorhanden (Pattern hat nur die Zahl
                    # erfasst, nicht das optionale %).
                    if field_name in PCT_FIELDS_LIVE:
                        pct_m = re.search(rf"{re.escape(val)}\s*%", raw)
                        if pct_m:
                            val = f"{val} %"
                    result[field_name] = val

    return result


def extract_bewertung_multiples(text: str) -> dict[str, str]:
    """Extrahiert handgeschriebene Multiples aus dem BEWERTUNG-Block.

    Pattern: 'KGV (forward) 22,59' oder 'KGV 34,48' oder 'PEG 0,51'
    """
    result: dict[str, str] = {}
    # Finde den BEWERTUNG-Block
    bm = re.search(r"## BEWERTUNG\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if not bm:
        return result
    bewertung_text = bm.group(1)
    # Multiplikte Pattern
    # KGV trailing / forward
    kgv_m = re.search(r"KGV\s*(?:\(trailing\))?\s*([\d.,]+)", bewertung_text)
    if kgv_m:
        result["KGV"] = kgv_m.group(1)
    kgv_fwd_m = re.search(r"KGV\s*\(forward\)\s*([\d.,]+)", bewertung_text)
    if kgv_fwd_m:
        result["KGV fwd"] = kgv_fwd_m.group(1)
    # PEG
    peg_m = re.search(r"PEG\s*([\d.,]+)", bewertung_text)
    if peg_m:
        result["PEG"] = peg_m.group(1)
    # P/S
    ps_m = re.search(r"EV/Sales\s*([\d.,]+)|P/S\s*([\d.,]+)", bewertung_text)
    if ps_m:
        result["P/S"] = ps_m.group(1) or ps_m.group(2)
    # EV/Rev, EV/EBITDA
    ev_rev_m = re.search(r"EV/Sales\s*([\d.,]+)", bewertung_text)
    if ev_rev_m:
        result["EV/Rev"] = ev_rev_m.group(1)
    ev_ebitda_m = re.search(r"EV/EBITDA\s*([\d.,]+)", bewertung_text)
    if ev_ebitda_m:
        result["EV/EBITDA"] = ev_ebitda_m.group(1)
    # Kurs
    kurs_m = re.search(r"Kurs\s*([\d.,]+)\s*(USD|EUR)", bewertung_text)
    if kurs_m:
        result["Kurs"] = kurs_m.group(1)
    return result


def check_file(path: Path) -> dict[str, Any]:
    """Gibt Flags-Dict + Status für ein Wiki-File zurück."""
    text = path.read_text(encoding="utf-8")
    flags: list[dict[str, Any]] = []
    live_block = extract_live_data_block(text)

    # Status-Basics
    ticker = path.stem
    has_live_data = len(live_block) > 2  # mehr als nur _timestamp und _block
    has_bewertung = "## BEWERTUNG" in text

    # 1) Fehlende LIVE-DATA → Rohgerüst
    if not has_live_data:
        return {
            "ticker": ticker,
            "status": "rohgeruest",
            "flags": [{"type": "no_live_data", "detail": "kein ## LIVE-DATA Block"}],
            "live_block": {},
            "bewertung_multiples": extract_bewertung_multiples(text),
        }

    # 2) Stand-Datum (Stale > 7 Tage)
    ts = live_block.get("_timestamp", "")
    try:
        if "Z" in ts:
            stand = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            stand = datetime.fromisoformat(ts)
        alter_tage = (datetime.now(timezone.utc) - stand.replace(tzinfo=timezone.utc)).days
        if alter_tage > 7:
            flags.append({
                "type": "stale_data",
                "detail": f"Stand {alter_tage}d alt ({ts})",
                "alter_tage": alter_tage,
            })
    except (ValueError, TypeError):
        flags.append({
            "type": "invalid_stand_format",
            "detail": f"Ungültiges Stand-Format: {ts}",
        })

    # 3) Margen/Returns/Sentiment: Werte in [0, 100] (Prozent)
    for label, yf_key in PCT_FIELDS_LIVE.items():
        live_val = live_block.get(label, "")
        if not live_val or live_val.lower() == "n/a":
            continue
        v = parse_pct_value(live_val)
        if v is not None and v < 0:
            flags.append({
                "type": "pct_negative",
                "field": label,
                "detail": f"{label}: {live_val} (negativ, unmöglich für Prozent)",
                "value": v,
            })
        elif v is not None and v > 100:
            # Ausnahme: Growth-Firmen können > 100% haben (Earnings +214%)
            # Nur flaggen, wenn es unrealistisch ist
            if v > 500:
                flags.append({
                    "type": "pct_unrealistic",
                    "field": label,
                    "detail": f"{label}: {live_val} (>500%, unrealistisch)",
                    "value": v,
                })
            elif label in ("Insider", "Institution", "Short") and v > 100:
                flags.append({
                    "type": "pct_sentiment_over_100",
                    "field": label,
                    "detail": f"{label}: {live_val} (>100% für Sentiment, unrealistisch)",
                    "value": v,
                })

    # 4) Kurs im 52W-Range
    current = live_block.get("Kurs") or live_block.get("_current")
    if current:
        low = live_block.get("52w_low")
        high = live_block.get("52w_high")
        cv = parse_number_value(current)
        lv = parse_number_value(low) if low else None
        hv = parse_number_value(high) if high else None
        if cv is not None and lv is not None and hv is not None and hv > lv:
            if cv < lv or cv > hv:
                flags.append({
                    "type": "price_out_of_range",
                    "detail": f"Kurs {current} ausserhalb 52W-Range [{low} – {high}]",
                    "kurs": cv,
                    "low": lv,
                    "high": hv,
                })

    # 5) recommendationKey-Enum-Check
    rec = live_block.get("recommendationKey", "")
    if rec:
        rec_lower = rec.strip().lower()
        if rec_lower not in RECOMMENDATION_ENUM:
            flags.append({
                "type": "unknown_recommendation",
                "detail": f"Unbekanntes recommendationKey: {rec} (erwartet: {RECOMMENDATION_ENUM})",
                "value": rec,
            })

    # 6) PEG negativ/zero → n/a erwartet
    peg_val = live_block.get("PEG", "")
    if peg_val and peg_val.lower() != "n/a":
        pv = parse_number_value(peg_val)
        if pv is not None and pv <= 0:
            flags.append({
                "type": "peg_zero_or_negative",
                "detail": f"PEG {peg_val} ≤ 0 (sollte n/a sein)",
                "value": pv,
            })

    # 7) KGV negativ/zero → n/a erwartet (bei Verlustfirmen)
    for label in ["KGV ttm", "fwd"]:
        val = live_block.get(label, "")
        if val and val.lower() != "n/a":
            nv = parse_number_value(val)
            if nv is not None and nv <= 0:
                flags.append({
                    "type": "kgv_zero_or_negative",
                    "field": label,
                    "detail": f"{label}: {val} ≤ 0 (sollte n/a sein)",
                    "value": nv,
                })

    # 8) BEWERTUNG vs LIVE-DATA Konsistenz (nur für Felder, die in beiden vorkommen)
    bw_multiples = extract_bewertung_multiples(text)
    for label in ["KGV", "KGV fwd", "PEG", "P/S", "EV/Rev", "EV/EBITDA"]:
        bw_val = bw_multiples.get(label)
        live_val = live_block.get(label)
        if bw_val and live_val and live_val.lower() != "n/a":
            bw_n = parse_number_value(bw_val)
            live_n = parse_number_value(live_val)
            if bw_n is not None and live_n is not None and abs(bw_n - live_n) > 5:
                flags.append({
                    "type": "inconsistency_bw_vs_live",
                    "field": label,
                    "detail": f"BEWERTUNG: {bw_val} vs LIVE-DATA: {live_val} (Differenz > 5)",
                    "bw": bw_n,
                    "live": live_n,
                    "diff": abs(bw_n - live_n),
                })

    # 9) TODO-Marker
    if re.search(r"TODO\s*\(Recherche", text):
        flags.append({
            "type": "pending_research",
            "detail": "TODO (Recherche ...) Marker vorhanden",
        })

    # 10) Format-Check: waren alle Prozent-Felder mit % geschrieben?
    for label in PCT_FIELDS_LIVE:
        val = live_block.get(label, "")
        if val and val.lower() != "n/a":
            if "%" not in val:
                flags.append({
                    "type": "missing_pct_sign",
                    "field": label,
                    "detail": f"{label}: '{val}' fehlt %-Zeichen",
                    "value": val,
                })

    # Status bestimmen
    status = "ok"
    if flags:
        # Sortierung nach Schweregrad
        critical = {"no_live_data", "pct_negative", "pct_unrealistic", "price_out_of_range",
                     "unknown_recommendation", "invalid_stand_format"}
        has_critical = any(f.get("type") in critical for f in flags)
        if has_critical:
            status = "flagged_critical"
        else:
            status = "flagged"

    # Zusätzliche Info für den Report
    result = {
        "ticker": ticker,
        "status": status,
        "flags": flags,
        "live_block": {k: v for k, v in live_block.items() if not k.startswith("_")},
        "bewertung_multiples": bw_multiples,
        "timestamp": live_block.get("_timestamp", ""),
        "has_live_data": has_live_data,
        "has_bewertung": has_bewertung,
    }
    return result


def bucket_sort(results: list[dict]) -> dict[str, list[dict]]:
    """Sortiert Ergebnisse in die 3 Buckets A/B/C.

    A — Auto-Fix: Formatfehler, die direkt korrigiert werden können
    B — Manueller Review: Inkonsistenzen, kritische Werte
    C — Backlog: Rohgerüste, pending research
    """
    bucket_a: list[dict] = []
    bucket_b: list[dict] = []
    bucket_c: list[dict] = []

    for r in results:
        flags = r["flags"]
        ticker = r["ticker"]

        # Bucket C: Rohgerüste oder pending research
        if r["status"] == "rohgeruest":
            bucket_c.append(r)
            continue
        if any(f["type"] == "pending_research" for f in flags):
            bucket_c.append(r)
            continue

        # Bucket B: Inkonsistenzen, kritische Werte, unbekannte Enums
        if any(f["type"] in {
            "inconsistency_bw_vs_live",
            "price_out_of_range",
            "pct_negative",
            "pct_unrealistic",
            "unknown_recommendation",
            "invalid_stand_format",
            "stale_data",
            "pct_sentiment_over_100",
        } for f in flags):
            bucket_b.append(r)
            continue

        # Bucket A: Formatfehler (Auto-Fix)
        if any(f["type"] in {
            "missing_pct_sign",
            "peg_zero_or_negative",
            "kgv_zero_or_negative",
        } for f in flags):
            bucket_a.append(r)
            continue

        # Falls doch nichts zutrifft, aber flags vorhanden → B
        if flags:
            bucket_b.append(r)
        else:
            # Kein Bucket → kein Eintrag (Status OK)
            pass

    return {"A": bucket_a, "B": bucket_b, "C": bucket_c}


def generate_report(results: list[dict], bucketed: dict[str, list[dict]]) -> str:
    """Erzeugt den Markdown-Report."""
    flagged = [r for r in results if r["status"] != "ok"]
    rohgerueste = [r for r in results if r["status"] == "rohgeruest"]

    lines: list[str] = []
    lines.append("# Wiki-Quality-Review 2026-08-21")
    lines.append("")
    lines.append(f"> **Erstellt von:** Portfoliobot Wiki-Quality-Review  ")
    lines.append(f"> **Stand:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC  ")
    lines.append(f"> **Geprüft:** {len(results)} Wiki-Files (222 mit LIVE-DATA + {len(rohgerueste)} Rohgerüste)")
    lines.append(f"> **Flagged:** {len(flagged)} Dateien mit einem oder mehr Flags")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Zusammenfassung")
    lines.append("")
    lines.append(f"- **Gesamt:** {len(results)} Dateien")
    lines.append(f"- **OK (keine Flags):** {len(results) - len(flagged)}")
    lines.append(f"- **Flagged:** {len(flagged)}")
    lines.append(f"- **Rohgerüste (kein LIVE-DATA):** {len(rohgerueste)}")
    lines.append("")
    lines.append("### Bucket-Verteilung")
    lines.append("")
    lines.append("| Bucket | Beschreibung | Dateien |")
    lines.append("|--------|-------------|---------|")
    lines.append(f"| **A — Auto-Fix** | Formatfehler, die direkt korrigiert werden können | {len(bucketed['A'])} |")
    lines.append(f"| **B — Manueller Review** | Inkonsistenzen, kritische Werte, Nik-Entscheidung nötig | {len(bucketed['B'])} |")
    lines.append(f"| **C — Backlog** | Rohgerüste + pending research (komplette Recherche nötig) | {len(bucketed['C'])} |")
    lines.append("")

    # Detaillierte Ergebnisse
    lines.append("---")
    lines.append("")
    lines.append("## Detaillierte Ergebnisse (alle Flagged-Dateien)")
    lines.append("")
    lines.append("| TICKER | STATUS | FLAGS | DETAIL |")
    lines.append("|--------|--------|-------|--------|")

    for r in sorted(results, key=lambda x: (x["status"] != "ok", x["ticker"])):
        if r["status"] == "ok":
            continue
        flags_str = ", ".join(f"[{f['type']}]" for f in r["flags"])
        details = "; ".join(f"{f.get('field','')}: {f.get('detail','')[:60]}" for f in r["flags"][:3])
        if len(r["flags"]) > 3:
            details += f" (+{len(r['flags'])-3} mehr)"
        lines.append(f"| `{r['ticker']}` | {r['status']} | {flags_str} | {details} |")

    lines.append("")

    # Bucket-A Details
    lines.append("---")
    lines.append("")
    lines.append("## Bucket A — Auto-Fix (Formatfehler)")
    lines.append("")
    if bucketed["A"]:
        for r in bucketed["A"]:
            lines.append(f"### `{r['ticker']}`")
            for f in r["flags"]:
                lines.append(f"- **{f['type']}:** {f.get('detail','')}")
            # Live-Block zeigen
            if r["live_block"]:
                lines.append("")
                lines.append("  **Live-Daten (aktuell):**")
                for k, v in r["live_block"].items():
                    lines.append(f"  - `{k}`: `{v}`")
            lines.append("")
    else:
        lines.append("Keine Dateien in Bucket A (keine Formatfehler).")
        lines.append("")

    # Bucket-B Details
    lines.append("---")
    lines.append("")
    lines.append("## Bucket B — Manueller Review (Nik-Entscheidung)")
    lines.append("")
    if bucketed["B"]:
        for r in bucketed["B"]:
            lines.append(f"### `{r['ticker']}`")
            for f in r["flags"]:
                detail = f.get("detail", "")
                if f.get("bw") is not None and f.get("live") is not None:
                    detail += f" (BEWERTUNG: {f['bw']} vs LIVE: {f['live']})"
                lines.append(f"- **{f['type']}:** {detail}")
            if r["live_block"]:
                lines.append("")
                lines.append("  **Live-Daten (aktuell):**")
                for k, v in r["live_block"].items():
                    lines.append(f"  - `{k}`: `{v}`")
            if r["bewertung_multiples"]:
                lines.append("")
                lines.append("  **Handgeschriebene Multiples (BEWERTUNG):**")
                for k, v in r["bewertung_multiples"].items():
                    lines.append(f"  - `{k}`: `{v}`")
            lines.append("")
    else:
        lines.append("Keine Dateien in Bucket B (keine kritischen Probleme).")
        lines.append("")

    # Bucket-C Details
    lines.append("---")
    lines.append("")
    lines.append("## Bucket C — Backlog (Recherche nötig)")
    lines.append("")
    if bucketed["C"]:
        for r in bucketed["C"]:
            lines.append(f"- **`{r['ticker']}`** — {r['status']}: " + 
                       "; ".join(f["type"] for f in r["flags"]))
        lines.append("")
        lines.append(f"Gesamt: {len(bucketed['C'])} Dateien. Diese Files benötigen entweder")
        lines.append("eine komplette Recherche (Rohgerüste) oder Firecrawl/Nous-Zugriff")
        lines.append("(pending research TODOs aus Feuerwehr-Modus).")
    else:
        lines.append("Keine Dateien in Bucket C.")
        lines.append("")

    # Top-5 häufigste Fehler
    lines.append("---")
    lines.append("")
    lines.append("## Top-Häufigste Fehler (alle Dateien)")
    lines.append("")
    type_counts: dict[str, int] = {}
    for r in results:
        for f in r["flags"]:
            t = f["type"]
            type_counts[t] = type_counts.get(t, 0) + 1
    sorted_types = sorted(type_counts.items(), key=lambda x: -x[1])
    lines.append("| Fehlertyp | Vorkommen |")
    lines.append("|-----------|----------|")
    for t, c in sorted_types[:10]:
        desc_map = {
            "stale_data": "Stand-Datum > 7 Tage alt",
            "inconsistency_bw_vs_live": "Diskrepanz zwischen BEWERTUNG und LIVE-DATA",
            "missing_pct_sign": "Prozent-Feld ohne %-Zeichen",
            "pending_research": "TODO (Recherche) Marker",
            "no_live_data": "Kein ## LIVE-DATA Block (Rohgerüst)",
            "price_out_of_range": "Kurs ausserhalb 52W-Range",
            "unknown_recommendation": "Unbekanntes recommendationKey",
        }
        lines.append(f"| `{t}` | {c} | {desc_map.get(t, t)} |")
    lines.append("")

    # Empfehlung
    lines.append("---")
    lines.append("")
    lines.append("## Empfehlung für Nik")
    lines.append("")
    lines.append("1. **Bucket A (Auto-Fix):** Diese Formatfehler können direkt korrigiert werden —")
    lines.append("   `missing_pct_sign` durch Hinzufügen von `%` im LIVE-DATA-Block.")
    lines.append("2. **Bucket B (Manueller Review):** Die Inkonsistenzen zwischen BEWERTUNG und")
    lines.append("   LIVE-DATA sollten priorisiert werden — LIVE-DATA ist frischer und hat Priorität.")
    lines.append("3. **Bucket C (Backlog):** Diese Files warten auf Firecrawl/Nous-Zugriff oder")
    lines.append("   vollständige Recherche. Kein Eilesfall — die Rohgerüste sind bewusst so belassen.")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Review durchgefuehrt von Portfoliobot Wiki-Quality-Review. Schema-Referenz: Wiki-Daten-Schema.md.*")

    return "\n".join(lines)


def main() -> None:
    files = sorted(WIKI_DIR.glob("*.md"))
    # README.md ist ein Meta-File, kein Wiki-Eintrag -- rausfiltern.
    # Path.stem enthaelt keine Extension, daher nur "README" vergleichen.
    files = [f for f in files if f.stem.upper() != "README"]

    results: list[dict] = []
    errors: list[dict] = []

    for path in files:
        try:
            result = check_file(path)
            results.append(result)
        except Exception as e:
            errors.append({
                "ticker": path.stem,
                "error": str(e),
                "path": str(path),
            })

    bucketed = bucket_sort(results)

    # Console output
    flagged = [r for r in results if r["status"] != "ok"]
    rohgerueste = [r for r in results if r["status"] == "rohgeruest"]

    print(f"Wiki-Quality-Review: {len(results)} geprüft, {len(flagged)} flagged, {len(rohgerueste)} Rohgerüste")
    print(f"Bucket A (Auto-Fix): {len(bucketed['A'])} | Bucket B (Review): {len(bucketed['B'])} | Bucket C (Backlog): {len(bucketed['C'])}")
    print()

    if flagged:
        print("Erste 40 Flagged-Dateien:")
        for r in flagged[:40]:
            flags_str = ", ".join(f["type"] for f in r["flags"])
            print(f"  {r['ticker']:10s} {r['status']:18s} {flags_str}")

    if errors:
        print()
        print(f"Dateien mit Lesefehlern: {len(errors)}")
        for e in errors:
            print(f"  FEHLER: {e['ticker']} — {e['error'][:80]}")

    # Report schreiben
    report = generate_report(results, bucketed)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print()
    print(f"Report geschrieben: {REPORT_PATH}")

    # Optional: JSON-Dump für weitere Verarbeitung
    json_path = _PROJECT_DIR / "quality_review_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"results": results, "buckets": {k: [r["ticker"] for r in v] for k, v in bucketed.items()},
                   "summary": {
                       "total": len(results),
                       "flagged": len(flagged),
                       "rohgerueste": len(rohgerueste),
                       "bucket_a": len(bucketed["A"]),
                       "bucket_b": len(bucketed["B"]),
                       "bucket_c": len(bucketed["C"]),
                   }}, f, ensure_ascii=False, indent=2)
    print(f"JSON-Dump: {json_path}")


if __name__ == "__main__":
    main()
