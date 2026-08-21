"""stock_commands.py — Telegram-Befehls-Parser.

Befehle:
  /add NVDA 120.50 10 Langfrist-Compounder   -> Position anlegen (+ E1 Check)
  /these NVDA <Text>                          -> Investment-These
  /remove NVDA                                -> Position schliessen (E7: exit_kurs/datum)
  /check NVDA                                 -> Ad-hoc Kurse + Indikatoren (kein LLM)
  /portfolio                                  -> Uebersicht aktiver Positionen

E1: Bei /add wird Korrelation zur bestehenden Position geprueft -> Warnung.
"""
import os
import sys
import argparse
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import konsole_utf8
konsole_utf8()

from db import (add_position, get_position, get_active_positions,
                close_position, update_position)
from data_layer import get_stock_data, get_info
from correlation import add_correlation_check
from telegram import send_telegram


def cmd_add(args):
    # args: NVDA 120.50 10 These...
    if len(args) < 3:
        return "Usage: /add <TICKER> <KAUF_KURS> <STUECK> [These]"
    ticker = args[0].upper()
    try:
        kurs = float(args[1]); stueck = float(args[2])
    except ValueError:
        return "Kurs/Stueck muessen Zahlen sein."
    these = " ".join(args[3:]) if len(args) > 3 else ""
    # Name via yfinance
    name = ticker
    info = get_info(ticker)
    if info.get("shortName"):
        name = info["shortName"]
    # E1 Korrelations-Check gegen bestehende (vor dem Einfuegen pruefen)
    existing = [p["ticker"] for p in get_active_positions() if p["ticker"] != ticker]
    warn = None
    if existing:
        try:
            max_c, warn = add_correlation_check(ticker, existing)
        except Exception:
            warn = None
    # Bestehende aktive Position desselben Tickers -> aufstocken (kein Duplikat)
    cur = get_position(ticker)
    if cur:
        new_stueck = (cur.get("stueckzahl") or 0) + stueck
        # gewichteter neuer Einstandskurs
        old_val = (cur.get("stueckzahl") or 0) * (cur.get("kauf_kurs") or 0)
        new_val = old_val + stueck * kurs
        new_kurs = new_val / new_stueck if new_stueck else kurs
        update_position(ticker, stueckzahl=new_stueck, kauf_kurs=new_kurs)
        msg = (f"🔼 {ticker} aufgestockt -> {new_stueck:.0f} Stk "
               f"(@ Ø {new_kurs:.2f})")
    else:
        add_position(ticker, name, kurs, stueck, these)
        msg = f"✅ {ticker} ({name}) angelegt @ {kurs} x {stueck}."
    if warn:
        msg += f"\n{warn}"
    send_telegram(msg)
    return msg


def cmd_these(args):
    if len(args) < 2:
        return "Usage: /these <TICKER> <Text>"
    ticker = args[0].upper()
    text = " ".join(args[1:])
    if not get_position(ticker):
        return f"{ticker} nicht gefunden."
    update_position(ticker, these=text)
    return f"✅ These fuer {ticker} aktualisiert."


def cmd_remove(args):
    if not args:
        return "Usage: /remove <TICKER>"
    ticker = args[0].upper()
    pos = get_position(ticker)
    if not pos:
        return f"{ticker} nicht gefunden."
    # E7: exit_kurs = aktueller Kurs
    exit_kurs = get_performance_safe(ticker)
    if exit_kurs is None:
        exit_kurs = pos.get("kauf_kurs")
    close_position(ticker, exit_kurs)
    send_telegram(f"🔒 {ticker} geschlossen @ {exit_kurs} (Regret-Log aktiv bis 90d).")
    return f"{ticker} geschlossen."


def get_performance_safe(ticker):
    try:
        from data_layer import get_performance
        return get_performance(ticker)
    except Exception:
        return None


def cmd_check(args):
    if not args:
        return "Usage: /check <TICKER>"
    ticker = args[0].upper()
    sd = get_stock_data(ticker)
    if not sd:
        return f"Keine Daten fuer {ticker} (API blockiert?)."
    return (f"{ticker}: {sd['spot']:.2f} | RSI {sd['rsi_14']:.0f} | "
            f"SMA50 {sd['sma_50']} | SMA200 {sd['sma_200']} | "
            f"52w {sd['low_52w']:.0f}-{sd['high_52w']:.0f}")


def cmd_portfolio(args):
    pos = get_active_positions()
    if not pos:
        return "Keine aktiven Positionen."
    lines = [f"📊 Portfolio ({len(pos)} Positionen):"]
    for p in pos:
        lines.append(f"- {p['ticker']} ({p.get('name')}): "
                     f"{p.get('stueckzahl')} @ {p.get('kauf_kurs')} "
                     f"| max_korr={p.get('max_korrelation')} "
                     f"cluster={p.get('cluster_id')}")
    return "\n".join(lines)


DISPATCH = {
    "add": cmd_add, "these": cmd_these, "remove": cmd_remove,
    "check": cmd_check, "portfolio": cmd_portfolio,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command")
    ap.add_argument("rest", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    fn = DISPATCH.get(args.command.lower())
    if not fn:
        print("Unbekannter Befehl. Verfuegbar:", list(DISPATCH))
        return
    print(fn(args.rest))


if __name__ == "__main__":
    main()
