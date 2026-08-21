"""DB-Migration v4 (idempotent) — P8 Edge-Module E1-E8.

Neue Spalten fuer positionen / empfehlungen + neue Tabelle news_gesehen.
Wird nach init_database + v2 + v3 ausgefuehrt.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from db import _conn


def migrate_v4():
    c = _conn()
    try:
        # positionen
        for col, typ in [
            ("max_korrelation", "REAL"),
            ("cluster_id", "TEXT"),
            ("nach_exit_30d", "REAL"),
            ("nach_exit_90d", "REAL"),
            ("exit_datum", "TEXT"),
            ("exit_kurs", "REAL"),
        ]:
            try:
                c.execute(f"ALTER TABLE positionen ADD COLUMN {col} {typ}")
            except Exception:
                pass  # existiert schon

        # status-Default 'aktiv' ist schon im Schema; sicherstellen
        try:
            c.execute("ALTER TABLE positionen ADD COLUMN status TEXT DEFAULT 'aktiv'")
        except Exception:
            pass

        # empfehlungen
        for col, typ in [
            ("advocate_dissens", "INTEGER"),
            ("advocate_json", "TEXT"),
            ("erwarteter_treiber", "TEXT"),
            ("attribution", "TEXT"),
            ("ev_berechnet", "REAL"),
            ("kelly_groesse", "REAL"),
            ("szenarien_json", "TEXT"),
        ]:
            try:
                c.execute(f"ALTER TABLE empfehlungen ADD COLUMN {col} {typ}")
            except Exception:
                pass

        # neue Tabelle
        c.execute(
            """CREATE TABLE IF NOT EXISTS news_gesehen (
                   hash TEXT PRIMARY KEY,
                   ticker TEXT,
                   datum TEXT
               )"""
        )
        c.commit()
    except Exception as e:
        c.rollback()
        raise
    finally:
        c.close()


if __name__ == "__main__":
    migrate_v4()
    print("migrate_v4 done")
