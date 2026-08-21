"""Portfoliobot Dashboard -- Helper-Modul.

C2-Refactor (2026-08-21): Aus dashboard/app.py herausgeloest. Enthaelt alle
Pure-Helper, Data-Access-Wrapper, TV/Chart-Wrapper und Hermes-Subprocess-
Wrapper. KEIN Flask-Import hier -- alle Funktionen sind framework-agnostisch
und testbar.

Modul-State (Cache, Hermes-Config) lebt hier, weil die Logik an die Daten
gebunden ist, nicht an die Flask-App. Routen in dashboard/app.py verwenden
diese Funktionen ueber `from lib.portfolio_helpers import ...`.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus


# Pfad-Aufloesung: lib/ -> Portfoliobot/ (Projekt-Root)
LIB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = LIB_DIR.parent

# Daten-Pfade (alle innerhalb des Projekt-Roots, sanitized 2026-08-21)
DB_PATH = PROJECT_ROOT / "data" / "stock.db"
TRIGGER_LOG_PATH = PROJECT_ROOT / "data" / "trigger_log.json"
SPARPLAN_DIR = PROJECT_ROOT / "data"
WISSENSDATENBANK_DIR = PROJECT_ROOT / "wissensdatenbank" / "aktien"
BANKER_COUNCIL_JSON = PROJECT_ROOT / "data" / "banker_council_latest.json"

SPARPLAN_FILES = {
    "default": "sparplan-tracking.json",
}

# Subprocess-Konfiguration: Python-Interpreter fuer Job-Runner.
# Default: sys.executable (das Python, das das Dashboard startet).
# Ueberschreibbar via PORTFOLIOBOT_PYTHON env var, falls ein separates
# Venv fuer die Skripte genutzt werden soll.
import sys as _sys
HERMES_PYTHON = Path(
    os.environ.get("PORTFOLIOBOT_PYTHON") or _sys.executable
)

# Risiko-Stufen:
#   "read"   = nur lesen, kein Daten-Schreiben
#   "write"  = schreibt in JSON/DB/MD (lokal, reversibel via Git)
#   "buy"    = loest eine Kauf-Aktion aus (Vorsicht!)
HERMES_JOBS: dict[str, dict] = {
    "valuation_refresh": {
        "label": "Alle Preise + Bewertung aktualisieren",
        "script": "valuation_refresh.py",
        "args": ["--apply"],
        "risk": "write",
        "desc": "Holt yfinance-Snapshots fuer alle 234 Wiki-Ticker, schreibt den ## LIVE-DATA-Block (Preise + KGV/PEG/P/S/EV-Rev/EV-EBITDA) in jede .md-Datei und aktualisiert den Live-Score im BEWERTUNG-Block. Ueberschreibt alte LIVE-DATA-Werte idempotent. Preise und Score sind hier gekoppelt (Score liest LIVE-DATA) -- fuer reine Positions-Preise siehe portfolio_data_refresh.",
        "timeout": 240,
    },
    "sparplan_perf_refresh": {
        "label": "Sparplan-Performance refresh",
        "script": "sparplan_perf_refresh.py",
        "args": [],
        "risk": "write",
        "desc": "Performance-Felder in sparplan-tracking{,-mavis,-banker}.json neu berechnen aus aktuellen Kursen. Struktur der JSONs bleibt unveraendert.",
        "timeout": 60,
    },
    "trigger_check": {
        "label": "Trigger-Check (alle Mavis-Picks)",
        "script": "trigger_check.py",
        "args": ["--all"],
        "risk": "read",
        "desc": "Prueft 9 Mavis-Picks auf Insider-Aktivitaet, News, Macro-Drivers. Read-only-Analyse.",
        "timeout": 120,
    },
    "banker_council": {
        "label": "5-Banker-Council Screening",
        "script": "banker_council.py",
        "args": ["--top", "20"],
        "risk": "read",
        "desc": "5-Rollen-Screening (Goldman/Morgan-Stanley/Bridgewater/JPMorgan/BlackRock) auf Basis yfinance-Fakten. Liefert Fakten, nicht LLMs.",
        "timeout": 180,
    },
    "daily_dashboard_check": {
        "label": "Dashboard-Health-Check",
        "script": "daily_dashboard_check.py",
        "args": [],
        "risk": "read",
        "desc": "Prueft Dashboard-Endpoint, DB-Erreichbarkeit, Render-Zeit. Read-only.",
        "timeout": 30,
    },
    "outcome_calculator": {
        "label": "Outcome-Reports berechnen",
        "script": "outcome_calculator.py",
        "args": [],
        "risk": "read",
        "desc": "Vergleicht ausgeloeste Trigger vs. tatsaechliche Performance. Read-only-Reporting.",
        "timeout": 60,
    },
    "portfolio_woche_mavis_dry": {
        "label": "Sparplan-Wochenkauf (Vorschau)",
        "script": "portfolio_woche_mavis.py",
        "args": ["--dry-run"],
        "risk": "buy",
        "desc": "Trockenlauf: zeigt an, was Mavis diese Woche kaufen wuerde, OHNE etwas zu loggen. Zum Pruefen vor echtem Kauf.",
        "timeout": 60,
    },
    "portfolio_data_refresh": {
        "label": "Portfolio-Daten refresh",
        "script": "portfolio_data_refresh.py",
        "args": ["--apply"],
        "risk": "write",
        "desc": "Aktualisiert Kurse/Marktdaten fuer aktive Positionen in stock.db. Schreibt in DB, braucht vorhandene positionen-Tabelle.",
        "timeout": 180,
    },
    "trigger_check_wiki": {
        "label": "Trigger-Check (alle Wiki-Ticker)",
        "script": "trigger_check.py",
        # args werden dynamisch gebaut: Ticker-Liste wird zur Laufzeit aus
        # der Wiki-Datenbank gelesen (alle .md ausser README). Sobald Hermes
        # --all-wiki direkt anbietet, koennen wir diese Spezialbehandlung
        # wieder rauswerfen.
        "args": ["--json", "--log"],
        "risk": "read",
        "desc": "Prueft ALLE ~233 Wiki-Ticker auf Insider-Aktivitaet, News, Macro-Drivers. Async (Background-Thread) weil 5-15 Min Laufzeit. Output wird in trigger_log.json geloggt.",
        "timeout": 900,  # 15 Min -- 233 Ticker * Insider+13F+Options+Cluster
        "async": True,    # via /admin/run/<id> in Background-Thread, Status-Polling
        "wiki_ticker_file": True,  # Spezialflag: Mavis-Wrapper baut die Ticker-Liste
    },
}

# In-Memory State fuer laufende/laufzeit-kuerzlich-beendete Hermes-Jobs.
# Struktur: {job_id: {"state": "idle"|"running"|"done"|"error",
#                     "started_at": float|None, "finished_at": float|None,
#                     "returncode": int|None, "stdout": str, "stderr": str,
#                     "duration_s": float|None, "ticker_count": int|None}}
# Wird bei jedem /admin/run/<id>-Call neu initialisiert; alte Eintraege
# bleiben 1h erhalten, damit das Polling-UI auch nach Abschluss noch
# das Ergebnis anzeigen kann (kein "Job unbekannt" direkt nach Start).
_JOB_STATE: dict = {}
_JOB_STATE_MAX_AGE = 3600  # Sekunden


# Lokales Hermes-lib (Market-Daten). Nach sys.path-Setup weiter unten in
# app.py, damit top-level importierbar. Bei Bedarf via try/except absichern.
try:
    from lib import market_data  # type: ignore
    _MARKET_DATA_AVAILABLE = True
except ImportError:
    market_data = None
    _MARKET_DATA_AVAILABLE = False


# ---------------------------------------------------------------------------
# Daten-Layer
# ---------------------------------------------------------------------------
def _safe_call(label: str, fn, default=None):
    """DB-/IO-Call mit einheitlichem Fehler-Reporting. Dashboard stuerzt nie ab."""
    try:
        return fn(), None
    except Exception as exc:
        return default, f"{label}: {type(exc).__name__}: {exc}"


def _read_json(path: Path) -> tuple[Optional[Any], Optional[str]]:
    if not path.exists():
        return None, f"Datei fehlt: {path.name}"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except json.JSONDecodeError as exc:
        return None, f"{path.name}: JSON-Parse-Fehler: {exc}"


def get_db_state() -> dict:
    """Liest positionen, empfehlungen, konto, bestand, orders."""
    from lib import db  # local import (sys.path oben)

    out: dict[str, Any] = {"errors": []}

    pos, err = _safe_call("positionen", db.get_active_positions, default=[])
    out["positionen"] = pos or []
    if err:
        out["errors"].append(err)

    # letzte Empfehlung je Ticker (1 DB-Hit pro Position)
    rec_map: dict[str, dict] = {}
    for p in out["positionen"]:
        rec, e2 = _safe_call(f"empfehlung {p['ticker']}", lambda: db.get_last_recommendation(p["ticker"]))
        if rec:
            rec_map[p["ticker"]] = rec
        if e2:
            out["errors"].append(e2)
    out["letzte_empfehlung"] = rec_map

    konto, e3 = _safe_call("konto", db.konto_get)
    out["konto"] = konto
    if e3:
        out["errors"].append(e3)

    bestand, e4 = _safe_call("bestand", db.bestand_all, default=[])
    out["paper_bestand"] = bestand or []
    if e4:
        out["errors"].append(e4)

    orders, e5 = _safe_call("orders", lambda: db.orders_list(limit=50))
    out["orders"] = orders or []
    if e5:
        out["errors"].append(e5)

    return out


def compute_saeulen_verteilung(track_data: dict) -> dict:
    """Aggregiert die EUR-Investments pro Saeule.
    Bevorzugt das explizite Feld 'saeulen_verteilung_eur' (Mavis-Typ),
    faellt sonst auf Aggregation aus den 'positionen' zurueck.
    """
    if not isinstance(track_data, dict):
        return {}
    explicit = track_data.get("saeulen_verteilung_eur")
    if isinstance(explicit, dict) and explicit:
        return explicit
    # Aggregation aus positionen
    pos = track_data.get("positionen", []) or []
    if not isinstance(pos, list):
        return {}
    agg: dict[str, float] = {}
    for p in pos:
        if not isinstance(p, dict):
            continue
        saeule = p.get("saeule") or "Ohne Säule"
        eur = p.get("eur_investiert") or 0
        try:
            eur = float(eur)
        except (TypeError, ValueError):
            eur = 0.0
        agg[saeule] = agg.get(saeule, 0.0) + eur
    return agg


def get_sparplan_state() -> dict:
    out = {"tracks": {}, "saeulen": {}, "errors": []}
    for key, filename in SPARPLAN_FILES.items():
        path = SPARPLAN_DIR / filename
        data, err = _read_json(path)
        if data is not None:
            out["tracks"][key] = data
            out["saeulen"][key] = compute_saeulen_verteilung(data)
        else:
            out["tracks"][key] = None
            out["saeulen"][key] = {}
        if err:
            out["errors"].append(f"{filename}: {err}")
    return out


def get_trigger_state() -> dict:
    out = {"entries": [], "errors": []}
    data, err = _read_json(TRIGGER_LOG_PATH)
    if data is not None:
        # Schema (lt. lib/trigger_engine.py log_trigger): List[Dict]
        # Sortierung: neueste zuerst (id/stand absteigend)
        try:
            entries = list(data)
            entries.sort(key=lambda e: e.get("stand") or "", reverse=True)
            out["entries"] = entries
        except TypeError:
            out["errors"].append("trigger_log.json: unerwartete Struktur")
    elif err:
        out["errors"].append(err)
    return out


def get_wissensdatenbank_state(
    view: str = "cards",
    sort: str = "score",
    direction: str = "desc",
    zones: Optional[set] = None,
    query: str = "",
    min_size: Optional[float] = None,
    max_size: Optional[float] = None,
    min_confidence: str = "insufficient",
) -> dict:
    out = {
        "ticker_files": [], "errors": [],
        "view": view, "sort": sort, "direction": direction,
        "active_zones": sorted(zones) if zones else [],
        "all_zones": ["stark-guenstig", "guenstig", "fair", "teuer", "stark-ueberteuert", "no-data"],
        "query": query, "min_size": min_size, "max_size": max_size,
        "min_confidence": min_confidence,
    }

    # 5-Banker-Score (Hermes-Territorium, read-only). Einmal pro State-Load.
    banker_scores = _load_banker_scores()
    banker_stand = banker_scores.get("__stand__")
    banker_stale = _banker_stale_info(banker_stand)
    if not WISSENSDATENBANK_DIR.exists():
        out["errors"].append(f"Ordner fehlt: {WISSENSDATENBANK_DIR}")
        return out
    try:
        files = sorted(WISSENSDATENBANK_DIR.glob("*.md"))
        files = [f for f in files if f.name.upper() != "README.MD"]
        ticker_files = []
        for f in files:
            try:
                # Erste 2 KB fuer H1-Header, Rest fuer KPI-Parser
                with open(f, encoding="utf-8") as fh:
                    full_text = fh.read()
                head = full_text[:2048]
            except Exception:
                full_text, head = "", ""
            company = parse_company_name(head) or f.stem
            kpis = parse_md_kpis(full_text)
            valuation = score_valuation(kpis)
            ticker_files.append({
                "name": f.stem,
                "filename": f.name,
                "company": company,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "score": valuation.get("score"),
                "zone": valuation.get("zone", "no-data"),
                "components": len(valuation.get("components", {})),
                "components_available": valuation.get("components_available", 0),
                "confidence": valuation.get("confidence", "insufficient"),
                # 5-Banker-Score (1-10, separater Methodik-Score)
                "banker_score": (banker_scores.get(f.stem) or {}).get("score_1_10"),
                "banker_score_100": (banker_scores.get(f.stem) or {}).get("score_100"),
                "banker_label": _banker_label((banker_scores.get(f.stem) or {}).get("score_1_10")),
            })
        # Filter: Zonen
        if zones:
            zones_set = set(zones)
            ticker_files = [t for t in ticker_files if t["zone"] in zones_set]
        # Filter: Suche (Ticker oder Company)
        if query:
            q_lower = query.strip().lower()
            ticker_files = [
                t for t in ticker_files
                if q_lower in t["name"].lower() or q_lower in t["company"].lower()
            ]
        # Filter: KB-Range
        if min_size is not None:
            ticker_files = [t for t in ticker_files if t["size_kb"] >= float(min_size)]
        if max_size is not None:
            ticker_files = [t for t in ticker_files if t["size_kb"] <= float(max_size)]
        # Filter: Mindest-Confidence (Audit-P1, 2026-08-21)
        # Reihenfolge: high (4) > medium (3) > low (2) > insufficient (0/1).
        # Default "insufficient" = kein Filter; User kann hoeher setzen.
        _conf_rank = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}
        _conf_min = _conf_rank.get(min_confidence, 0)
        if _conf_min > 0:
            ticker_files = [
                t for t in ticker_files
                if _conf_rank.get(t.get("confidence", "insufficient"), 0) >= _conf_min
            ]
        # Sortierung
        sort_keys = {
            "ticker":  lambda t: t["name"].lower(),
            "company": lambda t: t["company"].lower(),
            "size":    lambda t: t["size_kb"],
            "date":    lambda t: t["modified"],
        }
        if sort in ("score", "banker"):
            # Numerische Felder: None-Werte ans Ende, desc sortiert absteigend
            # (negative Keys), asc aufsteigend. Beide Felder folgen identischer
            # Logik, daher der DRY-Helper.
            field = "banker_score" if sort == "banker" else "score"
            ticker_files.sort(
                key=lambda t, f=field: (
                    t.get(f) is None,
                    -(t.get(f) or 0) if direction == "desc" else (t.get(f) or 0),
                )
            )
        elif sort in sort_keys:
            ticker_files.sort(key=sort_keys[sort], reverse=(direction == "desc"))
        out["ticker_files"] = ticker_files
        out["banker_stand"] = banker_stand
        out["banker_stale"] = banker_stale
    except Exception as exc:
        out["errors"].append(f"Globe fehlgeschlagen: {exc}")
    return out


def render_markdown_safe(text: str) -> str:
    """Markdown -> HTML, mit bleach-Whitelist. Lokal klein, aber sauber."""
    import bleach
    import markdown as md_lib

    allowed_tags = [
        "h1", "h2", "h3", "h4", "h5", "h6",
        "p", "ul", "ol", "li",
        "strong", "em", "code", "pre",
        "blockquote",
        "table", "thead", "tbody", "tr", "th", "td",
        "a", "br", "hr",
    ]
    allowed_attrs = {
        "a": ["href", "title"],
    }
    html = md_lib.markdown(
        text,
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html",
    )
    return bleach.clean(
        html,
        tags=allowed_tags,
        attributes=allowed_attrs,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


# ---------------------------------------------------------------------------
# .md-Parser: strukturierte Kennzahlen + Bewertungs-Score (auto)
# ---------------------------------------------------------------------------
_DASH_CHARS = "[–—\-]"  # en-dash, em-dash, hyphen
_PLACEHOLDER = re.compile(r"\b(n/?a|nicht\s+anwendbar|nicht\s+ermittelbar|nicht\s+verfuegbar|unbekannt)\b", re.IGNORECASE)
# Unterstuetzte Waehrungen (fuer multi-currency Parser-Patterns)
_CCY = r"USD|EUR|GBP|DKK|CAD|AUD|HKD|GBX|JPY|CHF|SEK|NOK"

# Pattern-Listen fuer die Fliessttext-Extraktion. Die Reihenfolge ist wichtig:
# spezifischere Pattern zuerst, generische zuletzt. Pro KPI wird die Liste
# linear durchlaufen, das erste plausible Match gewinnt.
_KURS_PATTERNS = [
    # 1) "Kurs $18,82" / "Kurs: $18,82" (Dollar-Zeichen, optionaler Doppelpunkt)
    r"Kurs:?\s+\$\s*([\d.,]+)",
    # 2) "Kurs 22,20 EUR" / "Kurs: 35.360 JPY" / "Kurs 166,33 USD" (mit Waehrung + Doppelpunkt optional)
    r"Kurs:?\s+([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\s+(?:" + _CCY + r")",
    # 3) "Kurs A$2,93" (A$, C$ etc., mit optionalem Doppelpunkt)
    r"Kurs:?\s+[A-Z]\$\s*([\d.,]+)",
    # 4) "Kurs ca. 0,06 CAD" (mit "ca.", mit und ohne Range)
    r"Kurs\s+ca\.\s+([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\s+(?:" + _CCY + r")",
    # 5) "NYSE-Kurs 66,86-67,04 USD" (Boersen-Prefix)
    r"(?:NYSE|NASDAQ|Xetra|Frankfurt|London|TSX|ASX|LSE|SEHK)-Kurs:?\s+([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\s*(?:\$|\b(?:" + _CCY + r"))",
    # 6) "Aktienkurs AUD 0,1350" / "Aktienkurs: AUD 0,1350" (Waehrung vor Zahl)
    r"Aktienkurs:?\s+(?:" + _CCY + r")\s+([\d.,]+)",
    # 7) "BYDDY-ADR 11,26 USD" (ADR-Ticker, kurs gleich mit dabei)
    r"ADR\s+(?:[A-Z0-9]+\s+)?([\d.,]+)\s+USD",
    # 8) "ADR XIACY 16,45 USD" (ADR-Symbol vor USD)
    r"ADR\s+[A-Z]+\s+([\d.,]+)\s+USD",
]

_MC_PATTERNS = [
    # 1) "Marktkap. $6,28 Mrd." (Abkuerzung, mit Dollar optional)
    r"Marktkap\.\s+\$?\s*([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\s+(Billion|Bio\.?|Mrd\.?|Mio\.?)\b",
    # 2) "Marktkapitalisierung **9,91-10,58 Mrd. USD**" (voll + Bold + Range)
    r"Marktkapitalisierung\s+\*?\s*([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\s*(Billion|Bio\.?|Mrd\.?|Mio\.?)\b",
    # 3) "Marktkapitalisierung ca. 5,56 Mio. CAD" (mit Waehrung am Ende)
    r"Marktkapitalisierung\s+ca\.\s+([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\s*(Billion|Bio\.?|Mrd\.?|Mio\.?)\s+(?:" + _CCY + r")",
    # 4) "Marktkapitalisierung AUD 307,24 Mio." (Waehrung vor Zahl)
    r"Marktkapitalisierung\s+(?:" + _CCY + r")\s+([\d.,]+)\s*(Billion|Bio\.?|Mrd\.?|Mio\.?)?",
    # 5) "Marktkap. ca. 362 Mio. EUR" (Kurzform, mit "ca.", mit Waehrung)
    r"Marktkap\.\s+ca\.\s+\$?([\d.,]+)\s*(Billion|Bio\.?|Mrd\.?|Mio\.?)?\s*(?:" + _CCY + r")?",
]

_KGV_TTM_PATTERNS = [
    # 1) "KGV (TTM) 29,44" / "KGV (trailing) 34,48" / "KGV (TTM) ca. 8,10"
    r"KGV\s+\((?:TTM|trailing)\)\s*(?:ca\.\s*)?([\d.,]+)",
    # 2) "KGV trailing ca. 31,6" / "KGV (trailing) ca. 31,6" (Klammern optional)
    r"KGV\s+\(?trailing\)?\s*(?:ca\.\s*)?([\d.,]+)",
    # 3) "Trailing-KGV 26,1-34,7" (mit Range)
    r"Trailing-KGV\s+(?!N/?A)(?:ca\.\s*)?([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?",
    # 4) "KGV 24,71" (generisch, aber kein forward)
    r"\bKGV\s+(?:ca\.\s*)?([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\b(?!\s*forward)",
    # 5) "P/E (trailing) 28,5" / "P/E 47,4" / "P/E 81,1" (Klammern optional)
    r"\bP/E\s+\(?trailing\)?\s+(?:ca\.\s*)?([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\b",
    r"\bP/E\s+(?:ca\.\s*)?([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\b",
]

_KGV_FWD_PATTERNS = [
    r"Forward-KGV\s+(?:ca\.\s*)?([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?",
    # KGV forward mit oder ohne Klammern: "KGV forward 22,59" / "KGV (forward) 22,59"
    r"KGV\s+\(?forward\)?\s+(?:ca\.\s*)?([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?",
]

_52W_PATTERNS = [
    # 1) "52W-Range $12,53–$58,15" (em-dash mit $)
    r"52W-Range\s+\$([\d.,]+)\s*[–\-]\s*\$([\d.,]+)",
    # 2) "52-Wochen-Range: 362,50-546,17 USD" (mit USD)
    r"52-Wochen-Range:\s+([\d.,]+)\s*[–\-]\s*([\d.,]+)\s+(?:" + _CCY + r")",
    # 3) "52-Wochen-Range: 19,11–51,70 EUR" (em-dash + EUR)
    r"52-Wochen-Range:\s+([\d.,]+)\s*[–\-]\s*([\d.,]+)\s+(?:" + _CCY + r")",
    # 4) "52-Wochen-Range A$2,43-7,52" (mit Praefix, ohne "USD")
    r"52-Wochen-Range\s+[A-Z]?\$?([\d.,]+)\s*[–\-]\s*[A-Z]?\$?([\d.,]+)",
    # 5) "52-Wochen-Range: AUD 0,1120–0,2700" (Waehrung davor)
    r"52-Wochen-Range:\s+(?:" + _CCY + r")\s+([\d.,]+)\s*[–\-]\s*([\d.,]+)",
    # 6) "52-Wochen-Range: Quellen streuen ... X,XX-Y,YY EUR" (Trenn-Text dazwischen)
    # [^X\d]*? = non-greedy, keine Ziffern, frisst den Trenn-Text
    r"52-Wochen-Range:\s*[^X\d]*?([\d.,]+)\s*[–\-]\s*([\d.,]+)\s+(?:" + _CCY + r")",
    # 7) "52-Wochen-\n  Range A$2,43-7,52" (Newline mitten im Wort, mit DOTALL)
    r"52-Wochen-[\s\-]*Range\s+[A-Z]?\$?([\d.,]+)\s*[–\-]\s*[A-Z]?\$?([\d.,]+)",
]

_PT_PATTERNS = [
    r"Konsens-Kursziel\s+\$([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?",
    r"Konsens-Kursziel\s+([\d.,]+)\s+(?:" + _CCY + r")",
    r"Kursziel\s+\$([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?",
    r"Kursziel\s+([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\s+(?:" + _CCY + r")",
    r"Kursziel[- ](?:Median|Durchschnitt|Spanne)\s+\$?([\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?",
]

_UPSIDE_PATTERNS = [
    # "Kursziel $X (+XX% ggü. Kurs)" / "Kursziel X,XX EUR (+XX% ggü. Kurs)"
    r"Kursziel\s+[\$A-Z]?[\d.,]+\s*(?:\(|\[)\s*([+\-][\d.,]+)\s*%\s*(?:ggü\.|vs\.|gegen[üu]ber)[^)\]]*",
    # "Upside +7,5-11,4 %"
    r"Upside\s+([+\-][\d.,]+)(?:\s*[–\-]\s*[\d.,]+)?\s*%",
    # "Kursziel ... +42,2 % ggü. aktuellem Kurs" (locker)
    r"\(\s*([+\-][\d.,]+)\s*%\s+vs\.",
    # "+53% ggü. Kurs" (sehr locker, aber kommt im Text vor)
    r"([+\-][\d.,]+)\s*%\s*(?:ggü\.|vs\.|gegen[üu]ber)",
]


def _to_number_de(s: str) -> Optional[float]:
    """Deutsche Zahlen-Schreibweise (Komma als Dezimaltrenner) -> float.
    '1.234,56' -> 1234.56, '5,45' -> 5.45, '79' -> 79.0
    """
    if s is None:
        return None
    s = s.strip()
    if not s or _PLACEHOLDER.search(s):
        return None
    # Heuristik: wenn sowohl . als auch , vorkommen, ist . Tausender und , Dezimal
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        # nur Komma: ist Dezimaltrenner
        s = s.replace(",", ".")
    # jetzt nur noch Ziffern, Punkt, Minus
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _section(text: str, name: str) -> str:
    """Schneidet den Inhalt eines '## NAME'-Blocks aus (bis zum naechsten '## ')."""
    m = re.search(
        rf"^##\s+{re.escape(name)}\s*$(.+?)(?=^##\s+|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# 5-Banker-Score (Hermes-Territorium: data/banker_council_latest.json)
# ---------------------------------------------------------------------------
# Nik-Wunsch 2026-08-21: separater Score 1-10, der die 5-Banker-Methodik aus
# scripts/banker_council.py zusammenfasst. Skript produziert score_gesamt
# (0-100, Summe aus val+qual+tech+risk+rec+smart_money); wir skalieren auf
# 1-10. Read-only: Mavis schreibt NIE in data/banker_council_latest.json.
_BANKER_SCORE_CACHE: Optional[dict] = None
_BANKER_SCORE_CACHE_TS: float = 0.0
_BANKER_SCORE_TTL = 60.0  # Sekunden -- die JSON aendert sich nur nach manuellem
                          # Hermes-Trigger (Admin-Konsole), 60s ist komfortabel.


def _load_banker_scores() -> dict:
    """Liest data/banker_council_latest.json und gibt ein Dict
    {ticker_key: {score_1_10, score_100, components, stand, ...}} zurueck.

    Cache: 60s (gueltig fuer den typischen Klick-Flow im Dashboard).
    Fehlerfaelle: Datei fehlt -> {}; JSON kaputt -> {}. In beiden Faellen
    zeigt das Template 'kein Banker-Score', das ist besser als ein Crash.
    """
    global _BANKER_SCORE_CACHE, _BANKER_SCORE_CACHE_TS
    now = time.time()
    if _BANKER_SCORE_CACHE is not None and (now - _BANKER_SCORE_CACHE_TS) < _BANKER_SCORE_TTL:
        return _BANKER_SCORE_CACHE

    out: dict = {}
    if not BANKER_COUNCIL_JSON.exists():
        _BANKER_SCORE_CACHE = out
        _BANKER_SCORE_CACHE_TS = now
        return out
    try:
        with open(BANKER_COUNCIL_JSON, "r", encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError):
        _BANKER_SCORE_CACHE = out
        _BANKER_SCORE_CACHE_TS = now
        return out

    stand = report.get("stand")
    for t in report.get("titel", []):
        key = t.get("key")
        if not key:
            continue
        score_100 = t.get("score_gesamt")
        score_1_10 = None
        if isinstance(score_100, (int, float)) and score_100 > 0:
            # Lineare Skalierung 0-100 -> 0-10, gerundet auf 1 Dezimalstelle.
            # Beispiel: SKHYY 94,8 -> 9,5; 24,9 -> 2,5.
            score_1_10 = round(float(score_100) / 10.0, 1)
            # Defense-in-Depth: clamp auf 0-10.
            score_1_10 = max(0.0, min(10.0, score_1_10))
        out[key] = {
            "score_1_10": score_1_10,
            "score_100": score_100,
            "value": t.get("score_value"),
            "qualitaet": t.get("score_qualitaet"),
            "technik": t.get("score_technik"),
            "risiko": t.get("score_risiko"),
            "analysten_konsens": t.get("score_analysten_konsens"),
            "smart_money": t.get("score_smart_money"),
            "smart_money_institutionen": t.get("smart_money_institutionen", []),
            "recommendation": t.get("recommendation"),
            "upside_pct": t.get("upside_pct"),
            "n_analysten": t.get("n_analysten"),
            "dcf_fair_value": t.get("dcf_fair_value"),
            "dcf_warnung": t.get("dcf_warnung"),
        }
    out["__stand__"] = stand  # Spezial-Key, nicht ticker-spezifisch
    _BANKER_SCORE_CACHE = out
    _BANKER_SCORE_CACHE_TS = now
    return out


# Schwellwert fuer die Stale-Warnung: Banker-Score-Daten aelter als N Tage
# loest einen sichtbaren Banner im Dashboard aus. Default 7 Tage, weil ein
# Refresh normalerweise woechentlich laeuft (Cron-Job in Hermes-Territorium).
_BANKER_STALE_THRESHOLD_DAYS = 7


def _banker_stale_info(stand: Optional[str], threshold_days: int = _BANKER_STALE_THRESHOLD_DAYS) -> dict:
    """Prueft, ob der Banker-Score-Datenstand aelter als threshold_days ist.

    Args:
        stand: ISO-Datetime-String aus data/banker_council_latest.json (z.B.
            "2026-08-20T23:15:45.571495+00:00"). None wenn JSON fehlt/leer.
        threshold_days: Schwellwert in Tagen (Default 7).

    Returns:
        Dict mit:
        - is_stale: bool -- True wenn Daten aelter als threshold_days ODER kein Stand
        - days: Optional[int] -- Anzahl Tage seit Stand (None wenn nicht parsbar)
        - threshold: int -- der verwendete Schwellwert (fuer Template-Anzeige)
        - reason: str -- "no_stand" | "fresh" | "stale" (fuer Template-Logik)
    """
    if not stand:
        return {"is_stale": True, "days": None, "threshold": threshold_days, "reason": "no_stand"}
    try:
        # ISO-Format mit oder ohne Zeitzone
        ts = datetime.fromisoformat(str(stand).replace("Z", "+00:00"))
        # Falls kein tzinfo: als UTC interpretieren (Banker-Score kommt mit +00:00)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - ts
        days = int(delta.total_seconds() // 86400)
        return {
            "is_stale": days >= threshold_days,
            "days": days,
            "threshold": threshold_days,
            "reason": "stale" if days >= threshold_days else "fresh",
        }
    except (ValueError, TypeError):
        return {"is_stale": True, "days": None, "threshold": threshold_days, "reason": "no_stand"}


def _banker_label(score_1_10: Optional[float]) -> str:
    """Mapping 1-10 -> Bewertungs-Label (5-Banker-Konsens-Sprache).

    Bucket-Grenzen: 1-2 meiden, 2-4 schwach, 4-6 neutral, 6-8 kaufenswert,
    8-10 stark kaufenswert. Anpassbar -- bewusst nicht aus empirischer
    Kalibrierung abgeleitet, das ist die naechste P2-Aufgabe (Backtesting).
    """
    if score_1_10 is None:
        return "kein Banker-Score"
    if score_1_10 < 2.0:
        return "stark meiden"
    if score_1_10 < 4.0:
        return "schwach"
    if score_1_10 < 6.0:
        return "neutral"
    if score_1_10 < 8.0:
        return "kaufenswert"
    return "stark kaufenswert"


def _banker_zone_class(score_1_10: Optional[float]) -> str:
    """CSS-Klassen-Mapping fuer die farbliche Hervorhebung."""
    if score_1_10 is None:
        return "no-data"
    if score_1_10 < 4.0:
        return "bad"        # rot-Look (vermeiden)
    if score_1_10 < 6.0:
        return "fair"       # neutral
    return "good"           # gruen-Look (kaufenswert)


def parse_company_name(text: str) -> Optional[str]:
    """Extrahiert den Unternehmensnamen aus dem H1-Header.
    Schema: '# <Name> (<TICKER>)' -> <Name>. Fallback: erste H1-Zeile.
    """
    m = re.match(r"^#\s+(.+?)\s*\(([A-Z0-9.\-]+)\)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.match(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return None


def parse_md_kpis(text: str) -> dict:
    """Liest strukturierte Kennzahlen aus dem BEWERTUNG-Block einer .md-Datei.
    Tolerant: fehlende Felder -> None. Pattern-Listen (s.o.) decken die
    gaengigen Fliesstext-Formate ab (Range-Spannen, Multi-Currency,
    Markdown-Bold, "Marktkap."-Abkuerzung, "Kurs ca.", "P/E" statt "KGV").

    Pattern-Listen werden linear durchlaufen, das erste plausible Match gewinnt.
    """
    be = _section(text, "BEWERTUNG")
    out: dict = {
        "kurs": None,
        "mc_bio": None,
        "kgv_trailing": None,
        "kgv_forward": None,
        "peg": None,
        "beta": None,
        "52w_low": None,
        "52w_high": None,
        "52w_position_pct": None,
        "consensus": None,
        "pt": None,
        "upside_pct": None,
        "konsens_basis": None,  # Anzahl Analysten
    }

    # ----- KURS -----
    for p in _KURS_PATTERNS:
        m = re.search(p, be, re.IGNORECASE)
        if m:
            v = _to_number_de(m.group(1))
            if v is not None and v > 0.0001:  # Pennystock-Schwelle
                out["kurs"] = v
                break

    # ----- MARKTKAPITALISIERUNG (in Bio USD normalisiert) -----
    for p in _MC_PATTERNS:
        m = re.search(p, be, re.IGNORECASE)
        if m:
            val = _to_number_de(m.group(1))
            unit = (m.group(2) or "Mio.").lower()
            if val is not None:
                if "bio" in unit or "billion" in unit:
                    out["mc_bio"] = val
                elif "mrd" in unit:
                    out["mc_bio"] = val / 1000.0
                elif "mio" in unit:
                    out["mc_bio"] = val / 1_000_000.0
                else:
                    # Fallback: ohne Einheit -> Mrd.
                    out["mc_bio"] = val / 1000.0
                break

    # ----- KGV trailing -----
    for p in _KGV_TTM_PATTERNS:
        m = re.search(p, be, re.IGNORECASE)
        if m:
            v = _to_number_de(m.group(1))
            if v is not None and v < 10000:  # NBIS hat 1795, alles > 10000 ist Quatsch
                out["kgv_trailing"] = v
                break

    # ----- KGV forward -----
    for p in _KGV_FWD_PATTERNS:
        m = re.search(p, be, re.IGNORECASE)
        if m:
            v = _to_number_de(m.group(1))
            if v is not None:
                out["kgv_forward"] = v
                break

    # ----- PEG -----
    m = re.search(r"\bPEG\s+([\d.,]+|nicht\s+ermittelbar|n/?a)", be)
    if m:
        out["peg"] = _to_number_de(m.group(1))

    # ----- Beta (5J falls vorhanden) -----
    m = re.search(r"Beta[^\d\n-]*\(?5[JJ]\)?\s*([\d.,]+)|Beta\s*\(?5[JJ]\)?\s*([\d.,]+)", be)
    if m:
        val = m.group(1) or m.group(2)
        out["beta"] = _to_number_de(val)
    if out["beta"] is None:
        m = re.search(r"Beta\s*([\d.,]+)", be)
        if m:
            out["beta"] = _to_number_de(m.group(1))

    # ----- KURS (WIZZ- und JPY-Spezialfall vorher) -----
    # WIZZ hat Kurse in GBX (Pence) im Format "Kurs 1.080–1.081 GBX".
    # Da _to_number_de den Punkt als Dezimaltrenner interpretiert, wuerde
    # 1.080 -> 1.08 werden. Sonder-Pattern: 3-stellige Zahl mit Punkt davor
    # in GBX -> Tausender-Format, also 1.080 -> 1080. Range "-1.081" optional.
    m_gbx = re.search(r"Kurs:?\s+(\d{1,3}\.\d{3})(?:\s*[–\-]\s*[\d.,]+)?\s+GBX", be)
    if m_gbx:
        val_str = m_gbx.group(1).replace(".", "")
        try:
            out["kurs"] = float(val_str)
        except ValueError:
            pass

    # JPY-Spezialfall: "Kurs: 35.360 JPY" / "Kurs 1.234.567 JPY" (Tausender-Punkt
    # statt Dezimal). JPY hat keine Dezimalstellen, also ist jeder Punkt ein
    # Tausender-Trenner. Pattern erlaubt optionalen Doppelpunkt + beliebig viele
    # 3-stellige Punkt-Gruppen.
    if out["kurs"] is None or out["kurs"] < 100:
        m_jpy = re.search(r"Kurs:?\s+(\d{1,3}(?:\.\d{3})+)\s+JPY", be)
        if m_jpy:
            val_str = m_jpy.group(1).replace(".", "")
            try:
                out["kurs"] = float(val_str)
            except ValueError:
                pass

    # ----- 52-Wochen-Range (low, high) -----
    # WIZZ-Spezialfall vorher: "52-Wochen-Range: 832–1.453 GBX" (Tausender-Punkt)
    m_gbx_52w = re.search(r"52-Wochen-Range:\s+(\d{3})\s*[–\-]\s*(\d{1,3}\.\d{3})\s+GBX", be, re.IGNORECASE)
    if m_gbx_52w:
        try:
            lo = float(m_gbx_52w.group(1))
            hi = float(m_gbx_52w.group(2).replace(".", ""))
            if hi > lo:
                out["52w_low"] = lo
                out["52w_high"] = hi
        except ValueError:
            pass

    # DOTALL damit 52-Wochen-[\s\-]*Range auch Newlines matcht (VUL hat
    # "52-Wochen-\n  Range" mit Zeilenumbruch im Wort).
    if out["52w_low"] is None:
        for p in _52W_PATTERNS:
            m = re.search(p, be, re.IGNORECASE | re.DOTALL)
            if m:
                lo = _to_number_de(m.group(1))
                hi = _to_number_de(m.group(2))
                if lo is not None and hi is not None and hi > lo:
                    out["52w_low"] = lo
                    out["52w_high"] = hi
                    break

    # 52W-Position nur berechnen wenn Kurs + Range da
    if (out["kurs"] is not None and out["52w_low"] is not None
            and out["52w_high"] is not None and out["52w_high"] > out["52w_low"]):
        out["52w_position_pct"] = round(
            (out["kurs"] - out["52w_low"]) / (out["52w_high"] - out["52w_low"]) * 100, 1
        )

    # ----- Analyst-Konsens (Reihenfolge: Strong > Moderate > Buy > Hold > Sell) -----
    consensus_text = be.lower()
    counts = [int(x) for x in re.findall(r"(\d+)\s*Analysten", be)]
    if counts:
        out["konsens_basis"] = max(counts)
    if "strong buy" in consensus_text:
        out["consensus"] = "Strong Buy"
    elif "moderate buy" in consensus_text:
        out["consensus"] = "Moderate Buy"
    elif re.search(r"\bbuy\b", consensus_text) and "hold" not in consensus_text and "sell" not in consensus_text:
        out["consensus"] = "Buy"
    elif "hold" in consensus_text and "sell" not in consensus_text:
        out["consensus"] = "Hold"
    elif "sell" in consensus_text:
        out["consensus"] = "Sell"

    # ----- Kursziel (PT) -----
    for p in _PT_PATTERNS:
        m = re.search(p, be, re.IGNORECASE)
        if m:
            v = _to_number_de(m.group(1))
            if v is not None and v > 0.01:
                out["pt"] = v
                break

    # ----- Upside (% Kursziel vs. aktueller Kurs) -----
    for p in _UPSIDE_PATTERNS:
        m = re.search(p, be, re.IGNORECASE)
        if m:
            v = _to_number_de(m.group(1))
            if v is not None:
                out["upside_pct"] = v
                break

    # Fallback: aus PT - Kurs berechnen
    if out["upside_pct"] is None and out["pt"] is not None and out["kurs"] is not None and out["kurs"] > 0:
        out["upside_pct"] = round((out["pt"] - out["kurs"]) / out["kurs"] * 100, 1)

    return out


# Bewertungs-Score: 0 (stark ueberbewertet) .. 100 (stark unterbewertet)
# Komponenten werden auf 0..100 normalisiert und gleich gewichtet gemittelt.
# Confidence: heuristisches Vertrauensmass basierend auf Anzahl verfuegbarer
# Komponenten. 1 Komponente = insufficient (kein Score), 2 = low, 3 = medium,
# 4 = high. Hintergrund: externe Audit-Empfehlung 2026-08-21 (P1).
# Hinweis: PEG und KGV sind nicht unabhaengig (PEG = KGV / Growth) und
# upside_pct haengt indirekt am KGV (via PT-Berechnung). Hohe Confidence
# ist also nicht gleichbedeutend mit unabhaengiger Validierung.
_CONFIDENCE_LEVELS = {4: "high", 3: "medium", 2: "low", 1: "insufficient", 0: "insufficient"}


def _confidence_label(n_components: int) -> str:
    return _CONFIDENCE_LEVELS.get(max(0, min(4, n_components)), "insufficient")


def score_valuation(kpis: dict) -> dict:
    """Liefert composite score (0..100), components (dict), zone (Label),
    confidence (high/medium/low/insufficient), components_available (int 0..4).

    Bei components_available < 2 wird score=None gesetzt (Zone 'no-data'),
    damit der Score nicht als gleichwertig mit volldatierten Aktien sortiert
    wird. Bei 2 Komponenten bleibt der Score berechnet, aber confidence='low'
    -- die UI muss das sichtbar machen.
    """
    components: dict[str, float] = {}

    # 52W-Position: 0% -> 100, 100% -> 0 (linear)
    pos = kpis.get("52w_position_pct")
    if pos is not None:
        components["52w"] = max(0.0, min(100.0, 100 - pos))

    # PEG: 0 -> 100, 1 -> 60, 2 -> 20, >=3 -> 0 (stufig-linear)
    peg = kpis.get("peg")
    if peg is not None and peg > 0:
        if peg <= 1:
            s = 60 + (1 - peg) * 40  # peg=0 -> 100, peg=1 -> 60
        else:
            s = max(0, 60 - (peg - 1) * 40)  # peg=2 -> 20, peg=3 -> 0
        components["peg"] = max(0.0, min(100.0, s))

    # KGV forward (oder trailing als Fallback): 5 -> 100, 15 -> 60, 25 -> 20, >=40 -> 0
    # Cap auf 100, weil bei KGV<5 (z.B. SKHYY 3,98 oder 8,10) die Formel
    # 60+(15-kgv)*4 > 100 ergeben kann.
    kgv = kpis.get("kgv_forward") or kpis.get("kgv_trailing")
    if kgv is not None and kgv > 0:
        if kgv <= 15:
            s = 60 + (15 - kgv) * (40 / 10)  # 5 -> 100, 15 -> 60
        else:
            s = max(0, 60 - (kgv - 15) * (60 / 25))  # 15 -> 60, 40 -> 0
        components["kgv"] = max(0.0, min(100.0, s))

    # Upside: +30% -> 100, 0% -> 50, -30% -> 0 (linear)
    up = kpis.get("upside_pct")
    if up is not None:
        s = 50 + up * (50 / 30)
        components["upside"] = max(0.0, min(100.0, s))

    if components:
        # Final-Clamp als Defense-in-Depth (sollte nie noetig sein)
        score = sum(components.values()) / len(components)
        score = max(0.0, min(100.0, score))
        # Auf eine Nachkommastelle runden
        score = round(score, 1)
        # Komponenten ebenfalls runden
        components = {k: round(v, 1) for k, v in components.items()}
    else:
        score = None

    # Confidence + components_available (Audit-P1, 2026-08-21)
    n_components = len(components)
    confidence = _confidence_label(n_components)
    # Bei nur 1 Komponente den Score unterdruecken (insufficient-data).
    # Hintergrund: ein einzelner Score-Wert ist methodisch zu schwach fuer
    # eine Zonen-Klassifikation; das wuerde z.B. {'52w': 100} als
    # 'stark-guenstig' labeln, was irrefuehrend ist.
    if n_components < 2:
        score = None

    # Zone
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

    return {
        "score": score,
        "components": components,
        "zone": zone,
        "confidence": confidence,
        "components_available": n_components,
    }


def get_ticker_md(ticker: str) -> tuple[Optional[dict], Optional[str]]:
    path = WISSENSDATENBANK_DIR / f"{ticker}.md"
    if not path.exists():
        return None, f"Eintrag nicht gefunden: {ticker}.md"
    try:
        text = path.read_text(encoding="utf-8")
        kpis = parse_md_kpis(text)
        valuation = score_valuation(kpis)
        # 5-Banker-Score (Hermes-Territorium, read-only)
        banker_scores = _load_banker_scores()
        banker = banker_scores.get(ticker) or {}
        return {
            "ticker": ticker,
            "filename": path.name,
            "raw": text,
            "html": render_markdown_safe(text),
            "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "size_kb": round(path.stat().st_size / 1024, 1),
            "kpis": kpis,
            "valuation": valuation,
            "confidence": valuation.get("confidence", "insufficient"),
            "components_available": valuation.get("components_available", 0),
            "banker_score": banker.get("score_1_10"),
            "banker_score_100": banker.get("score_100"),
            "banker_label": _banker_label(banker.get("score_1_10")),
            "banker_components": {
                "value": banker.get("value"),
                "qualitaet": banker.get("qualitaet"),
                "technik": banker.get("technik"),
                "risiko": banker.get("risiko"),
                "analysten_konsens": banker.get("analysten_konsens"),
                "smart_money": banker.get("smart_money"),
            },
            "banker_recommendation": banker.get("recommendation"),
            "banker_upside_pct": banker.get("upside_pct"),
            "banker_n_analysten": banker.get("n_analysten"),
            "banker_smart_money_institutionen": banker.get("smart_money_institutionen", []),
            "banker_dcf_fair_value": banker.get("dcf_fair_value"),
            "banker_dcf_warnung": banker.get("dcf_warnung"),
            "banker_stand": banker_scores.get("__stand__"),
            "banker_stale": _banker_stale_info(banker_scores.get("__stand__")),
        }, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# TradingView + Marktdaten-Wrapper
# ---------------------------------------------------------------------------
def _get_tv_symbol_for_ticker(ticker: str) -> dict:
    """TradingView EXCHANGE:SYMBOL fuer ticker. None wenn yfinance nichts findet.

    Liefert {symbol, exchange, mode} (mode in {yfinance-resolved, ...}).
    Bei 'all-failed' (kein yfinance-Datensatz) gibt es KEIN Fallback auf den
    bare-ticker -- das Template entscheidet dann zwischen Plotly-Fallback und
    Hinweis-Box.
    """
    state, err = _safe_call(
        "tv_symbol",
        lambda: market_data._get_tv_symbol(ticker),
        default={"symbol": None, "exchange": None, "mode": "all-failed"},
    )
    return state or {"symbol": None, "exchange": None, "mode": "all-failed"}


def _get_chart_state_for_ticker(ticker: str) -> dict:
    """Marktdaten (5Y) fuer den Ticker-Detail-Chart. Fehler werden ins
    state.error geschrieben, das Template rendert dann einen dezenten
    Fallback-Hinweis statt des Charts.
    """
    state, err = _safe_call(
        "market_data_5y",
        lambda: market_data.fetch_market_state(ticker, period="5y"),
        default={},
    )
    if err or not state:
        return {"ok": False, "ticker": ticker, "error": err or "yfinance nicht erreichbar"}
    return state


def _get_chart_state_json_for_ticker(ticker: str) -> str:
    """Wie _get_chart_state_for_ticker, aber als JSON-String fuer JS-Inline.
    Bei Fehler: leerer String + Template-Block entscheidet was rendert.
    """
    state = _get_chart_state_for_ticker(ticker)
    if not state.get("ok"):
        return ""
    return _json_safe(state)


# ---------------------------------------------------------------------------
# News-Helpers (fuer Deep-Dive)
# ---------------------------------------------------------------------------
def _news_count_for_ticker(ticker: str, days: int = 30) -> int:
    """Zaehlt news_gesehen-Eintraege der letzten `days` Tage fuer ticker.
    Die Tabelle speichert nur Hashes, also gibt es keinen Inhalt -- wir
    koennen aber die Frequenz als Indikator fuer 'ist etwas los' anzeigen.
    """
    try:
        from lib import db
        c = db._conn()
        try:
            lo = (date.today() - timedelta(days=days)).isoformat()
            row = c.execute(
                "SELECT COUNT(*) AS n FROM news_gesehen WHERE ticker=? AND datum>=?",
                (ticker, lo),
            ).fetchone()
            return int(row["n"]) if row else 0
        finally:
            c.close()
    except Exception:
        return 0


def _google_news_url(ticker: str) -> str:
    """Externer Google-News-Link (Deep-Dive-Nutzer kann selber suchen)."""
    return f"https://news.google.com/search?q={quote_plus(ticker)}&hl=de"


# ---------------------------------------------------------------------------
# JSON-Safe
# ---------------------------------------------------------------------------
def _json_safe(obj):
    """Serialisiert ein Dict (mit NaN/None) sauber zu JSON-String fuer JS."""
    def walk(v):
        if v is None:
            return None
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                return None
            return v
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    return json.dumps(walk(obj), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Hermes-Subprocess-Wrapper (Admin-Konsole)
# ---------------------------------------------------------------------------
def _build_wiki_ticker_file() -> Optional[Path]:
    """Schreibt alle Wiki-Ticker (ausser README) in einen Temp-File und
    gibt den Pfad zurueck. Format: ein Ticker pro Zeile.

    Der Temp-File wird vom Hermes-Skript gelesen (--ticker-file <pfad>).
    Aufraeumung: passiert im Background-Thread nach Subprocess-Ende.
    """
    if not WISSENSDATENBANK_DIR.exists():
        return None
    tickers = []
    for p in sorted(WISSENSDATENBANK_DIR.glob("*.md")):
        if p.stem.upper() == "README":
            continue
        tickers.append(p.stem)
    if not tickers:
        return None
    fd, path_str = tempfile.mkstemp(prefix="trigger_check_wiki_", suffix=".txt", text=True)
    path = Path(path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for t in tickers:
                f.write(t + "\n")
    except Exception:
        if path.exists():
            path.unlink()
        raise
    return path


def _hermes_thread_target(job_id: str, args: list, ticker_file: Optional[Path]) -> None:
    """Background-Thread-Target: startet Hermes-Subprocess, schreibt State.

    ticker_file (falls mitgegeben) wird NACH Subprocess-Ende geloescht,
    egal ob erfolgreich oder mit Fehler.
    """
    cfg = HERMES_JOBS.get(job_id, {})
    state = _JOB_STATE.setdefault(job_id, {})
    state.update({
        "state": "running",
        "started_at": time.time(),
        "finished_at": None,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "duration_s": None,
        "label": cfg.get("label", job_id),
        "ticker_count": sum(1 for _ in ticker_file.read_text(encoding="utf-8").splitlines() if _.strip()) if ticker_file else None,
    })

    cmd = [str(HERMES_PYTHON), str(PROJECT_ROOT / "scripts" / cfg["script"]), *args]
    started = datetime.now()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=int(cfg.get("timeout", 120)),
        )
        duration = (datetime.now() - started).total_seconds()
        state.update({
            "state": "done" if proc.returncode == 0 else "error",
            "finished_at": time.time(),
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "duration_s": round(duration, 1),
        })
    except subprocess.TimeoutExpired as exc:
        duration = (datetime.now() - started).total_seconds()
        state.update({
            "state": "error",
            "finished_at": time.time(),
            "returncode": -1,
            "stdout": (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            "stderr": f"Timeout nach {cfg.get('timeout', 120)}s",
            "duration_s": round(duration, 1),
        })
    except Exception as exc:
        state.update({
            "state": "error",
            "finished_at": time.time(),
            "returncode": -1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "duration_s": 0.0,
        })
    finally:
        if ticker_file and ticker_file.exists():
            try:
                ticker_file.unlink()
            except OSError:
                pass


def start_hermes_job_async(job_id: str) -> dict:
    """Startet einen Hermes-Job in einem Background-Thread.

    Spezialfall: Jobs mit "wiki_ticker_file": True bekommen einen
    dynamisch gebauten Ticker-File als Argument.
    Returns: dict mit "started" (bool), "reason" (str) bei Fehler.
    """
    cfg = HERMES_JOBS.get(job_id)
    if not cfg:
        return {"started": False, "reason": f"Unbekannter Job: {job_id}"}
    # Concurrency-Guard: ein laufender Job blockt weitere Starts
    cur = _JOB_STATE.get(job_id, {})
    if cur.get("state") == "running":
        return {"started": False, "reason": f"Job laeuft bereits seit {int(time.time() - cur.get('started_at', time.time()))}s"}

    args = list(cfg.get("args", []))
    ticker_file = None
    if cfg.get("wiki_ticker_file"):
        ticker_file = _build_wiki_ticker_file()
        if not ticker_file:
            return {"started": False, "reason": "Wiki-Ordner leer oder nicht erreichbar"}
        args = args + ["--ticker-file", str(ticker_file)]

    t = threading.Thread(
        target=_hermes_thread_target,
        args=(job_id, args, ticker_file),
        daemon=True,
        name=f"hermes-{job_id}",
    )
    t.start()
    return {"started": True, "thread": t.name, "ticker_file": str(ticker_file) if ticker_file else None}


def _prune_job_state() -> None:
    """Entfernt Eintraege aelter als _JOB_STATE_MAX_AGE aus dem State-Dict.
    Wird bei jedem admin_status-Call aufgerufen (kein dedizierter Cron noetig)."""
    now = time.time()
    for jid in list(_JOB_STATE.keys()):
        st = _JOB_STATE[jid]
        finished = st.get("finished_at")
        if finished and (now - finished) > _JOB_STATE_MAX_AGE:
            del _JOB_STATE[jid]


def _run_hermes_script(job_id: str) -> dict:
    """Startet ein Hermes-Skript als Subprocess und liefert Ergebnis-Dict.

    Wird von /admin/run/<job_id> aufgerufen. Schreibt NICHTS selbst -- nur
    subprocess-Wrapper. Rueckgabe:
      {"ok": bool, "returncode": int, "stdout": str, "stderr": str, "duration_s": float}
    """
    cfg = HERMES_JOBS.get(job_id)
    if not cfg:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"Unbekannter Job: {job_id}", "duration_s": 0.0}
    script = PROJECT_ROOT / "scripts" / cfg["script"]
    if not script.exists():
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"Skript fehlt: {script}", "duration_s": 0.0}
    if not HERMES_PYTHON.exists():
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"Hermes-Python fehlt: {HERMES_PYTHON}", "duration_s": 0.0}

    cmd = [str(HERMES_PYTHON), str(script), *cfg["args"]]
    started = datetime.now()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=int(cfg.get("timeout", 120)),
        )
        duration = (datetime.now() - started).total_seconds()
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "duration_s": round(duration, 1),
        }
    except subprocess.TimeoutExpired as exc:
        duration = (datetime.now() - started).total_seconds()
        return {"ok": False, "returncode": -1, "stdout": (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                "stderr": f"Timeout nach {cfg.get('timeout', 120)}s", "duration_s": round(duration, 1)}
    except Exception as exc:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "duration_s": 0.0}


# ---------------------------------------------------------------------------
# Jinja-Template-Filter (Pure-Format, kein Flask-Context noetig)
# ---------------------------------------------------------------------------
# Werden in app.py via app.add_template_filter() registriert. Hier definiert
# als Pure-Funktionen, damit sie in Tests direkt importierbar sind.
def eur(value):
    if value is None:
        return "\u2014"
    try:
        return f"{float(value):,.2f} \u20ac".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(value)


def usd(value):
    if value is None:
        return "\u2014"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def pct(value, digits=1):
    if value is None:
        return "\u2014"
    try:
        return f"{float(value):.{digits}f} %"
    except (TypeError, ValueError):
        return str(value)


def de_number(value, digits=2):
    if value is None:
        return "\u2014"
    try:
        s = f"{float(value):,.{digits}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(value)


def g(value, digits=2):
    if value is None:
        return "\u2014"
    try:
        v = float(value)
        if abs(v) >= 1e9:
            return f"{v/1e9:.{digits}f} Bio."
        if abs(v) >= 1e6:
            return f"{v/1e6:.{digits}f} Mio."
        if abs(v) >= 1e3:
            return f"{v/1e3:.{digits}f} Tsd."
        return f"{v:.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def dt(value):
    if not value:
        return "\u2014"
    try:
        s = str(value)
        if "T" in s:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return d.strftime("%Y-%m-%d %H:%M")
        return s
    except (TypeError, ValueError):
        return str(value)


def bio_usd(value):
    if value is None:
        return "\u2014"
    try:
        v = float(value)
        if v >= 1:
            return f"{v:,.2f} Bio. $".replace(",", "X").replace(".", ",").replace("X", ".")
        if v >= 0.001:
            return f"{v * 1000:,.0f} Mrd. $".replace(",", "X").replace(".", ",").replace("X", ".")
        if v >= 0.000001:
            return f"{v * 1_000_000:,.0f} Mio. $".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{v:,.4f} $"
    except (TypeError, ValueError):
        return str(value)


def signed_pct(value, digits=1):
    if value is None:
        return ("\u2014", "muted")
    try:
        v = float(value)
        if v > 0:
            return (f"+{v:.{digits}f} %".replace(".", ","), "pos")
        if v < 0:
            return (f"{v:.{digits}f} %".replace(".", ","), "neg")
        return (f"{v:.{digits}f} %".replace(".", ","), "muted")
    except (TypeError, ValueError):
        return (str(value), "muted")


def zone_label(zone):
    return {
        "stark-ueberteuert": "stark \u00fcberteuert",
        "teuer": "teuer",
        "fair": "fair bewertet",
        "guenstig": "g\u00fcnstig",
        "stark-guenstig": "stark g\u00fcnstig",
        "no-data": "keine Daten",
    }.get(zone, zone or "\u2014")

