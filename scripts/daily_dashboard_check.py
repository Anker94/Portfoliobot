"""daily_dashboard_check.py — A1 Tagesform-Check fuer Portfoliobot Dashboard.

Ersatz fuer das PowerShell-Skript, weil hier PowerShell-Logging/Exit-Code
nicht zuverlässig zurückkommt. Nutzt requests statt Invoke-WebRequest.
Schreibt eine Zeile in data/tagesform.log: [ISO-Zeit] OK oder Fehlerbeschreibung.
"""
import os
import sys
import json
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import load_config
import requests

PORT = int(os.environ.get("PORTFOLIOBOT_PORT", "5000"))
BASE_URL = os.environ.get("PORTFOLIOBOT_URL", "http://127.0.0.1:5000")
LOG_PATH = os.path.join(_ROOT, "data", "tagesform.log")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> int:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    now = _now_iso()
    try:
        listen = requests.get(f"{BASE_URL}/healthz", timeout=10)
        health_ok = listen.status_code == 200
    except Exception as e:
        line = f"{now} FAIL /healthz nicht erreichbar: {e}"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line)
        return 2

    if not health_ok:
        line = f"{now} FAIL /healthz status={listen.status_code}"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line)
        return 2

    try:
        home = requests.get(BASE_URL, timeout=10)
        home_ok = home.status_code == 200
    except Exception as e:
        line = f"{now} FAIL / nicht erreichbar: {e}"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line)
        return 3

    if not home_ok:
        line = f"{now} FAIL / status={home.status_code}"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line)
        return 3

    line = f"{now} OK"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
