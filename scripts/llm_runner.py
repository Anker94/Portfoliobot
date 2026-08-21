"""llm_runner.py — Prompt-Ausfuehrung + LLM-Aufruf (P1-P5, P7).

Laedt Prompt-Templates, haengt Guardrails an, injiziert Code-berechnete Daten,
ruft das (free) LLM, parst JSON, haengt P3-Postprocessing an (E4/E5/E6).

Einstieg: python scripts/llm_runner.py <p1|p2|p3|p4|p5|p7> [ticker]
"""
import os
import sys
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import PROMPTS_DIR, load_config
from llm import call_llm
from json_parse import parse_llm_json
from db import (get_active_positions, get_position, get_last_recommendation,
                add_recommendation, get_recommendations)
from data_layer import get_stock_data, get_performance
from macro_drivers import build_driver_context
from fear_greed import fear_greed_index
from regime import regime_vector, describe_regime
from correlation import monthly_matrix_for_p5
from ev_sizing import ev_and_kelly
from devils_advocate import run_devils_advocate
from attribution import summarize_for_p7
from telegram import send_telegram

PROMPT_FILES = {
    "p1": "P1_news_triage.txt", "p2": "P2_morgen_sentiment.txt",
    "p3": "P3_einzelaktien_empfehlung.txt", "p4": "P4_pre_event.txt",
    "p5": "P5_portfolio_tiefenanalyse.txt", "p7": "P7_lern_loop.txt",
}


def load_prompt(name):
    path = os.path.join(PROMPTS_DIR, PROMPT_FILES[name])
    with open(path, encoding="utf-8") as f:
        base = f.read()
    # Guardrails anhängen (SKILL.md: load_prompt haengt automatisch an)
    gp = os.path.join(PROMPTS_DIR, "guardrails.txt")
    if os.path.exists(gp):
        with open(gp, encoding="utf-8") as f:
            base += "\n\n" + f.read()
    return base


def _fill(template, **kw):
    for k, v in kw.items():
        token = "{" + k + "}"
        if token in template:
            template = template.replace(token, str(v))
    return template


# --------------------------------------------------------------------------
# Einzelne Tasks
# --------------------------------------------------------------------------
def run_p3(ticker):
    pos = get_position(ticker)
    if not pos:
        return f"Position {ticker} nicht gefunden."
    these = pos.get("these") or "(keine These hinterlegt)"
    sd = get_stock_data(ticker)
    stock_block = json.dumps(sd, ensure_ascii=False, indent=2) if sd else "keine Daten"
    driver_block = build_driver_context(ticker)
    last = get_last_recommendation(ticker)
    last_rec_block = (f"{last['typ']} / {last['confidence']}"
                      if last else "keine Vor-Empfehlung")
    regime_block = describe_regime(regime_vector())
    # E3 Short-Interest Kontext (lazy import vermeidet yfinance-Hammer bei Tests)
    try:
        from short_interest import build_e3_context
        short_block = build_e3_context(ticker)
    except Exception:
        short_block = "n/a"

    tmpl = load_prompt("p3")
    prompt = _fill(tmpl, ticker=ticker, these=these, stock_block=stock_block,
                   driver_block=driver_block, last_rec_block=last_rec_block,
                   regime_block=regime_block, short_block=short_block)
    raw = call_llm("P3", prompt, temperature=0.2, json_mode=True)
    data = parse_llm_json(raw)
    if not isinstance(data, dict):
        return f"P3 Parser-Fehler fuer {ticker}"
    return _postprocess_p3(ticker, these, prompt, data)


def _postprocess_p3(ticker, these, data_block_for_advocate, data):
    """Speichert Empfehlung + E5 EV-Sizing + E6 Treiber + E4 Dissens."""
    typ = data.get("typ", "HALTEN")
    conf = data.get("confidence", "niedrig")
    szen = data.get("szenarien", [])
    extra = {}
    # E5 EV-Sizing
    spot = None
    sd = get_stock_data(ticker)
    if sd:
        spot = sd["spot"]
    if szen and spot:
        ev, size = ev_and_kelly(szen, spot)
        extra["ev_berechnet"] = ev
        extra["kelly_groesse"] = size
        extra["szenarien_json"] = json.dumps(szen, ensure_ascii=False)
    # E6 erwarteter Treiber
    et = data.get("erwarteter_treiber")
    if et:
        extra["erwarteter_treiber"] = json.dumps(et, ensure_ascii=False)
    rec_id = add_recommendation(ticker, typ, conf, data.get("begruendung", ""),
                                prompt_version="P3_v3", **extra)
    # E4 Devil's Advocate nur bei KAUFEN/AUFSTOCKEN
    msg_parts = [f"P3 {ticker}: {typ} ({conf})"]
    if szen and spot:
        ev = extra.get("ev_berechnet"); size = extra.get("kelly_groesse")
        if ev is not None:
            msg_parts.append(f"EV +{ev}%/12m, Größe {size}% (¼-Kelly)")
    if typ in ("KAUFEN", "AUFSTOCKEN"):
        dissens, aj = run_devils_advocate(ticker, these, data_block_for_advocate,
                                          rec_id=rec_id)
        if dissens:
            msg_parts.append("⚠️ Dissens-Alert (siehe Telegram)")
    send_telegram("\n".join(msg_parts))
    return "\n".join(msg_parts)


