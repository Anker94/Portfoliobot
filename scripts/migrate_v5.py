"""DB-Migration v5 (idempotent) — Papier-Ausfuehrungsschicht.

Neue Tabellen fuer den Broker-Layer (lib/broker.py):
  orders         — jede Order mit Guardrail-Protokoll, auch die abgelehnten
  broker_konto   — Barbestand des Papier-Kontos (nur Provider 'paper')
  paper_bestand  — Stueckzahlen des Papier-Kontos

Bewusst getrennt von `positionen`: dort steht das real gefuehrte Portfolio,
hier der Sandbox-Bestand. Eine Vermischung wuerde die Outcome-Auswertung
(E6/E7) verfaelschen, weil Papier-Trades sonst als echte Entscheidungen in
die Attribution laufen.

Wird nach init_database + v2 + v3 + v4 ausgefuehrt.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from db import _conn


def migrate_v5():
    c = _conn()
    try:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker_order_id TEXT,
                provider TEXT,
                ticker TEXT NOT NULL,
                seite TEXT,               -- kaufen / verkaufen
                stueckzahl REAL,
                order_typ TEXT,           -- market / limit
                limit_kurs REAL,
                status TEXT,              -- angenommen / ausgefuehrt / abgelehnt
                ausgefuehrt_kurs REAL,
                ablehnungsgrund TEXT,
                guardrail_json TEXT,      -- Protokoll aller geprueften Regeln
                rec_id INTEGER,           -- ausloesende Empfehlung (E6-Kette)
                erstellt_am TEXT,
                aktualisiert_am TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_orders_ticker ON orders(ticker);
            CREATE INDEX IF NOT EXISTS idx_orders_erstellt ON orders(erstellt_am);

            CREATE TABLE IF NOT EXISTS broker_konto (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                provider TEXT,
                startkapital REAL,
                cash REAL,
                waehrung TEXT DEFAULT 'USD',
                angelegt_am TEXT,
                aktualisiert_am TEXT
            );

            CREATE TABLE IF NOT EXISTS paper_bestand (
                ticker TEXT PRIMARY KEY,
                stueckzahl REAL,
                durchschnittskurs REAL,
                aktualisiert_am TEXT
            );
            """
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


if __name__ == "__main__":
    migrate_v5()
    print("migrate_v5 done")
