"""trigger_check.py — CLI fuer manuelle Trigger-Pruefung (on-demand).

Prueft einen oder mehrere Ticker auf Smart-Money-Signale +
Support/Resistance-Cluster-Konfluenz und gibt eine BUY/SELL-Trigger-
Empfehlung. NIK prueft das Ergebnis manuell und entscheidet.

KEIN Standing-Listener, KEINE automatische Order-Ausfuehrung.
Nur ein Empfehlungs-Generator.

Aufruf:
  python scripts/trigger_check.py PSIX NVDA ORCL
  python scripts/trigger_check.py --ticker-file pfad/zur/datei.txt
  python scripts/trigger_check.py --all    # alle 9 Mavis-Picks
  python scripts/trigger_check.py --wissensdatenbank  # alle 234 aktien-*.md-Ticker
  python scripts/trigger_check.py PSIX --json  # maschinenlesbar
  python scripts/trigger_check.py PSIX --days 60  # 60-Tage-Fenster
"""
import os
import sys
import json
import re
import argparse
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import konsole_utf8
konsole_utf8()

from trigger_engine import trigger_check, log_trigger

# Default: Mavis' 9 Picks
MAVIS_PICKS = ["PSIX", "VUL.AX", "1810.HK", "ORCL", "RZLV",
               "PDD", "CPNG", "TCEHY", "NFLX"]


def formatiere_trigger(out: dict, ausfuehrlich: bool = True) -> str:
    """Menschenlesbare Darstellung eines Trigger-Checks."""
    z = []
    ticker = out["ticker"]
    kurs = out.get("kurs")
    kurs_txt = f"{kurs:.2f}" if kurs else "n/a"
    z.append(f"\n{'=' * 70}")
    z.append(f"  TRIGGER-CHECK: {ticker}   Stand {out['stand'][:19]}")
    z.append(f"  Kurs: {kurs_txt}")
    z.append("=" * 70)

    # Smart Money
    sm = out["smart_money"]
    z.append(f"\n  SMART-MONEY-SIGNALE:")
    z.append(f"  Insider-Cluster: {sm['insider_cluster']['signal']} "
             f"({sm['insider_cluster']['staerke']}, "
             f"{sm['insider_cluster']['anzahl_buyer']} Buyer, "
             f"${sm['insider_cluster']['gesamtwert_usd']:,.0f})")
    z.append(f"    -> {sm['insider_cluster']['details']}")
    z.append(f"  13F (Institutionelle Holdings): {sm['13f_institutional'].get('signal', '?')} "
             f"({sm['13f_institutional'].get('staerke', '?')}, "
             f"{sm['13f_institutional'].get('anzahl_holdings', 0)} Holdings)")
    if sm['13f_institutional'].get('details'):
        z.append(f"    -> {sm['13f_institutional']['details']}")
    z.append(f"  Options-Flow: {sm['options_flow'].get('signal', '?')} "
             f"({sm['options_flow'].get('staerke', '?')})")
    if sm['options_flow'].get('details'):
        z.append(f"    -> {sm['options_flow']['details']}")
    z.append(f"  -> Bullishe Signale: {sm['anzahl_bullish']}  |  "
             f"Bearishe Signale: {sm['anzahl_bearish']}")
    if sm['anzahl_bullish'] > 0:
        for s in sm['bullish_signale']:
            z.append(f"     + {s['quelle']}: {s['staerke']} - {s['details']}")
    if sm['anzahl_bearish'] > 0:
        for s in sm['bearish_signale']:
            z.append(f"     - {s['quelle']}: {s['staerke']} - {s['details']}")

    # Cluster
    cl = out["cluster"]
    if cl.get("fehler"):
        z.append(f"\n  CLUSTER: FEHLER - {cl['fehler']}")
    else:
        z.append(f"\n  SUPPORT/RESISTANCE-CLUSTER:")
        z.append(f"  RSI-14: {cl.get('rsi_14')}  ATR: {cl.get('atr')}")
        u = cl.get("naechste_unterstuetzung")
        w = cl.get("naechster_widerstand")
        if u:
            z.append(f"  Unterstuetzung: {u['zentrum']:.2f} "
                     f"(Abstand {u['abstand_pct']:+.2f}%, "
                     f"Staerke {u['staerke']}, {u['anzahl_quellen']} Quellen)")
        else:
            z.append(f"  Unterstuetzung: keine")
        if w:
            z.append(f"  Widerstand:     {w['zentrum']:.2f} "
                     f"(Abstand {w['abstand_pct']:+.2f}%, "
                     f"Staerke {w['staerke']}, {w['anzahl_quellen']} Quellen)")
        else:
            z.append(f"  Widerstand:     keine")
        z.append(f"  Konfluenz: {cl.get('konfluenz_einstufung', '?')} "
                 f"(Score {cl.get('konfluenz_score', '?')})")

    # Entscheidung
    ent = out["entscheidung"]
    z.append(f"\n  ENTSCHEIDUNG:")
    if ent["buy_trigger_fired"]:
        z.append(f"  ** BUY-TRIGGER FEUERT **  (max {ent['trigger_budget_max_eur']} EUR)")
        z.append(f"     Grund: {ent['buy_trigger_grund']}")
    elif ent["sell_trigger_fired"]:
        z.append(f"  ** SELL-TRIGGER FEUERT **")
        z.append(f"     Grund: {ent['sell_trigger_grund']}")
    else:
        # Diagnose, warum nicht gefeuert
        if sm['anzahl_bullish'] < 2 and sm['anzahl_bearish'] < 2:
            z.append(f"  Kein Trigger: zu wenig bullishe/bearishe Signale "
                     f"({sm['anzahl_bullish']}/{sm['anzahl_bearish']}, "
                     f"min. 2 noetig)")
        else:
            z.append(f"  Kein Trigger: Kurs nicht an Support/Resistance-Cluster "
                     f"(im ±3%-Band)")
    return "\n".join(z)


