"""broker_commands.py — CLI/Telegram-Befehle fuer die Papier-Ausfuehrung.

Befehle:
  status                              -> Kontostand des Papier-Kontos
  bestand                             -> offene Papier-Positionen
  kaufen NVDA 10 [--kurs 150] [--limit 149] [--these-neu]
  verkaufen NVDA 5 [--kurs 160] [--limit 161]
  orders [TICKER]                     -> letzte Orders inkl. abgelehnter
  reset [--kapital 100000]            -> Sandbox komplett zuruecksetzen

Jede Kauf-/Verkauf-Order laeuft zwingend durch lib/order_guard.pruefe().
Eine verletzte Regel lehnt die Order ab; das Protokoll wird mitgespeichert.

PAPIER-ONLY: lib/broker.py laesst keinen Live-Endpunkt zu (LiveTradingBlocked).
"""
import os
import sys
import argparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import konsole_utf8
konsole_utf8()

import db
import broker
from order_guard import pruefe, format_protokoll


def cmd_status(args):
    try:
        k = broker.konto()
    except broker.LiveTradingBlocked as e:
        return f"⛔ {e}"
    except broker.BrokerFehler as e:
        return f"❌ {e}"
    zeilen = [
        f"🏦 Papier-Konto ({k['provider']} · {k['modus']})",
        f"   Cash:      {k['cash']:>12,.2f} {k['waehrung']}",
        f"   Depotwert: {k['depotwert']:>12,.2f} {k['waehrung']}",
        f"   Kaufkraft: {k['kaufkraft']:>12,.2f} {k['waehrung']}",
    ]
    if k.get("startkapital"):
        gv = k["depotwert"] - k["startkapital"]
        gv_pct = gv / k["startkapital"] * 100 if k["startkapital"] else 0
        zeilen.append(f"   G/V:       {gv:>12,.2f} {k['waehrung']} ({gv_pct:+.2f}%)")
    if k.get("hinweis"):
        zeilen.append(f"   ⚠️ {k['hinweis']}")
    return "\n".join(zeilen)


def cmd_bestand(args):
    try:
        rows = broker.bestand()
    except broker.LiveTradingBlocked as e:
        return f"⛔ {e}"
    except broker.BrokerFehler as e:
        return f"❌ {e}"
    if not rows:
        return "Papier-Depot ist leer."
    zeilen = [f"📄 Papier-Bestand ({len(rows)}):"]
    for r in rows:
        zeilen.append(f"- {r['ticker']}: {r['stueckzahl']:g} Stk "
                      f"@ Ø {r['durchschnittskurs']:.2f}")
    return "\n".join(zeilen)


def _order(seite, args):
    ap = argparse.ArgumentParser(prog=seite, add_help=False)
    ap.add_argument("ticker")
    ap.add_argument("stueckzahl", type=float)
    ap.add_argument("--kurs", type=float, default=None,
                    help="Ausfuehrungskurs erzwingen (Tests/Offline)")
    ap.add_argument("--limit", type=float, default=None)
    ap.add_argument("--these-neu", action="store_true",
                    help="Guardrail 7: neue Faktenlage, kein blosses Verbilligen")
    ap.add_argument("--rec-id", type=int, default=None,
                    help="ID der ausloesenden Empfehlung (E6-Kette)")
    try:
        a = ap.parse_args(args)
    except SystemExit:
        return f"Usage: {seite} <TICKER> <STUECK> [--kurs X] [--limit X] [--these-neu]"

    ticker = a.ticker.upper()
    kurs = a.kurs if a.kurs is not None else broker._spot(ticker)
    if kurs is None:
        return (f"❌ Kein Kurs fuer {ticker} verfuegbar (API-Ausfall). "
                f"Es wird kein Preis geschaetzt — Order nicht abgegeben. "
                f"Mit --kurs kann ein Kurs erzwungen werden.")

    ok, protokoll = pruefe(ticker, seite, a.stueckzahl, kurs,
                           these_neu=a.these_neu)
    if not ok:
        o = broker._ablehnen(ticker, seite, a.stueckzahl,
                             "limit" if a.limit else "market", a.limit,
                             "Guardrail verletzt", protokoll, a.rec_id)
        return (f"⛔ Order abgelehnt ({ticker} {seite} {a.stueckzahl:g}):\n"
                f"{format_protokoll(protokoll)}\n"
                f"   protokolliert als Order #{o['id']}")

    try:
        o = broker.order(ticker, seite, a.stueckzahl,
                         order_typ="limit" if a.limit else "market",
                         limit_kurs=a.limit, kurs=a.kurs, rec_id=a.rec_id,
                         guardrail=protokoll)
    except broker.LiveTradingBlocked as e:
        return f"⛔ {e}"
    except broker.BrokerFehler as e:
        return f"❌ {e}"

    kopf = "✅" if o["status"] == "ausgefuehrt" else "⛔"
    zeilen = [f"{kopf} Order #{o['id']} {o['status']}: {o['seite']} "
              f"{o['stueckzahl']:g} {o['ticker']}"]
    if o.get("ausgefuehrt_kurs"):
        zeilen.append(f"   Kurs {o['ausgefuehrt_kurs']:.2f} · Volumen "
                      f"{o['ausgefuehrt_kurs'] * o['stueckzahl']:,.2f}")
    if o.get("ablehnungsgrund"):
        zeilen.append(f"   Grund: {o['ablehnungsgrund']}")
    zeilen.append(format_protokoll(protokoll))
    return "\n".join(zeilen)


def cmd_kaufen(args):
    return _order("kaufen", args)


def cmd_verkaufen(args):
    return _order("verkaufen", args)


def cmd_orders(args):
    ticker = args[0].upper() if args else None
    rows = broker.orders(limit=20, ticker=ticker)
    if not rows:
        return "Keine Orders protokolliert."
    zeichen = {"ausgefuehrt": "✅", "abgelehnt": "⛔", "angenommen": "⏳"}
    zeilen = [f"🧾 Letzte Orders ({len(rows)}):"]
    for o in rows:
        k = f"@ {o['ausgefuehrt_kurs']:.2f}" if o.get("ausgefuehrt_kurs") else ""
        zeilen.append(f"{zeichen.get(o['status'], '?')} #{o['id']} "
                      f"{o['erstellt_am'][:16]} {o['seite']} "
                      f"{o['stueckzahl']:g} {o['ticker']} {k}"
                      + (f" — {o['ablehnungsgrund']}" if o.get("ablehnungsgrund") else ""))
    return "\n".join(zeilen)


def cmd_reset(args):
    ap = argparse.ArgumentParser(prog="reset", add_help=False)
    ap.add_argument("--kapital", type=float, default=None)
    try:
        a = ap.parse_args(args)
    except SystemExit:
        return "Usage: reset [--kapital 100000]"
    _, _, _, _, startkapital, waehrung = broker.broker_config()
    kapital = a.kapital if a.kapital is not None else startkapital
    db.konto_reset()
    db.konto_init(kapital, waehrung, provider="paper")
    return (f"🧹 Sandbox zurueckgesetzt: {kapital:,.2f} {waehrung} "
            f"Startkapital, Bestand und Orders geleert.")


DISPATCH = {
    "status": cmd_status, "bestand": cmd_bestand,
    "kaufen": cmd_kaufen, "verkaufen": cmd_verkaufen,
    "orders": cmd_orders, "reset": cmd_reset,
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
