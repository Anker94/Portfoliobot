"""event_watcher.py — automatische Event-Ueberwachung + Pre/Post-Trigger.

Laeuft taeglich. Laedt Events (daily_check scan_events fuellt DB), loest
Pre-Event (heute/morgen) und Post-Event (gestern) ueber P4 aus.

BMO/AMC-Logik: AMC heute -> Pre-Event heute; BMO morgen -> Pre-Event heute abend.
Pre-Event setzt processed NICHT (sonst kein Post-Event); nur Post setzt es.
"""
import os
import sys
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from db import get_events, mark_event_processed
from llm_runner import run_p4
from telegram import send_telegram


def main():
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)
    for ev in get_events(processed=False):
        ed = datetime.strptime(ev["datum"], "%Y-%m-%d").date()
        bmo = ev.get("bmo_amc")
        # Pre-Event: heute oder morgen (BMO morgen -> heute alerten)
        if ed == today or ed == tomorrow:
            # AMC heute oder BMO morgen -> jetzt Pre-Event
            if ed == today or bmo == "BMO":
                send_telegram(f"📅 Pre-Event {ev['ticker']} ({ev['event_typ']}) {ev['datum']}")
                run_p4(ev["ticker"], event_datum=ev["datum"], bmo_amc=bmo, post=False)
                # Pre markiert processed NICHT -> Post-Event bleibt moeglich
        # Post-Event: gestern
        elif ed == yesterday:
            send_telegram(f"📅 Post-Event {ev['ticker']} ({ev['event_typ']}) {ev['datum']}")
            run_p4(ev["ticker"], event_datum=ev["datum"], bmo_amc=bmo, post=True)
            mark_event_processed(ev["id"])
    print("event_watcher done.")


if __name__ == "__main__":
    main()
