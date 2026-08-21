"""SQLite-Datenbank: Schema (v1-Basis) + CRUD.

Wird von init_database() aufgerufen und danach die idempotenten Migrationen
migrate_v2 / migrate_v3 / migrate_v4 ausgeführt -> vollstaendiges v4-Schema.

Prinzip (SKILL.md): Code liefert Fakten, LLM liefert Urteil. Diese Datei ist
rein persistente Schicht, kein LLM-Aufruf.
"""
import os
import sqlite3
import json
from datetime import datetime, date

from paths import db_path, PROJECT_ROOT

SCHEMA_VERSION = 5


def _conn():
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA foreign_keys=ON;")
    return c


def init_database():
    """Erstellt die Basis-Tabellen (v1) falls nicht vorhanden.
    Migrationen v2/v3/v4 danach separat ausfuehren."""
    c = _conn()
    try:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS positionen (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                name TEXT,
                kauf_kurs REAL,
                stueckzahl REAL,
                these TEXT,
                angelegt_am TEXT,
                status TEXT DEFAULT 'aktiv'
            );

            CREATE TABLE IF NOT EXISTS empfehlungen (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                datum TEXT,
                typ TEXT,            -- KAUFEN / AUFSTOCKEN / HALTEN / REDUZIEREN / VERKAUFEN
                confidence TEXT,     -- hoch / mittel / niedrig
                begruendung TEXT,
                prompt_version TEXT,
                regime_bei_empfehlung TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                datum TEXT,
                event_typ TEXT,      -- earnings / dividend / split / other
                bmo_amc TEXT,        -- BMO / AMC / None
                processed INTEGER DEFAULT 0,
                UNIQUE(ticker, datum, event_typ)
            );

            CREATE TABLE IF NOT EXISTS telegram_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zeit TEXT,
                ziel TEXT,
                text TEXT
            );
            """
        )
        c.commit()
    finally:
        c.close()


# --------------------------------------------------------------------------
# Positionen
# --------------------------------------------------------------------------
def add_position(ticker, name, kauf_kurs, stueckzahl, these=""):
    c = _conn()
    try:
        cur = c.execute(
            """INSERT INTO positionen
               (ticker, name, kauf_kurs, stueckzahl, these, angelegt_am, status)
               VALUES (?,?,?,?,?,?, 'aktiv')""",
            (ticker, name, kauf_kurs, stueckzahl, these, _now()),
        )
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def get_position(ticker):
    c = _conn()
    try:
        row = c.execute(
            "SELECT * FROM positionen WHERE ticker=? AND status='aktiv'", (ticker,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def get_active_positions():
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM positionen WHERE status='aktiv' ORDER BY ticker"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def get_all_positions(include_closed=False):
    c = _conn()
    try:
        if include_closed:
            rows = c.execute("SELECT * FROM positionen ORDER BY ticker").fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM positionen WHERE status='aktiv' ORDER BY ticker"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def update_position(ticker, **fields):
    c = _conn()
    try:
        cols = []
        vals = []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(v)
        if not cols:
            return
        vals.append(ticker)
        c.execute(
            f"UPDATE positionen SET {','.join(cols)} WHERE ticker=?", vals
        )
        c.commit()
    finally:
        c.close()


def close_position(ticker, exit_kurs, exit_datum=None):
    """Setzt Position auf geschlossen (E7 Regret-Log)."""
    c = _conn()
    try:
        c.execute(
            """UPDATE positionen SET status='geschlossen', exit_kurs=?,
               exit_datum=? WHERE ticker=? AND status='aktiv'""",
            (exit_kurs, exit_datum or _now(), ticker),
        )
        c.commit()
    finally:
        c.close()


def set_position_json(ticker, column, obj):
    """Hilfsfunktion fuer JSON-Spalten (treiber, szenarien_json, ...)"""
    update_position(ticker, **{column: json.dumps(obj, ensure_ascii=False)})


# --------------------------------------------------------------------------
# Empfehlungen
# --------------------------------------------------------------------------
def add_recommendation(ticker, typ, confidence, begruendung,
                       prompt_version=None, regime_bei_empfehlung=None,
                       **extra):
    c = _conn()
    try:
        cols = ["ticker", "datum", "typ", "confidence", "begruendung",
                "prompt_version", "regime_bei_empfehlung"]
        vals = [ticker, _now(), typ, confidence, begruendung,
                prompt_version, regime_bei_empfehlung]
        for k, v in extra.items():
            cols.append(k)
            vals.append(v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False))
        ph = ",".join("?" for _ in cols)
        cur = c.execute(
            f"INSERT INTO empfehlungen ({','.join(cols)}) VALUES ({ph})", vals
        )
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def get_last_recommendation(ticker):
    c = _conn()
    try:
        row = c.execute(
            """SELECT * FROM empfehlungen WHERE ticker=?
               ORDER BY datum DESC LIMIT 1""", (ticker,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def get_recommendations(limit=50):
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM empfehlungen ORDER BY datum DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def update_recommendation(rec_id, **fields):
    c = _conn()
    try:
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(v if not isinstance(v, (dict, list))
                        else json.dumps(v, ensure_ascii=False))
        vals.append(rec_id)
        c.execute(
            f"UPDATE empfehlungen SET {','.join(cols)} WHERE id=?", vals
        )
        c.commit()
    finally:
        c.close()


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------
def add_event(ticker, datum, event_typ, bmo_amc=None):
    c = _conn()
    try:
        c.execute(
            """INSERT OR IGNORE INTO events (ticker, datum, event_typ, bmo_amc)
               VALUES (?,?,?,?)""", (ticker, datum, event_typ, bmo_amc)
        )
        c.commit()
    finally:
        c.close()


def get_events(processed=None, days_ahead=None):
    c = _conn()
    try:
        q = "SELECT * FROM events"
        where = []
        vals = []
        if processed is not None:
            where.append("processed=?")
            vals.append(1 if processed else 0)
        if days_ahead is not None:
            from datetime import timedelta
            lo = date.today().isoformat()
            hi = (date.today() + timedelta(days=days_ahead)).isoformat()
            where.append("datum BETWEEN ? AND ?")
            vals.append(lo)
            vals.append(hi)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY datum"
        rows = c.execute(q, vals).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def mark_event_processed(event_id):
    c = _conn()
    try:
        c.execute("UPDATE events SET processed=1 WHERE id=?", (event_id,))
        c.commit()
    finally:
        c.close()


# --------------------------------------------------------------------------
# News-Dedup (E8)
# --------------------------------------------------------------------------
def news_seen_add(hash_, ticker, datum=None):
    c = _conn()
    try:
        c.execute(
            "INSERT OR IGNORE INTO news_gesehen (hash, ticker, datum) VALUES (?,?,?)",
            (hash_, ticker, datum or _now()),
        )
        c.commit()
    finally:
        c.close()


def news_seen_exists(hash_):
    c = _conn()
    try:
        row = c.execute(
            "SELECT 1 FROM news_gesehen WHERE hash=?", (hash_,)
        ).fetchone()
        return row is not None
    finally:
        c.close()


def news_seen_recent(ticker, days=7):
    """Hashes der letzten `days` Tage fuer Jaccard-Aehnlichkeits-Check (E8)."""
    c = _conn()
    try:
        from datetime import timedelta
        lo = (date.today() - timedelta(days=days)).isoformat()
        rows = c.execute(
            "SELECT hash FROM news_gesehen WHERE ticker=? AND datum>=?",
            (ticker, lo),
        ).fetchall()
        return [r["hash"] for r in rows]
    finally:
        c.close()


def purge_old_news(days=30):
    c = _conn()
    try:
        from datetime import timedelta
        lo = (date.today() - timedelta(days=days)).isoformat()
        c.execute("DELETE FROM news_gesehen WHERE datum<?", (lo,))
        c.commit()
    finally:
        c.close()


# --------------------------------------------------------------------------
# Telegram-Log
# --------------------------------------------------------------------------
def log_telegram(ziel, text):
    c = _conn()
    try:
        c.execute(
            "INSERT INTO telegram_log (zeit, ziel, text) VALUES (?,?,?)",
            (_now(), ziel, text),
        )
        c.commit()
    finally:
        c.close()


# --------------------------------------------------------------------------
# Papier-Ausfuehrungsschicht (v5) — Konto, Bestand, Orders
#
# Strikt getrennt von `positionen`: das hier ist die Sandbox, dort steht das
# real gefuehrte Portfolio. Siehe scripts/migrate_v5.py.
# --------------------------------------------------------------------------
def konto_init(startkapital, waehrung="USD", provider="paper"):
    """Legt das Papier-Konto an (nur wenn noch keins existiert)."""
    c = _conn()
    try:
        c.execute(
            """INSERT OR IGNORE INTO broker_konto
               (id, provider, startkapital, cash, waehrung, angelegt_am, aktualisiert_am)
               VALUES (1,?,?,?,?,?,?)""",
            (provider, startkapital, startkapital, waehrung, _now(), _now()),
        )
        c.commit()
    finally:
        c.close()


def konto_get():
    c = _conn()
    try:
        row = c.execute("SELECT * FROM broker_konto WHERE id=1").fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def konto_set_cash(cash):
    c = _conn()
    try:
        c.execute(
            "UPDATE broker_konto SET cash=?, aktualisiert_am=? WHERE id=1",
            (cash, _now()),
        )
        c.commit()
    finally:
        c.close()


def konto_reset():
    """Setzt die komplette Sandbox zurueck (Konto, Bestand, Orders)."""
    c = _conn()
    try:
        c.execute("DELETE FROM orders")
        c.execute("DELETE FROM paper_bestand")
        c.execute("DELETE FROM broker_konto")
        c.commit()
    finally:
        c.close()


def bestand_get(ticker):
    c = _conn()
    try:
        row = c.execute(
            "SELECT * FROM paper_bestand WHERE ticker=?", (ticker,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def bestand_all():
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM paper_bestand WHERE stueckzahl > 0 ORDER BY ticker"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def bestand_upsert(ticker, stueckzahl, durchschnittskurs):
    c = _conn()
    try:
        c.execute(
            """INSERT INTO paper_bestand (ticker, stueckzahl, durchschnittskurs,
                                          aktualisiert_am)
               VALUES (?,?,?,?)
               ON CONFLICT(ticker) DO UPDATE SET
                   stueckzahl=excluded.stueckzahl,
                   durchschnittskurs=excluded.durchschnittskurs,
                   aktualisiert_am=excluded.aktualisiert_am""",
            (ticker, stueckzahl, durchschnittskurs, _now()),
        )
        c.commit()
    finally:
        c.close()


def order_add(ticker, seite, stueckzahl, order_typ="market", provider="paper",
              limit_kurs=None, status="angenommen", ausgefuehrt_kurs=None,
              ablehnungsgrund=None, guardrail=None, rec_id=None,
              broker_order_id=None):
    """Protokolliert eine Order — auch eine abgelehnte. Liefert die id.

    Abgelehnte Orders werden bewusst gespeichert: der Nachweis, dass ein
    Guardrail gegriffen hat, ist genauso wertvoll wie der Trade selbst.
    """
    c = _conn()
    try:
        cur = c.execute(
            """INSERT INTO orders
               (broker_order_id, provider, ticker, seite, stueckzahl, order_typ,
                limit_kurs, status, ausgefuehrt_kurs, ablehnungsgrund,
                guardrail_json, rec_id, erstellt_am, aktualisiert_am)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (broker_order_id, provider, ticker, seite, stueckzahl, order_typ,
             limit_kurs, status, ausgefuehrt_kurs, ablehnungsgrund,
             json.dumps(guardrail, ensure_ascii=False) if guardrail is not None else None,
             rec_id, _now(), _now()),
        )
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def order_update(order_id, **fields):
    c = _conn()
    try:
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(v if not isinstance(v, (dict, list))
                        else json.dumps(v, ensure_ascii=False))
        if not cols:
            return
        cols.append("aktualisiert_am=?")
        vals.append(_now())
        vals.append(order_id)
        c.execute(f"UPDATE orders SET {','.join(cols)} WHERE id=?", vals)
        c.commit()
    finally:
        c.close()


def orders_list(limit=50, ticker=None, status=None):
    c = _conn()
    try:
        q = "SELECT * FROM orders"
        where, vals = [], []
        if ticker:
            where.append("ticker=?")
            vals.append(ticker)
        if status:
            where.append("status=?")
            vals.append(status)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id DESC LIMIT ?"
        vals.append(limit)
        rows = c.execute(q, vals).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def _now():
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    init_database()
    print("DB initialisiert:", db_path())
