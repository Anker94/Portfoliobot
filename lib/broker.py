"""Broker-Layer: Order-Ausfuehrung, provider-agnostisch — PAPIER-ONLY.

Provider (config/config.json -> broker.provider):
  - "paper"        : Offline-Simulator gegen die eigene DB. Kein Netzwerk,
                     kein Key, deterministisch. Default — analog zum
                     LLM-Provider "mock", damit die Pipeline offline
                     testbar bleibt.
  - "alpaca_paper" : Alpaca Paper-Trading-API. Echte Marktdaten, echte
                     Order-Semantik, fiktives Kapital.

HARTE GRENZE (nicht konfigurierbar)
-----------------------------------
Dieses Modul spricht ausschliesslich Papier-Endpunkte an. `assert_papier_only()`
prueft den Host gegen eine Allowlist und wirft `LiveTradingBlocked`, sobald eine
Live-URL konfiguriert ist. Das ist bewusst kein Schalter: der Uebergang zu echtem
Kapital ist eine Entscheidung, die ein Mensch an anderer Stelle trifft und nicht
durch Umkonfigurieren dieses Moduls passieren darf.

Der Kurs kommt IMMER aus lib/data_layer (yfinance) oder wird explizit
uebergeben. Kein Kurs -> Order wird abgelehnt. Es wird nie ein Preis geraten
(Kernregel aus SKILL.md, hier auf die Ausfuehrung uebertragen).
"""
import os
import sys

try:
    from paths import load_config
except Exception:  # standalone
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    def load_config():
        return {}

import db


class LiveTradingBlocked(RuntimeError):
    """Konfiguration zeigt auf einen Live-Endpunkt. Wird immer geworfen."""


class BrokerFehler(RuntimeError):
    """Transport-/API-Fehler des Brokers."""


# Allowlist der zulaessigen Hosts. Alles andere ist gesperrt — auch Hosts,
# die harmlos aussehen. Positivliste, keine Negativliste.
PAPIER_HOSTS = {
    "paper-api.alpaca.markets",
}

# Bekannte Live-Hosts, nur fuer eine sprechende Fehlermeldung.
_LIVE_HOSTS = {
    "api.alpaca.markets": "Alpaca Live",
    "api.kraken.com": "Kraken Live",
    "api.binance.com": "Binance Live",
    "api-fxtrade.oanda.com": "OANDA Live",
}

ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"

STARTKAPITAL_DEFAULT = 100000.0


def _host(url):
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "").lower()


def assert_papier_only(base_url):
    """Wirft LiveTradingBlocked, wenn base_url kein Papier-Endpunkt ist."""
    h = _host(base_url)
    if h in PAPIER_HOSTS:
        return
    name = _LIVE_HOSTS.get(h)
    if name:
        raise LiveTradingBlocked(
            f"'{h}' ist der Live-Endpunkt von {name}. Dieses Modul fuehrt "
            f"ausschliesslich Papier-Orders aus."
        )
    raise LiveTradingBlocked(
        f"'{h or base_url}' steht nicht auf der Papier-Allowlist "
        f"({', '.join(sorted(PAPIER_HOSTS))}). Order abgebrochen."
    )


def broker_config():
    """(provider, base_url, key_id, secret, startkapital, waehrung)."""
    cfg = load_config().get("broker", {}) or {}
    provider = cfg.get("provider", "paper")
    startkapital = float(cfg.get("startkapital", STARTKAPITAL_DEFAULT))
    waehrung = cfg.get("waehrung", "USD")

    if provider == "alpaca_paper":
        # base_url wird NICHT aus der Config uebernommen, sondern gesetzt.
        # Eine konfigurierbare URL waere genau das Schlupfloch, das die
        # Papier-Grenze aufweichen wuerde.
        base_url = ALPACA_PAPER_URL
        key_id = cfg.get("key_id") or os.environ.get("ALPACA_API_KEY_ID")
        secret = cfg.get("api_secret") or os.environ.get("ALPACA_API_SECRET_KEY")
    else:
        base_url, key_id, secret = None, None, None
    return provider, base_url, key_id, secret, startkapital, waehrung


