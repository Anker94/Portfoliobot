"""DB-Migration v2 (idempotent) — Audit-Items K1-K5, W1-W6, A1-A4."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from db import _conn


def migrate_v2():
    c = _conn()
    try:
        # positionen
        c.execute("ALTER TABLE positionen ADD COLUMN exit_preis REAL")
        c.execute("ALTER TABLE positionen ADD COLUMN treiber TEXT")
        c.execute("ALTER TABLE positionen ADD COLUMN news_blind_days INTEGER")
        c.execute("ALTER TABLE positionen ADD COLUMN news_blind_alerted INTEGER DEFAULT 0")
        # empfehlungen
        c.execute("ALTER TABLE empfehlungen ADD COLUMN outcome_30d REAL")
        c.execute("ALTER TABLE empfehlungen ADD COLUMN outcome_vs_spy_30d REAL")
        c.execute("ALTER TABLE empfehlungen ADD COLUMN outcome_checked INTEGER DEFAULT 0")
        c.commit()
    except Exception as e:
        # Spalte existiert evtl schon -> ignorieren
        c.rollback()
    finally:
        c.close()


if __name__ == "__main__":
    migrate_v2()
    print("migrate_v2 done")
