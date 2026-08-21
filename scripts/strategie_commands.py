"""strategie_commands.py — CLI/Telegram-Befehle fuer Strategie-Analysen.

Befehle:
  fib NVDA [--zeitfenster 4h] [--spanne 3] [--legs 6] [--json]
      Fibonacci-Cluster + Konfluenz (RSI, Doppeltop/-boden).
      Zeitfenster: 1h 4h 8h 1d 1wk (4h/8h werden aus 1h-Kerzen resampled,
      weil yfinance diese Intervalle nicht direkt liefert).

Diese Befehle geben ausschliesslich Analysen aus. Sie loesen keine Order aus —
die Verbindung Signal -> Order trifft ein Mensch ueber broker_commands.py.
"""
import os
import sys
import json
import argparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import konsole_utf8
konsole_utf8()

import fib_cluster


def cmd_fib(args):
    ap = argparse.ArgumentParser(prog="fib", add_help=False)
    ap.add_argument("ticker")
    ap.add_argument("--zeitfenster", default="4h",
                    choices=sorted(fib_cluster.ZEITFENSTER))
    ap.add_argument("--spanne", type=int, default=3,
                    help="Pivot-Fenster in Kerzen (groesser = weniger, robustere Swings)")
    ap.add_argument("--legs", type=int, default=6,
                    help="Anzahl juengster Swing-Legs, aus denen Level gebildet werden")
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(args)
    except SystemExit:
        return "Usage: fib <TICKER> [--zeitfenster 1h|4h|8h|1d|1wk] [--spanne N] [--legs N] [--json]"

    ergebnis = fib_cluster.analysiere(a.ticker.upper(),
                                      zeitfenster=a.zeitfenster,
                                      spanne=a.spanne, max_legs=a.legs)
    if a.json:
        return json.dumps(ergebnis, ensure_ascii=False, indent=2, default=str)
    return fib_cluster.formatiere(ergebnis)


DISPATCH = {"fib": cmd_fib}


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