# --------------------------------------------------------------------------
# Gemeinsame Schnittstelle
# --------------------------------------------------------------------------
def konto():
    """Kontostand. Liefert dict oder wirft BrokerFehler/LiveTradingBlocked."""
    provider, base_url, key_id, secret, startkapital, waehrung = broker_config()
    if provider == "alpaca_paper":
        assert_papier_only(base_url)
        d = _alpaca_get("/v2/account", base_url, key_id, secret)
        return {
            "provider": provider,
            "modus": "papier",
            "cash": float(d.get("cash", 0)),
            "depotwert": float(d.get("equity", 0)),
            "kaufkraft": float(d.get("buying_power", 0)),
            "waehrung": d.get("currency", waehrung),
        }
    # paper (offline)
    db.konto_init(startkapital, waehrung, provider="paper")
    k = db.konto_get()
    bestand = db.bestand_all()
    wert = 0.0
    unbewertet = []
    for b in bestand:
        kurs = _spot(b["ticker"])
        if kurs is None:
            unbewertet.append(b["ticker"])
            kurs = b["durchschnittskurs"] or 0.0
        wert += kurs * (b["stueckzahl"] or 0)
    out = {
        "provider": "paper",
        "modus": "papier (offline-simulator)",
        "cash": k["cash"],
        "depotwert": round(k["cash"] + wert, 2),
        "kaufkraft": k["cash"],
        "waehrung": k["waehrung"],
        "startkapital": k["startkapital"],
    }
    if unbewertet:
        # Ehrlich ausweisen statt still mit Einstandskurs weiterrechnen.
        out["hinweis"] = ("zu Einstandskursen bewertet (kein Live-Kurs): "
                          + ", ".join(unbewertet))
    return out


def bestand():
    """Offene Papier-Positionen als Liste von dicts."""
    provider, base_url, key_id, secret, _, _ = broker_config()
    if provider == "alpaca_paper":
        assert_papier_only(base_url)
        rows = _alpaca_get("/v2/positions", base_url, key_id, secret)
        return [{
            "ticker": r.get("symbol"),
            "stueckzahl": float(r.get("qty", 0)),
            "durchschnittskurs": float(r.get("avg_entry_price", 0)),
            "marktwert": float(r.get("market_value", 0)),
            "gv_pct": float(r.get("unrealized_plpc", 0)) * 100,
        } for r in rows]
    return db.bestand_all()


def order(ticker, seite, stueckzahl, order_typ="market", limit_kurs=None,
          kurs=None, rec_id=None, guardrail=None):
    """Gibt eine Papier-Order auf und protokolliert sie in `orders`.

    seite: 'kaufen' | 'verkaufen'. `kurs` uebersteuert den Marktkurs
    (fuer deterministische Tests). Liefert das Order-dict aus der DB.

    Der Aufrufer ist dafuer verantwortlich, vorher lib/order_guard.pruefe()
    laufen zu lassen — scripts/broker_commands.py tut das.
    """
    ticker = ticker.upper()
    seite = seite.lower()
    if seite not in ("kaufen", "verkaufen"):
        raise ValueError("seite muss 'kaufen' oder 'verkaufen' sein")
    if not stueckzahl or stueckzahl <= 0:
        return _ablehnen(ticker, seite, stueckzahl, order_typ, limit_kurs,
                         "Stueckzahl muss > 0 sein", guardrail, rec_id)

    provider, base_url, key_id, secret, _, _ = broker_config()

    if provider == "alpaca_paper":
        assert_papier_only(base_url)
        body = {
            "symbol": ticker,
            "qty": str(stueckzahl),
            "side": "buy" if seite == "kaufen" else "sell",
            "type": order_typ,
            "time_in_force": "day",
        }
        if order_typ == "limit":
            body["limit_price"] = str(limit_kurs)
        try:
            d = _alpaca_post("/v2/orders", body, base_url, key_id, secret)
        except BrokerFehler as e:
            return _ablehnen(ticker, seite, stueckzahl, order_typ, limit_kurs,
                             str(e), guardrail, rec_id, provider=provider)
        oid = db.order_add(ticker, seite, stueckzahl, order_typ,
                           provider=provider, limit_kurs=limit_kurs,
                           status="angenommen", guardrail=guardrail,
                           rec_id=rec_id, broker_order_id=d.get("id"))
        return db.orders_list(limit=1, ticker=ticker)[0] if oid else None

    # ---- Offline-Simulator ----
    ausfuehrungskurs = kurs if kurs is not None else _spot(ticker)
    if ausfuehrungskurs is None:
        return _ablehnen(ticker, seite, stueckzahl, order_typ, limit_kurs,
                         "kein Kurs verfuegbar (API-Ausfall) — es wird kein "
                         "Preis geschaetzt", guardrail, rec_id)
    if order_typ == "limit" and limit_kurs is not None:
        # Konservativ: Limit fuellt nur, wenn der Markt es hergibt.
        if seite == "kaufen" and ausfuehrungskurs > limit_kurs:
            return _ablehnen(ticker, seite, stueckzahl, order_typ, limit_kurs,
                             f"Limit {limit_kurs} < Markt {ausfuehrungskurs:.2f} "
                             f"— nicht ausgefuehrt", guardrail, rec_id)
        if seite == "verkaufen" and ausfuehrungskurs < limit_kurs:
            return _ablehnen(ticker, seite, stueckzahl, order_typ, limit_kurs,
                             f"Limit {limit_kurs} > Markt {ausfuehrungskurs:.2f} "
                             f"— nicht ausgefuehrt", guardrail, rec_id)
        ausfuehrungskurs = float(limit_kurs)

    provider_cfg, _, _, _, startkapital, waehrung = broker_config()
    db.konto_init(startkapital, waehrung, provider="paper")
    k = db.konto_get()
    pos = db.bestand_get(ticker)
    volumen = ausfuehrungskurs * stueckzahl

    if seite == "kaufen":
        if volumen > k["cash"]:
            return _ablehnen(ticker, seite, stueckzahl, order_typ, limit_kurs,
                             f"Kaufkraft reicht nicht ({volumen:.2f} > "
                             f"{k['cash']:.2f} {k['waehrung']})",
                             guardrail, rec_id)
        alt_stk = (pos or {}).get("stueckzahl") or 0.0
        alt_kurs = (pos or {}).get("durchschnittskurs") or 0.0
        neu_stk = alt_stk + stueckzahl
        neu_kurs = (alt_stk * alt_kurs + volumen) / neu_stk
        db.bestand_upsert(ticker, neu_stk, round(neu_kurs, 4))
        db.konto_set_cash(round(k["cash"] - volumen, 2))
    else:
        alt_stk = (pos or {}).get("stueckzahl") or 0.0
        if stueckzahl > alt_stk:
            return _ablehnen(ticker, seite, stueckzahl, order_typ, limit_kurs,
                             f"Leerverkauf gesperrt: Bestand {alt_stk:g} < "
                             f"{stueckzahl:g}", guardrail, rec_id)
        db.bestand_upsert(ticker, alt_stk - stueckzahl,
                          (pos or {}).get("durchschnittskurs") or 0.0)
        db.konto_set_cash(round(k["cash"] + volumen, 2))

    oid = db.order_add(ticker, seite, stueckzahl, order_typ, provider="paper",
                       limit_kurs=limit_kurs, status="ausgefuehrt",
                       ausgefuehrt_kurs=round(ausfuehrungskurs, 4),
                       guardrail=guardrail, rec_id=rec_id)
    return _order_by_id(oid)