def extract_ticker_from_title(text: str) -> str | None:
    """Extrahiert das Ticker-Symbol aus der H1-Titelseite der MD-Datei.

    Pattern der ersten Zeile: ``# Firmenname (TICKER)`` oder ``# Firmenname (TICKER.SA)``.
    Zwei-Attempt-Strategie:
    1. Klammer-Inhalt der ersten Zeile → direkt
    2. Falls nicht, erster Grossbuchstaben-Token der ersten Zeile.
    """
    first_line = text.split("\n", 1)[0]
    # Attempt 1: Klammer-Inhalt
    bracket = re.search(r"\(([^)]+)\)", first_line)
    if bracket:
        cand = bracket.group(1).strip()
        if cand and " " not in cand and "/" not in cand:
            return cand
    # Attempt 2: Grossbuchstaben-Token
    tok = re.search(r"\b([A-Z]{2,}[A-Z0-9.]*)\b", first_line)
    if tok:
        cand = tok.group(1)
        if cand and " " not in cand and "/" not in cand:
            return cand
    return None


def _wissensdatenbank_tickerextraction(aktien_dir: str | Path) -> list[str]:
    """Extrahiert alle Ticker aus aktien/*.md über die H1-Titelseite."""
    aktien_path = Path(aktien_dir)
    tickers = []
    for md_file in sorted(aktien_path.glob("*.md")):
        if md_file.name.upper() == "README.MD":
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        ticker = extract_ticker_from_title(text)
        if ticker:
            tickers.append(ticker)
    return tickers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="Ticker-Symbole (z.B. PSIX NVDA)")
    ap.add_argument("--all", action="store_true",
                    help="Alle 9 Mavis-Picks pruefen")
    ap.add_argument("--wissensdatenbank", action="store_true",
                    help="Alle Ticker aus aktien/*.md — Ergebnis in trigger_wb_results.json")
    ap.add_argument("--ticker-file", help="Pfad zu Datei mit einem Ticker pro Zeile")
    ap.add_argument("--days", type=int, default=30, help="Insider-Fenster in Tagen (default 30)")
    ap.add_argument("--json", action="store_true", help="Output als JSON (sonst formatiert)")
    ap.add_argument("--log", action="store_true",
                    help="Ausgeloese Trigger in trigger_log.json loggen (nach manueller Pruefung)")
    args = ap.parse_args()

    tickers = list(args.tickers)
    if args.all:
        tickers = MAVIS_PICKS
    if args.wissensdatenbank:
        _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        aktien_dir = os.path.join(_ROOT, "..", "..", "wissensdatenbank", "aktien")
        aktien_dir = os.path.normpath(aktien_dir)
        tickers = _wissensdatenbank_tickerextraction(aktien_dir)
        if not tickers:
            # Debug output: zeige Pfad und Anzahl Dateien
            import sys as _sys
            _sys.stderr.write(f"INFO: aktien_dir={aktien_dir}\n")
            from pathlib import Path as _Path
            _p = _Path(aktien_dir)
            _sys.stderr.write(f"INFO: existiert={_p.exists()}, Dateien={len(list(_p.glob('*.md')))}\n")
            print("Keine Ticker aus aktien/*.md extrahiert", file=_sys.stderr)
            _sys.exit(1)
        # Ergebnisdatei im data/-Unterordner des Projekt-Roots
        results_file = os.path.join(_ROOT, "data", "trigger_wb_results.json")
    if args.ticker_file:
        if not os.path.exists(args.ticker_file):
            print(f"Ticker-File nicht gefunden: {args.ticker_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.ticker_file, encoding="utf-8") as f:
            for line in f:
                t = line.strip()
                if t and not t.startswith("#"):
                    tickers.append(t)
    if not tickers:
        ap.print_help()
        print("\nBeispiel: python scripts/trigger_check.py --all")
        sys.exit(1)

    ergebnisse = []
    for t in tickers:
        try:
            out = trigger_check(t, days_back=args.days)
            if args.json or args.wissensdatenbank:
                ergebnisse.append(out)
            else:
                print(formatiere_trigger(out))
                if not args.json and "volltext" in out.get("cluster", {}):
                    print("\n  --- Cluster-Volltext (Fib-Cluster-Analyse) ---")
                    print(out["cluster"]["volltext"])
            if args.log and (out["entscheidung"]["buy_trigger_fired"]
                             or out["entscheidung"]["sell_trigger_fired"]):
                aus = "buy" if out["entscheidung"]["buy_trigger_fired"] else "sell"
                log_trigger(t, aus, out)
        except Exception as e:
            print(f"FEHLER fuer {t}: {e}", file=sys.stderr)
            if args.json or args.wissensdatenbank:
                ergebnisse.append({"ticker": t, "fehler": str(e)})
    if args.json or args.wissensdatenbank:
        if args.wissensdatenbank:
            os.makedirs(_ROOT, exist_ok=True)
            with open(results_file, "w", encoding="utf-8") as f:
                json.dump(ergebnisse, f, ensure_ascii=False, indent=2)
            print(f"{len(ergebnisse)} Ergebnisse in {results_file} geschrieben")
        else:
            print(json.dumps(ergebnisse, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
