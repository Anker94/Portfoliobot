"""E4 — Devil's-Advocate-Dissens (LLM billig).

Laeuft nach jedem P3-Lauf mit Empfehlung KAUFEN/AUFSTOCKEN. Zweiter LLM-Call
mit INVERTIERTEM Mandat, sieht NUR die Rohdaten (NICHT die P3-Empfehlung).
Code vergleicht: P3=Kaufen + Advocate=these_brechend -> Dissens-Alert.

Warum getrennte Kontexte: Advocate darf P3-Empfehlung nicht sehen (Ankerung).
Blind-Dissens ist das Signal.

Kosten: ~4 Calls/Monat, free-Modell -> ~0 EUR.
"""
import json
from llm import call_llm
from json_parse import parse_llm_json
from telegram import alert
from db import update_recommendation


PROMPT = """Du bist der beauftragte Gegner dieser Investment-These. Finde die 3
staerksten Gruende GEGEN einen Kauf von {ticker} auf Basis der gelieferten Daten.

Daten (nur diese bewerten):
{data_block}

Bewerte abschliessend: Ist einer dieser Gruende THESE-BRECHEND (wuerde die
hinterlegte These "{these}" widerlegen) oder nur RISIKO-RAUSCHEN?

Antwort als JSON:
{{"gruende": ["...", "...", "..."], "these_brechend": true/false,
  "begruendung": "..."}}"""


def run_devils_advocate(ticker, these, data_block, rec_id=None,
                        model_task="advocate"):
    """Liefert (dissens_bool, advocate_json). Bei Dissens: Telegram-Alert."""
    prompt = PROMPT.format(ticker=ticker, these=these, data_block=data_block)
    raw = call_llm(model_task, prompt, temperature=0.4, json_mode=True)
    aj = parse_llm_json(raw)
    if not isinstance(aj, dict):
        aj = {"gruende": [], "these_brechend": False,
              "begruendung": "Parser-Fehler"}
    these_brechend = bool(aj.get("these_brechend"))
    # Speichern
    if rec_id is not None:
        update_recommendation(rec_id, advocate_json=json.dumps(aj, ensure_ascii=False),
                              advocate_dissens=1 if these_brechend else 0)
    if these_brechend:
        msg = (f"⚠️ DISSENS-ALERT ({ticker})\n"
               f"P3 empfiehlt KAUFEN, Devil's-Advocate sagt THESE-BRECHEND:\n"
               f"- " + "\n- ".join(aj.get("gruende", [])) + "\n"
               f"Begruendung: {aj.get('begruendung', '')}")
        alert(msg)
    return these_brechend, aj


if __name__ == "__main__":
    # Test im Mock-Modus
    db, aj = run_devils_advocate("NVDA", "Langfrist-Compounder",
                                 "Kurs 150, RSI 55", rec_id=None)
    print("dissens:", db, aj)
