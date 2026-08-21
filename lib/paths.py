"""Zentrale Pfad-Auflösung. lib/ ist Geschwister von scripts/ und prompts/.
PROJECT_ROOT ist das übergeordnete Verzeichnis, damit `import lib.x` aus scripts/ funktioniert.
Siehe SKILL.md Pitfall: 'Windows sys.path'."""
import os
import sys

# lib/ -> parent = PROJECT_ROOT
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(PROJECT_ROOT, "lib")
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

for _p in (PROJECT_ROOT, LIB_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# config.json laden (optional)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def load_config():
    import json
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# Hermes-globale .env laden (Datei, nicht exportierte Env-Vars).
# Liefert die Werte nur in os.environ, gibt NICHTS zurueck/druckt NICHTS aus.
_HERMES_ENV = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "hermes", ".env"
)


def load_hermes_env():
    """Liest die Hermes-globale .env in os.environ (nur nicht gesetzte Keys)."""
    if not os.path.exists(_HERMES_ENV):
        return
    for line in open(_HERMES_ENV, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        # nur setzen wenn noch nicht in der Umgebung
        if k and k not in os.environ:
            os.environ[k] = v


# Automatisch laden, damit telegram.py/llm.py die Keys finden
load_hermes_env()


def konsole_utf8():
    """Stellt stdout/stderr auf UTF-8 um (nur fuer CLI-Einstiegspunkte).

    Windows-Konsole und Pipes benutzen sonst cp1252; jedes Emoji in einer
    Ausgabe (📊 🔼 ⛔ ...) beendet das Skript mit UnicodeEncodeError. Bewusst
    NICHT automatisch beim Import: eine Bibliothek soll die globalen Streams
    des aufrufenden Prozesses nicht umkonfigurieren.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def db_path():
    cfg = load_config()
    p = cfg.get("db_path")
    if p:
        return p
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "stock.db")
