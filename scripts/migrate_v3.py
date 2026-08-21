"""DB-Migration v3 (idempotent) — P7 Lern-Loop & Regime-Tabellen."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from db import _conn


def migrate_v3():
    c = _conn()
    try:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS regime_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                datum TEXT,
                signal TEXT,
                alter_wert TEXT,
                neuer_wert TEXT,
                parameter_aenderung TEXT,
                outcome_bewertung_90d TEXT
            );
            CREATE TABLE IF NOT EXISTS prompt_versionen (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_id TEXT,
                version INTEGER,
                datei TEXT,
                aktiv_seit TEXT,
                aenderungsgrund TEXT,
                vorgeschlagen_von TEXT
            );
            CREATE TABLE IF NOT EXISTS lern_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                datum TEXT,
                befunde_json TEXT,
                vorschlaege_json TEXT,
                entscheidung TEXT,
                stichprobengroesse INTEGER
            );
            """
        )
        c.execute("ALTER TABLE empfehlungen ADD COLUMN regime_bei_empfehlung TEXT")
        c.commit()
    except Exception as e:
        c.rollback()
    finally:
        c.close()


if __name__ == "__main__":
    migrate_v3()
    print("migrate_v3 done")