def run_p5():
    pos = get_active_positions()
    positions_block = json.dumps(pos, ensure_ascii=False, indent=2)
    corr_text, _ = monthly_matrix_for_p5()
    # Outcome-Block (vereinfacht)
    recs = get_recommendations(limit=200)
    outcomes = [r for r in recs if r.get("outcome_checked")]
    outcome_block = f"{len(outcomes)} Empfehlungen mit Outcome"
    regime_block = describe_regime(regime_vector())
    regret_line, _ = summarize_for_p7()
    tmpl = load_prompt("p5")
    prompt = _fill(tmpl, corr_block=corr_text, positions_block=positions_block,
                   outcome_block=outcome_block, regime_block=regime_block,
                   regret_block=regret_line)
    raw = call_llm("P5", prompt, temperature=0.3, json_mode=False)
    send_telegram("P5 Portfolio-Tiefenanalyse:\n" + raw)
    return raw


def run_p7():
    line, counts = summarize_for_p7()
    tmpl = load_prompt("p7")
    prompt = _fill(tmpl, )  # keine Token im Template ausser Befunden via text
    # Befunde manuell voranstellen
    prompt = line + "\n\n" + prompt
    raw = call_llm("P7", prompt, temperature=0.3, json_mode=True)
    send_telegram("P7 Lern-Loop:\n" + raw)
    return raw


def run_p1():
    # Vereinfacht: holt News der aktiven Positionen, dedup, triage
    from news import fetch_news
    from dedup import dedup_news
    from data_layer import get_news_yfinance
    items = []
    for p in get_active_positions():
        t = p["ticker"]
        raw = fetch_news(t, get_news_yfinance(t))
        new = dedup_news(t, [r["headline"] for r in raw])
        items.extend(new)
    tmpl = load_prompt("p1")
    prompt = _fill(tmpl, news_block=json.dumps(items, ensure_ascii=False))
    raw = call_llm("P1", prompt, temperature=0.1, json_mode=True)
    send_telegram("P1 Triage:\n" + raw)
    return raw


def run_p2():
    fg = fear_greed_index()
    market_block = json.dumps(fg, ensure_ascii=False) if fg else "keine Daten"
    # Sektor-Momentum aus regime
    rv = regime_vector()
    market_block += "\nSektoren Top: " + ",".join(rv["sektor_rotation"]["top3"])
    tmpl = load_prompt("p2")
    prompt = _fill(tmpl, market_block=market_block)
    raw = call_llm("P2", prompt, temperature=0.2, json_mode=False)
    send_telegram("P2 Morgen-Sentiment:\n" + raw)
    return raw


def run_p4(ticker, event_datum="", bmo_amc="", post=False):
    """P4 Pre/Post-Event. Injiziert E2 (Implied Move) + E3 (Short) + Actual-Move."""
    from implied_move import build_e2_context
    from short_interest import build_e3_context
    from data_layer import get_stock_data, get_performance
    sd = get_stock_data(ticker)
    stock_block = json.dumps(sd, ensure_ascii=False, indent=2) if sd else "keine Daten"
    e2 = build_e2_context(ticker)
    e3 = build_e3_context(ticker)
    these = get_position(ticker)
    these = these.get("these") if these else ""
    if post:
        now = get_performance(ticker)
        base = get_position(ticker)
        base_k = base.get("kauf_kurs") if base else None
        actual = (f"Kurs jetzt {now}, Basis {base_k}" if now else "n/a")
        tmpl = load_prompt("p4").replace("P4_pre_event", "P4_post_event")
        # Post-Event Template nutzen
        tmpl = open(os.path.join(PROMPTS_DIR, "P4_post_event.txt"), encoding="utf-8").read()
        prompt = _fill(tmpl, ticker=ticker, event_datum=event_datum,
                       bmo_amc=bmo_amc, actual_move_block=actual,
                       stock_block=stock_block, driver_block=build_driver_context(ticker),
                       exit_kriterien=these or "(keine)")
    else:
        tmpl = load_prompt("p4")
        prompt = _fill(tmpl, ticker=ticker, event_datum=event_datum,
                       bmo_amc=bmo_amc, implied_block=e2, hist_block=e2,
                       stock_block=stock_block, short_block=e3,
                       driver_block=build_driver_context(ticker))
    raw = call_llm("P4", prompt, temperature=0.2, json_mode=True)
    send_telegram(f"P4 {ticker}:\n" + raw)
    return raw


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=list(PROMPT_FILES.keys()) + ["p3spek"])
    ap.add_argument("ticker", nargs="?")
    args = ap.parse_args()
    if args.task in ("p3",) and args.ticker:
        print(run_p3(args.ticker))
    elif args.task == "p5":
        print(run_p5())
    elif args.task == "p7":
        print(run_p7())
    elif args.task == "p1":
        print(run_p1())
    elif args.task == "p2":
        print(run_p2())
    elif args.task == "p4" and args.ticker:
        print(run_p4(args.ticker))
    else:
        print("Usage: llm_runner.py <p1|p2|p3|p5|p7> [ticker]")


if __name__ == "__main__":
    main()
