"""Portfoliobot Dashboard -- Flask-App + Routes.

C2-Refactor (2026-08-21): Alle Pure-Helper, Data-Access-Wrapper und
Hermes-Subprocess-Wrapper sind nach lib/portfolio_helpers.py ausgelagert.
Hier bleibt nur Flask-App-Setup, Jinja-Filter-Registrierung, der
Context-Processor und die Routen.

Manuell starten mit:
    python dashboard/app.py
    -> http://127.0.0.1:5000/

Liest ausschliesslich aus existierenden Datenquellen:
- SQLite via lib.db (positionen, empfehlungen, broker_konto, paper_bestand, orders)
- data/trigger_log.json
- data/sparplan-tracking*.json
- wissensdatenbank/aktien/*.md
- data/banker_council_latest.json (5-Banker-Score, read-only)

Schreibt NICHTS. Trigger-Jobs werden ueber /admin/run/<job_id> angestossen
und laufen als Subprozess mit den jeweiligen Skripten aus scripts/.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# Pfad-Aufloesung: dashboard/ -> Portfoliobot/ (Projekt-Root)
DASHBOARD_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DASHBOARD_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from flask import Flask, Response, abort, flash, get_flashed_messages, redirect, render_template, request, url_for  # noqa: E402

# Helper-Modul: alle Pure-Helper, Data-Access, Hermes-Subprocess-Wrapper
# und Jinja-Filter-Definitionen. Kein Flask-Import in portfolio_helpers.
from lib import portfolio_helpers as h  # noqa: E402
from lib import market_data  # noqa: E402  (fuer /healthz cache_stats)
from lib.portfolio_helpers import (  # noqa: E402, F401
    # Konstanten / Modul-State
    HERMES_PYTHON, HERMES_JOBS, _JOB_STATE, _JOB_STATE_MAX_AGE,
    # Data-Layer
    _safe_call, _read_json, get_db_state, compute_saeulen_verteilung,
    get_sparplan_state, get_trigger_state, get_wissensdatenbank_state,
    get_ticker_md, render_markdown_safe,
    # Parser / Score
    parse_company_name, parse_md_kpis, score_valuation, _confidence_label,
    # Banker
    _load_banker_scores, _banker_label, _banker_zone_class,
    # TV / Chart / News
    _get_tv_symbol_for_ticker, _get_chart_state_for_ticker,
    _get_chart_state_json_for_ticker,
    _news_count_for_ticker, _google_news_url,
    # Hermes-Subprocess
    _build_wiki_ticker_file, _hermes_thread_target, start_hermes_job_async,
    _prune_job_state, _run_hermes_script,
    # JSON-Safe
    _json_safe,
)

# ---------------------------------------------------------------------------
# Flask-App
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_SORT_KEYS"] = False
# Templates bei jedem Request neu von Disk lesen (sonst Cache-Probleme bei
# lokalem Dev-Server ohne debug=True).
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
# Lokale Statusmeldungen via Flash; kein Remote-Betrieb, festes Secret reicht.
app.secret_key = "portfoliobot-local-only-refresh-status"

# Jinja-Filter registrieren (Pure-Format-Funktionen aus portfolio_helpers).
# Vorher @app.template_filter-Dekorator in app.py, jetzt via add_template_filter
# weil die Funktionen in einem anderen Modul definiert sind.
app.add_template_filter(h.eur, "eur")
app.add_template_filter(h.usd, "usd")
app.add_template_filter(h.pct, "pct")
app.add_template_filter(h.de_number, "de_number")
app.add_template_filter(h.g, "g")
app.add_template_filter(h.dt, "dt")
app.add_template_filter(h.bio_usd, "bio_usd")
app.add_template_filter(h.signed_pct, "signed_pct")
app.add_template_filter(h.zone_label, "zone_label")
app.add_template_filter(h._banker_zone_class, "banker_zone_class")


# ---------------------------------------------------------------------------
# Context-Processor + Flash
# ---------------------------------------------------------------------------
@app.context_processor
def inject_nav():
    return {
        "nav_items": [
            ("uebersicht", "Übersicht"),
            ("positionen", "Positionen"),
            ("sparplan", "Sparplan"),
            ("wissensdatenbank_index", "Wissensdatenbank"),
            ("trigger_log", "Trigger-Log"),
            ("admin", "Admin"),
        ],
        "data_stand": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "flashes": get_flashed_messages(with_categories=True),
    }


# ---------------------------------------------------------------------------
# Routen (alle read-only)
# ---------------------------------------------------------------------------
@app.route("/")
def uebersicht():
    db_state = get_db_state()
    trigger_state = get_trigger_state()

    # Kennzahlen
    pos = db_state["positionen"]
    paper = db_state["paper_bestand"]
    konto = db_state["konto"]
    last_triggers = trigger_state["entries"][:5]

    return render_template(
        "uebersicht.html",
        page_id="uebersicht",
        n_active=len(pos),
        n_paper=len(paper),
        konto=konto,
        last_triggers=last_triggers,
        errors=db_state["errors"] + trigger_state["errors"],
    )


@app.route("/positionen")
def positionen():
    db_state = get_db_state()
    return render_template(
        "positionen.html",
        page_id="positionen",
        positionen=db_state["positionen"],
        letzte_empfehlung=db_state["letzte_empfehlung"],
        paper_bestand=db_state["paper_bestand"],
        konto=db_state["konto"],
        orders=db_state["orders"][:20],
        errors=db_state["errors"],
    )


@app.route("/sparplan")
def sparplan():
    state = get_sparplan_state()
    return render_template(
        "sparplan.html",
        page_id="sparplan",
        tracks=state["tracks"],
        saeulen_map=state["saeulen"],
        errors=state["errors"],
    )


@app.route("/wissensdatenbank")
def wissensdatenbank_index():
    # Query-Params lesen
    view = request.args.get("view", "cards")
    if view not in ("cards", "list"):
        view = "cards"
    sort = request.args.get("sort", "score")
    if sort not in ("score", "banker", "ticker", "company", "size", "date"):
        sort = "score"
    direction = request.args.get("dir", "desc")
    if direction not in ("asc", "desc"):
        direction = "desc"
    zones_raw = request.args.get("zone", "")
    zones = {z for z in zones_raw.split(",") if z in
             {"stark-guenstig", "guenstig", "fair", "teuer", "stark-ueberteuert", "no-data"}}
    query = request.args.get("q", "").strip()
    try:
        min_size = float(request.args["min_size"]) if request.args.get("min_size") else None
    except (ValueError, KeyError):
        min_size = None
    try:
        max_size = float(request.args["max_size"]) if request.args.get("max_size") else None
    except (ValueError, KeyError):
        max_size = None
    min_confidence = request.args.get("min_confidence", "insufficient")
    if min_confidence not in ("insufficient", "low", "medium", "high"):
        min_confidence = "insufficient"
    state = get_wissensdatenbank_state(
        view=view, sort=sort, direction=direction,
        zones=zones, query=query, min_size=min_size, max_size=max_size,
        min_confidence=min_confidence,
    )
    return render_template(
        "wissensdatenbank.html",
        page_id="wissensdatenbank",
        ticker_files=state["ticker_files"],
        view=state["view"],
        sort=state["sort"],
        direction=state["direction"],
        active_zones=state["active_zones"],
        all_zones=state["all_zones"],
        query=state["query"],
        min_size=state["min_size"],
        max_size=state["max_size"],
        min_confidence=state["min_confidence"],
        banker_stand=state.get("banker_stand"),
        banker_stale=state.get("banker_stale"),
        errors=state["errors"],
    )


@app.route("/wissensdatenbank/<ticker>")
def wissensdatenbank_ticker(ticker: str):
    ticker = ticker.upper()
    data, err = get_ticker_md(ticker)
    if data is None:
        # Beim 404 die gleichen Filter beibehalten wie auf der Liste
        state = get_wissensdatenbank_state()
        return render_template(
            "wissensdatenbank.html",
            page_id="wissensdatenbank",
            ticker_files=state["ticker_files"],
            view=state["view"],
            sort=state["sort"],
            direction=state["direction"],
            active_zones=state["active_zones"],
            all_zones=state["all_zones"],
            query=state["query"],
            min_size=state["min_size"],
            max_size=state["max_size"],
            min_confidence=state["min_confidence"],
            banker_stand=state.get("banker_stand"),
            banker_stale=state.get("banker_stale"),
            errors=[err] if err else [],
            missing_ticker=ticker,
        ), 404
    return render_template(
        "ticker_detail.html",
        page_id="wissensdatenbank",
        ticker=data,
        chart_state=_get_chart_state_for_ticker(ticker),
        chart_state_json=_get_chart_state_json_for_ticker(ticker),
        tv_symbol=_get_tv_symbol_for_ticker(ticker),
    )


@app.route("/trigger-log")
def trigger_log():
    state = get_trigger_state()
    page_size = 50
    entries = state["entries"][:page_size]
    return render_template(
        "trigger_log.html",
        page_id="trigger-log",
        entries=entries,
        total=len(state["entries"]),
        page_size=page_size,
        errors=state["errors"],
    )


# ---------------------------------------------------------------------------
# Deep-Dive-View: yfinance live + Indikatoren + News + Wiki-Kontext
# ---------------------------------------------------------------------------
@app.route("/deepdive/<ticker>")
def deepdive(ticker: str):
    """Deep-Dive-View fuer einen Ticker. Akzeptiert beliebige Ticker (read-only).

    Datenquellen:
    - yfinance live on-demand (lib/market_data.fetch_market_state)
    - news_gesehen-Tabelle (nur Count, kein Inhalt)
    - Wissensdatenbank (wissensdatenbank/<TICKER>.md) als Wiki-Kontext
    """
    ticker = ticker.upper().strip()
    if not ticker:
        abort(404)

    # Marktdaten
    state, err = _safe_call("market_data", lambda: h.market_data.fetch_market_state(ticker), default={})
    if err:
        state = {"ok": False, "ticker": ticker, "error": err, "summary": {}, "indicators": {}, "ohlc": {}, "dates": []}

    # Wiki-Kontext (optional)
    wiki, wiki_err = get_ticker_md(ticker)

    # News-Count
    news_n = _news_count_for_ticker(ticker, days=30)

    return render_template(
        "deepdive.html",
        page_id="deepdive",
        ticker=ticker,
        state=state,
        wiki=wiki,
        wiki_err=wiki_err,
        news_count=news_n,
        news_url=_google_news_url(ticker),
        market_state_json=_json_safe(state),
    )


@app.route("/deepdive/<ticker>/export.md")
def deepdive_export(ticker: str):
    """Markdown-Export der Deep-Dive-Analyse als text/markdown-Response."""
    ticker = ticker.upper().strip()
    if not ticker:
        abort(404)
    state, _ = _safe_call("market_data", lambda: h.market_data.fetch_market_state(ticker), default={})
    if not state:
        state = {"ok": False, "ticker": ticker, "error": "yfinance nicht erreichbar"}
    md_text = h.market_data.market_state_to_markdown(state, ticker_label=ticker)
    filename = f"deepdive_{ticker}_{datetime.now().strftime('%Y-%m-%d')}.md"
    return Response(
        md_text,
        mimetype="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Admin: Manuelle Trigger fuer Hermes-Skripte
# ---------------------------------------------------------------------------
@app.route("/admin")
def admin():
    """Admin-Konsole: listet alle Hermes-Jobs + Status."""
    return render_template(
        "admin.html",
        page_id="admin",
        jobs=HERMES_JOBS,
        job_states=_JOB_STATE,
    )


@app.route("/admin/run/<job_id>", methods=["POST"])
def admin_run(job_id: str):
    """Startet einen Hermes-Job und rendert die Admin-Seite mit Ergebnis.

    Sync-Jobs (<timeout 5 Min): klassischer Subprocess-Block, Output im
    Flash-Banner. Async-Jobs (cfg["async"]=True, z.B. trigger_check_wiki):
    starten in einem Background-Thread, Status wird via /admin/status/<id>
    polliert. Sync-Variante bleibt fuer die kleinen Jobs, weil der Output
    dort direkt lesbar ist.
    """
    cfg = HERMES_JOBS.get(job_id, {})
    if not cfg:
        flash(f"Unbekannter Job: {job_id}", "error")
        return redirect(url_for("admin"))

    if cfg.get("async"):
        result = start_hermes_job_async(job_id)
        if result["started"]:
            label = cfg.get("label", job_id)
            tf = result.get("ticker_file") or "n/a"
            flash(
                f"{label} gestartet (Background-Thread, Ticker-File: {Path(tf).name}). "
                f"Status pollt automatisch alle 2s -- nicht neu laden.",
                "ok",
            )
        else:
            flash(f"{cfg.get('label', job_id)} konnte nicht starten: {result.get('reason', '?')}", "error")
        return redirect(url_for("admin"))

    # Sync-Variante (alte Logik, beibehalten fuer die kleinen Jobs)
    result = _run_hermes_script(job_id)
    label = cfg.get("label", job_id)
    status_line = f"{label} (rc={result['returncode']}, {result['duration_s']}s)"
    detail_parts = []
    if result["stdout"]:
        detail_parts.append(f"stdout: {result['stdout'][:400]}")
    if result["stderr"]:
        detail_parts.append(f"stderr: {result['stderr'][:400]}")
    detail = "\n".join(detail_parts)
    flash(status_line + "\n" + detail, "ok" if result["ok"] else "error")
    return redirect(url_for("admin"))


@app.route("/admin/status/<job_id>")
def admin_status(job_id: str):
    """JSON-Endpoint fuer Live-Status-Polling (Async-Jobs).

    Response: {"job_id": str, "state": str, "started_at": float|None,
               "finished_at": float|None, "duration_s": float|None,
               "returncode": int|None, "stdout_tail": str, "stderr_tail": str,
               "ticker_count": int|None, "label": str}
    """
    _prune_job_state()
    st = _JOB_STATE.get(job_id)
    if not st:
        return {"job_id": job_id, "state": "idle"}
    now = time.time()
    out = {
        "job_id": job_id,
        "state": st.get("state", "idle"),
        "label": st.get("label", job_id),
        "started_at": st.get("started_at"),
        "finished_at": st.get("finished_at"),
        "duration_s": st.get("duration_s"),
        "returncode": st.get("returncode"),
        "ticker_count": st.get("ticker_count"),
    }
    if st.get("state") == "running":
        # Laufzeit-Fortschritt
        out["elapsed_s"] = round(now - st["started_at"], 1) if st.get("started_at") else None
        # stdout/stderr auf letzte 800 Zeichen kuerzen (verhindert Riesen-Responses)
        stdout = st.get("stdout", "")
        stderr = st.get("stderr", "")
        out["stdout_tail"] = stdout[-800:] if stdout else ""
        out["stderr_tail"] = stderr[-800:] if stderr else ""
    else:
        # done/error: vollstaendiger Output (max 2000 Zeichen pro Stream)
        stdout = st.get("stdout", "")
        stderr = st.get("stderr", "")
        out["stdout_tail"] = stdout[-2000:] if stdout else ""
        out["stderr_tail"] = stderr[-2000:] if stderr else ""
    return out


# Rueckwaertskompatibel: alte /valuation/refresh-Route war ein eigener Button,
# jetzt nur noch ein Wrapper auf den Admin-Job "valuation_refresh".
@app.route("/valuation/refresh", methods=["POST"])
def valuation_refresh():
    """Legacy-Wrapper: loest valuation_refresh-Job aus und redirected zu /admin."""
    return admin_run("valuation_refresh")


# Alias in die andere Richtung: /prices/refresh macht klar, dass der Job
# Preise UND Bewertung zusammen aktualisiert (Score liest aus LIVE-DATA-Block).
# Beide Wrapper rufen denselben Job -- es gibt nur eine Datenquelle.
@app.route("/prices/refresh", methods=["POST"])
def prices_refresh():
    """Alias auf valuation_refresh-Job. Klient fuer Lesefreundlichkeit: zeigt
    deutlich, dass Preise + Bewertung in einem Schritt aktualisiert werden."""
    return admin_run("valuation_refresh")


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------
@app.route("/healthz")
def healthz():
    cache_info = market_data.cache_stats() if hasattr(market_data, "cache_stats") else {}
    return {
        "ok": True,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "yfinance_cache": cache_info,
    }


# ---------------------------------------------------------------------------
# Manuelle Start-Route
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Portfoliobot Dashboard — http://127.0.0.1:5000/")
    print("Ctrl+C zum Beenden.")
    app.run(host="127.0.0.1", port=5000, debug=False)