def orders(limit=20, ticker=None):
    return db.orders_list(limit=limit, ticker=ticker)


# --------------------------------------------------------------------------
# Intern
# --------------------------------------------------------------------------
def _ablehnen(ticker, seite, stueckzahl, order_typ, limit_kurs, grund,
              guardrail, rec_id, provider="paper"):
    oid = db.order_add(ticker, seite, stueckzahl, order_typ, provider=provider,
                       limit_kurs=limit_kurs, status="abgelehnt",
                       ablehnungsgrund=grund, guardrail=guardrail, rec_id=rec_id)
    return _order_by_id(oid)


def _order_by_id(oid):
    for o in db.orders_list(limit=5):
        if o["id"] == oid:
            return o
    return None


def _spot(ticker):
    """Aktueller Kurs ueber den bestehenden Daten-Layer. None bei API-Ausfall."""
    try:
        from data_layer import get_performance
        return get_performance(ticker)
    except Exception:
        return None


def _alpaca_headers(key_id, secret):
    if not key_id or not secret:
        raise BrokerFehler(
            "ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY fehlen. Keys legt der "
            "Nutzer selbst im Alpaca-Dashboard an und traegt sie in "
            "config/config.json (broker.key_id/api_secret) oder in die "
            "Umgebung ein."
        )
    return {
        "APCA-API-KEY-ID": key_id,
        "APCA-API-SECRET-KEY": secret,
        "Content-Type": "application/json",
    }


def _alpaca_get(pfad, base_url, key_id, secret):
    assert_papier_only(base_url)
    import requests
    try:
        r = requests.get(base_url.rstrip("/") + pfad,
                         headers=_alpaca_headers(key_id, secret), timeout=30)
        r.raise_for_status()
        return r.json()
    except BrokerFehler:
        raise
    except Exception as e:
        raise BrokerFehler(f"Alpaca GET {pfad}: {type(e).__name__}: {e}")


def _alpaca_post(pfad, body, base_url, key_id, secret):
    assert_papier_only(base_url)
    import requests
    try:
        r = requests.post(base_url.rstrip("/") + pfad, json=body,
                          headers=_alpaca_headers(key_id, secret), timeout=30)
        if r.status_code >= 400:
            raise BrokerFehler(f"Alpaca {r.status_code}: {r.text[:300]}")
        return r.json()
    except BrokerFehler:
        raise
    except Exception as e:
        raise BrokerFehler(f"Alpaca POST {pfad}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print("Konto:", konto())
    print("Bestand:", bestand())
