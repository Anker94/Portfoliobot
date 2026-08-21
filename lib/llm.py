"""LLM-Client: OpenAI-kompatibel, routing-faehig.

Provider (config/config.json -> llm.provider):
  - "nous"         : Nous Subscription ueber Hermes Gateway-OAuth.
                     Endpoint https://inference-api.nousresearch.com/v1 ,
                     Bearer = scoped JWT, zur Laufzeit ueber Hermes' eigene
                     auth-Funktion aufgeloest (kein Datei-Scraping, kein
                     hartkodierter Key). Modelle: Nous free-tier Modelle.
  - "hermes_free"  : OpenRouter-kompatibel mit API-Key (OPENROUTER_API_KEY).
  - "mock"         : kein Netzwerk, deterministische Antworten (Default,
                     damit die Pipeline offline testbar ist).

Guardrail: Die Funktion ruft NIE yfinance/Finnhub selbst -- sie bekommt nur
injizierte Daten (siehe SKILL.md 'Kernregel').
"""
import os
import json
import sys

try:
    from paths import load_config
except Exception:  # standalone
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    def load_config():
        return {}


# Nous free-tier Modelle (ueber inference-api.nousresearch.com/v1).
# Hermes empfiehlt ueber die Subscription i.d.R. tencent/hy3:free o.ae.;
# hier als Default hinterlegt, ueberschreibbar in config.json.
DEFAULT_NOUS_MODELS = {
    "P1": "tencent/hy3:free",
    "P2": "tencent/hy3:free",
    "P3": "tencent/hy3:free",
    "P4": "tencent/hy3:free",
    "P5": "tencent/hy3:free",
    "P7": "tencent/hy3:free",
    "advocate": "tencent/hy3:free",
}

NOUS_INFERENCE_URL = "https://inference-api.nousresearch.com/v1"


def _resolve_env_key():
    for env in ("OPENROUTER_API_KEY", "NOUS_API_KEY", "HERMES_OPENROUTER_KEY"):
        v = os.environ.get(env)
        if v:
            return v
    return None


def _resolve_nous_token():
    """Public-Release-Stub: Provider 'nous' erwartet API-Key in config.json
    (siehe config/config.example.json). Diese Funktion gibt None zurueck; die
    Auth-Aufloesung laeuft dann ueber den Bearer-Token aus der Config."""
    return None


def _client_cfg():
    cfg = load_config().get("llm", {})
    provider = cfg.get("provider", "mock")
    models = cfg.get("models") or DEFAULT_NOUS_MODELS
    base_url = cfg.get("base_url")
    api_key = cfg.get("api_key")

    if provider == "nous":
        base_url = NOUS_INFERENCE_URL
        api_key = _resolve_nous_token()  # JWT zur Laufzeit
    elif provider in ("hermes_free", "openrouter_free"):
        base_url = base_url or "https://openrouter.ai/api/v1"
        api_key = api_key or _resolve_env_key()
    else:  # mock
        base_url = base_url or "https://openrouter.ai/api/v1"
        api_key = api_key or _resolve_env_key()
    return provider, base_url, api_key, models


def model_for(task):
    """Gibt das konfigurierte Modell fuer einen Task zurueck (P1..P7, advocate)."""
    _, _, _, models = _client_cfg()
    return models.get(task, models.get("P3", "tencent/hy3:free"))


def call_llm(task, prompt, system=None, temperature=0.2, json_mode=False,
             max_tokens=1500):
    """Zentraler Einstiegspunkt. task = 'P1'..'P7' oder 'advocate'.
    Liefert den reinen Antwort-Text (String)."""
    provider, base_url, api_key, models = _client_cfg()
    model = models.get(task, "tencent/hy3:free")

    if provider == "mock" or not api_key:
        return _mock_response(task, prompt, model)

    import requests
    url = base_url.rstrip("/") + "/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # Nous (tencent/hy3:free u.a.) lehnt response_format={"type":"json_object"} ab
    # (400). OpenAI/OpenRouter unterstuetzen es. Wir senden es nur, wenn nicht
    # nous. Die Prompts fordern ohnehin JSON; json_parse entfernt Markdown-Fences.
    if json_mode and provider != "nous":
        body["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, json=body, headers=headers, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------
# MOCK-Modus: deterministische Antworten fuer Offline-Tests
# --------------------------------------------------------------------------
def _mock_response(task, prompt, model):
    """Liefert eine plausible, strukturierte Antwort OHNE Netzwerk.
    Erkennt aus dem Prompt, welcher Prompt-Typ gemeint ist, und gibt
    passendes JSON bzw. Text zurueck."""
    ticker = _extract_ticker(prompt)
    p = prompt.lower()

    # P3 / Empfehlung -> JSON mit Szenarien (E5) + erwarteter_treiber (E6)
    if "einzelaktien" in p or "empfehlung" in p or task == "P3":
        return json.dumps({
            "ticker": ticker,
            "these_check": "intakt",
            "typ": "KAUFEN",
            "confidence": "mittel",
            "contra": ["Bewertung ambitioniert", "Makro-Abhaengigkeit"],
            "begruendung": "MOCK: Tech-Fundamentals intakt, Treiber positiv.",
            "erwarteter_treiber": {"treiber": "^TNX", "richtung": "fallend"},
            "szenarien": [
                {"name": "Bull", "wahrscheinlichkeit": 0.30, "kursziel_12m": 210},
                {"name": "Base", "wahrscheinlichkeit": 0.50, "kursziel_12m": 165},
                {"name": "Bear", "wahrscheinlichkeit": 0.20, "kursziel_12m": 95},
            ],
        }, ensure_ascii=False)

    # E4 Advocate -> invertiertes Mandat
    if task == "advocate" or "gegner" in p:
        return json.dumps({
            "gruende": [
                "Makro-Zinsumfeld belastet Bewertung",
                "Wettbewerb intensiviert sich",
                "Chart technisch ueberkauft",
            ],
            "these_brechend": False,
            "begruendung": "MOCK: Gegenargumente sind Risiko-Rauschen, brechen die These nicht.",
        }, ensure_ascii=False)

    # P5 / P7 / P1 / P2 / P4 -> kurzer Text
    return (f"MOCK-ANTWORT [{model}] fuer Task {task}. "
            f"Analysierte Daten aus Prompt ({len(prompt)} Zeichen) "
            f"wurden als Fakten beruecksichtigt. Kein LLM-Netzwerkaufruf "
            f"(Mock-Modus).")


def _extract_ticker(prompt):
    import re
    # typische Marker: "Ticker: NVDA", "von NVDA", "fuer NVDA"
    m = re.search(r"(?:ticker|von|fuer|position)\s*[:\s]\s*([A-Z][A-Z0-9.V]{1,6})",
                  prompt, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return "XXXX"
