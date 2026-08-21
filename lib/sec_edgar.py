"""sec_edgar.py — SEC EDGAR Client (Form 4 Insider-Trading + 13F Institutional Holdings).

Zweck: Smart-Money-Signale fuer das Trigger-System (siehe investmentthese.md
Offener Punkt 9). Liefert:
  - Form 4 Insider-Transaktionen pro Ticker
  - Insider-Cluster-Detection: 3+ Insider-Kaeufe in 30 Tagen = bullishes Signal
  - 13F-Holdings einer kuratierten Liste von Track-Fonds (Werden geladen,
    geaendert werden kann die Liste in `data/tracked_institutions.json`)

Datenquellen (alle SEC EDGAR, gratis, offiziell):
  - Company-Tickers-Map: https://www.sec.gov/files/company_tickers.json
  - Submissions pro CIK: https://data.sec.gov/submissions/CIK<10-stellig>.json
  - Form 4 XML: pro Accession-Number unter
    https://www.sec.gov/Archives/edgar/data/<cik_int>/<acc_clean>/<doc>.xml
  - 13F-HR XML: aehnlich, aber pro Institution-CIK, nicht pro Ticker-CIK.

SEC Rate-Limit: 10 Requests/Sekunde. Wir nutzen 0.15s Delay (= max ~6 Req/s)
plus konfigurierbares Caching (1 Tag fuer Submissions, 7 Tage fuer Tickers-Map,
30 Tage fuer historische Filings).

WICHTIG: SEC verlangt einen User-Agent mit Kontaktdaten. In
config.json unter `sec.user_agent` setzen, sonst faellt die Library auf einen
Platzhalter zurueck und SEC kann den Account sperren.
"""
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

# Defaults
DEFAULT_USER_AGENT = "Portfoliobot your-email@example.com"  # bitte in config.json ueberschreiben
RATE_LIMIT_DELAY = 0.15  # 6 Req/s, halbwegs sicher
BASE = "https://data.sec.gov"
ARCHIVES = "https://www.sec.gov/Archives"

# Cache-Verzeichnis (eine Ebene ueber lib/)
_CACHE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "sec_cache"))
os.makedirs(_CACHE_DIR, exist_ok=True)

# Session (Connection-Pool + persistente User-Agent-Header)
_session: Optional[requests.Session] = None
_last_request = 0.0


def _get_session(user_agent: str = None) -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
    ua = user_agent or _load_user_agent()
    # Wichtig: KEIN "Host"-Header setzen, das wird vom Session-Manager
    # automatisch gemacht. Ein explizit gesetzter "Host" auf einen anderen
    # Domainnamen fuehrt zu 4xx-Antworten.
    _session.headers.update({
        "User-Agent": ua,
        "Accept-Encoding": "gzip, deflate",
    })
    return _session


def _load_user_agent() -> str:
    """Laed User-Agent aus config.json. Fallback: Platzhalter."""
    try:
        from paths import CONFIG_PATH
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg.get("sec", {}).get("user_agent", DEFAULT_USER_AGENT)
    except Exception:
        return DEFAULT_USER_AGENT


def _rate_limit():
    global _last_request
    elapsed = time.time() - _last_request
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)
    _last_request = time.time()


def _cache_path(key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return os.path.join(_CACHE_DIR, safe + ".json")


def _cached_get_json(url: str, cache_key: str = None, ttl: int = 86400,
                     user_agent: str = None) -> Optional[dict]:
    """GET mit File-Cache (TTL in Sekunden) und Rate-Limit."""
    if cache_key is None:
        cache_key = url
    path = _cache_path(cache_key)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < ttl:
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    _rate_limit()
    try:
        r = _get_session(user_agent).get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        # Wenn ein Cache-Eintrag existiert (auch abgelaufen), nimm den als Fallback
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass
    return data


def _cached_get_text(url: str, cache_key: str = None, ttl: int = 86400,
                     user_agent: str = None) -> Optional[str]:
    """GET mit File-Cache fuer Text/XML-Antworten."""
    if cache_key is None:
        cache_key = url
    path = _cache_path(cache_key)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < ttl:
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
    _rate_limit()
    try:
        r = _get_session(user_agent).get(url, timeout=15)
        r.raise_for_status()
        text = r.text
    except Exception:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return None
        return None
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    return text


# ----------------------------------------------------------------------
# Ticker -> CIK Mapping
# ----------------------------------------------------------------------
def get_cik(ticker: str, user_agent: str = None) -> Optional[str]:
    """Mappe Ticker zu 10-stelliger CIK (mit fuehrenden Nullen). None wenn nicht gefunden."""
    data = _cached_get_json(
        f"{BASE}/submissions/CIK{ticker}.json" if False else "https://www.sec.gov/files/company_tickers.json",
        cache_key="company_tickers",
        ttl=86400 * 7,
        user_agent=user_agent,
    )
    if not data:
        return None
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker.upper():
            return str(entry["cik_str"]).zfill(10)
    return None


def get_company_name(ticker: str, user_agent: str = None) -> Optional[str]:
    """Hole den vollen Firmennamen zu einem Ticker."""
    data = _cached_get_json(
        "https://www.sec.gov/files/company_tickers.json",
        cache_key="company_tickers",
        ttl=86400 * 7,
        user_agent=user_agent,
    )
    if not data:
        return None
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker.upper():
            return entry.get("title")
    return None


# ----------------------------------------------------------------------
# Submissions (Form-Liste pro Ticker)
# ----------------------------------------------------------------------
def get_recent_submissions(ticker: str, user_agent: str = None) -> Optional[dict]:
    """Hole die 'recent'-Filings-Liste eines Tickers. Cache 1 Tag."""
    cik = get_cik(ticker, user_agent=user_agent)
    if not cik:
        return None
    return _cached_get_json(
        f"{BASE}/submissions/CIK{cik}.json",
        cache_key=f"submissions_{cik}",
        ttl=86400,
        user_agent=user_agent,
    )


def get_filings_of_type(ticker: str, form_types: list, days_back: int = 90,
                        user_agent: str = None) -> list:
    """Liste der Filings eines Tickers in den letzten `days_back` Tagen,
    gefiltert auf form_types (z.B. ['4', '4/A'] oder ['13F-HR']).
    Liefert Liste von {form, filing_date, accession_number, primary_document}.
    """
    subs = get_recent_submissions(ticker, user_agent=user_agent)
    if not subs:
        return []
    recent = subs.get("filings", {}).get("recent", {})
    if not recent:
        return []
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).date()
    out = []
    for i, f in enumerate(forms):
        if not any(f == ft or f.startswith(ft + "/") for ft in form_types):
            continue
        try:
            d = datetime.strptime(dates[i], "%Y-%m-%d").date()
        except Exception:
            continue
        if d < cutoff:
            continue
        out.append({
            "form": f,
            "filing_date": dates[i],
            "accession_number": accs[i],
            "primary_document": primary_docs[i],
        })
    return out


# ----------------------------------------------------------------------
# Form 4 (Insider-Trading) Parsing
# ----------------------------------------------------------------------
# Transaktions-Codes aus Form 4 (relevant fuer uns: P=Purchase, S=Sale)
TRANSACTION_CODES = {
    "P": "Open-market purchase (Kauf)",
    "S": "Open-market sale (Verkauf)",
    "A": "Grant/Award",
    "D": "Sale to issuer",
    "F": "Payment of exercise price",
    "I": "Discretionary transaction",
    "M": "Option exercise",
    "C": "Conversion of derivative",
    "E": "Expiration of derivative",
    "H": "Expiration of short position",
    "O": "Other",
    "X": "Exercise of derivative",
    "G": "Gift",
    "W": "Will/estate",
    "J": "Other (W-2 plan)",
    "L": "Small acquisition",
    "K": "Equity swap",
    "U": "Disposition due to tender",
    "T": "Derivative (other)",
    "V": "Conversion",
    "Z": "Deposit/withdrawal",
}


def fetch_filing_xml(accession: str, primary_doc: str, cik: str,
                       filer_label: str = "filing", hint: str = "",
                       user_agent: str = None) -> Optional[str]:
    """Hole das ROHE XML einer beliebigen SEC-Filing-Form. Cache 7 Tage.

    Wichtig: SEC legt zwei Versionen ab -- eine HTML-Version (gerendert, mit
    `xslF345X06/`-Praefix bei Form 4 bzw. `xslForm13F_X02/` bei 13F) und eine
    rohe XML-Version (ohne Praefix). Wir nutzen die rohe XML.

    Strategie:
      1. Versuche zuerst den uebergebenen primary_doc (oft schon richtig)
      2. Falls der Pfad mit einem XSL-Praefix beginnt, ersetze diesen Teil
      3. Falls der direkte Fetch fehlschlaegt, hole den SGML-Index der
         Accession und suche dort nach dem ersten XML-File mit `hint` im Namen
         (z.B. "form4" oder "13F").
    """
    if not cik:
        return None
    cik_int = int(cik)
    acc_clean = accession.replace("-", "")
    base = f"{ARCHIVES}/edgar/data/{cik_int}/{acc_clean}"
    candidates = []
    if primary_doc:
        candidates.append(primary_doc)
        # XSL-Praefixe generisch strippen (xslF345X06, xslForm13F_X02 etc.)
        import re as _re
        cleaned = _re.sub(r"^xsl[^/]+/", "", primary_doc).lstrip("/")
        if cleaned != primary_doc:
            candidates.append(cleaned)
    # SGML-Index lesen
    try:
        idx_text = _cached_get_text(
            f"{base}/", cache_key=f"{filer_label}_index_{accession}",
            ttl=86400 * 7, user_agent=user_agent,
        )
        if idx_text:
            import re as _re
            for href in _re.findall(r'href="([^"]*\.xml)"', idx_text):
                fname = href.rsplit("/", 1)[-1]
                if (not hint or hint.lower() in fname.lower()) and fname not in candidates:
                    candidates.append(fname)
    except Exception:
        pass
    for c in candidates:
        url = f"{base}/{c}"
        # Cache-Key mit Dateinamen, damit HTML- und XML-Version nicht
        # denselben Cache-Slot belegen.
        fname = c.replace("/", "_").replace(":", "")
        text = _cached_get_text(
            url, cache_key=f"{filer_label}_{accession}_{fname}",
            ttl=86400 * 7, user_agent=user_agent,
        )
        if text and not text.lstrip().startswith("<!DOCTYPE html"):
            return text
    return None


def fetch_form4_xml(ticker: str, accession: str, primary_doc: str = None,
                    user_agent: str = None) -> Optional[str]:
    """Hole das rohe XML einer Form 4. Cache 7 Tage."""
    cik = get_cik(ticker, user_agent=user_agent)
    return fetch_filing_xml(accession, primary_doc or "", cik,
                            filer_label="form4", hint="form4",
                            user_agent=user_agent)


def parse_form4(xml_text: str) -> dict:
    """Parsed ein Form 4 XML und liefert Insider-Name, Transaktionen, Holdings.

    Liefert:
      {
        "period_of_report": "2026-08-10",
        "issuer": {"cik": "...", "name": "...", "ticker": "..."},
        "reporting_owner": {"cik": "...", "name": "...", "is_director": bool,
                            "is_officer": bool, "officer_title": "..."},
        "transactions": [
            {"date": "2026-08-08", "code": "P", "shares": 1000.0, "price": 25.5,
             "owned_after": 5000.0, "direct_indirect": "D"},
            ...
        ]
      }
    """
    if not xml_text:
        return {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    out = {
        "period_of_report": _text(root, "periodOfReport"),
        "issuer": {
            "cik": _text(root, "issuer/issuerCik"),
            "name": _text(root, "issuer/issuerName"),
            "ticker": _text(root, "issuer/issuerTradingSymbol"),
        },
        "reporting_owner": _parse_reporting_owner(root),
        "transactions": [],
    }
    # Non-derivative Transaktionen (normale Aktien)
    for tx in root.findall(".//nonDerivativeTransaction"):
        t = _parse_transaction(tx)
        if t:
            out["transactions"].append(t)
    # Derivative Transaktionen (Optionen etc.) -- hier weniger relevant
    # fuer Insider-Cluster-Detection, daher nur optional
    for tx in root.findall(".//derivativeTransaction"):
        t = _parse_transaction(tx, derivative=True)
        if t:
            out["transactions"].append(t)
    return out


def _parse_reporting_owner(root) -> dict:
    ro = root.find("reportingOwner")
    if ro is None:
        return {}
    rel = ro.find("reportingOwnerRelationship")
    return {
        "cik": _text(ro, "reportingOwnerId/rptOwnerCik"),
        "name": _text(ro, "reportingOwnerId/rptOwnerName"),
        "is_director": _text(rel, "isDirector") == "1",
        "is_officer": _text(rel, "isOfficer") == "1",
        "is_ten_percent": _text(rel, "isTenPercentOwner") == "1",
        "officer_title": _text(rel, "officerTitle"),
    }


def _parse_transaction(tx, derivative: bool = False) -> Optional[dict]:
    code = _text(tx, "transactionCoding/transactionCode")
    if not code:
        return None
    try:
        shares = float(_text(tx, "transactionAmounts/transactionShares/value") or 0)
        price = float(_text(tx, "transactionAmounts/transactionPricePerShare/value") or 0)
    except ValueError:
        return None
    return {
        "date": _text(tx, "transactionDate/value"),
        "code": code,
        "code_bedeutung": TRANSACTION_CODES.get(code, "Unknown"),
        "shares": shares,
        "price_per_share": price,
        "owned_after": _try_float(_text(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
        "direct_indirect": _text(tx, "ownershipNature/directOrIndirectOwnership/value") or "D",
        "is_derivative": derivative,
    }


def _try_float(s):
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _text(elem, path: str) -> str:
    """Hole .text eines Sub-Elements per slash-getrenntem Pfad."""
    if elem is None:
        return ""
    for part in path.split("/"):
        elem = elem.find(part)
        if elem is None:
            return ""
    return (elem.text or "").strip()


# ----------------------------------------------------------------------
# Insider-Cluster-Detection
# ----------------------------------------------------------------------
def get_form4_transactions(ticker: str, days_back: int = 90,
                          include_derivatives: bool = False,
                          user_agent: str = None) -> list:
    """Hole ALLE Form-4-Transaktionen eines Tickers in den letzten `days_back` Tagen.

    Liefert flache Liste von Transaktions-Dicts (plus owner-Info als Prefix).
    Cache pro Accession-Number, 7 Tage.
    """
    filings = get_filings_of_type(ticker, ["4", "4/A"], days_back=days_back,
                                  user_agent=user_agent)
    out = []
    for f in filings:
        xml = fetch_form4_xml(ticker, f["accession_number"], f["primary_document"],
                              user_agent=user_agent)
        parsed = parse_form4(xml)
        if not parsed:
            continue
        owner = parsed.get("reporting_owner", {})
        owner_name = owner.get("name", "")
        role = _owner_role(owner)
        for tx in parsed.get("transactions", []):
            if tx.get("is_derivative") and not include_derivatives:
                continue
            out.append({
                "ticker": parsed.get("issuer", {}).get("ticker", ticker),
                "filing_date": f["filing_date"],
                "period_of_report": parsed.get("period_of_report"),
                "accession": f["accession_number"],
                "owner_name": owner_name,
                "owner_role": role,
                "transaction_date": tx["date"],
                "transaction_code": tx["code"],
                "transaction_bedeutung": tx["code_bedeutung"],
                "shares": tx["shares"],
                "price_per_share": tx["price_per_share"],
                "value_eur": _value(tx["shares"], tx["price_per_share"]),
                "owned_after": tx["owned_after"],
                "direct_indirect": tx["direct_indirect"],
            })
    # Sortiere nach Transaktions-Datum (neueste zuerst)
    out.sort(key=lambda x: (x["transaction_date"] or "", x["filing_date"] or ""),
             reverse=True)
    return out


def _owner_role(owner: dict) -> str:
    parts = []
    if owner.get("is_director"):
        parts.append("Director")
    if owner.get("is_officer"):
        parts.append(f"Officer ({owner.get('officer_title') or '?'})")
    if owner.get("is_ten_percent"):
        parts.append("10%-Owner")
    return ", ".join(parts) if parts else "Other Insider"


def _value(shares: float, price: float) -> float:
    if not shares or not price:
        return 0.0
    return round(shares * price, 2)


def insider_cluster_signal(ticker: str, days_back: int = 30,
                           min_buyers: int = 3, min_total_value: float = 100000,
                           user_agent: str = None) -> dict:
    """Detektiert Insider-Cluster-Kaeufe: 3+ verschiedene Insider kaufen
    in den letzten `days_back` Tagen denselben Ticker, mit Gesamtvolumen
    >= `min_total_value` (in USD).

    Liefert:
      {
        "signal": "bullish" | "neutral" | "n/a",
        "staerke": "stark" | "mittel" | "schwach",
        "anzahl_buyer": int,
        "anzahl_kauf_transaktionen": int,
        "gesamtwert_usd": float,
        "buyer": [{"name": ..., "role": ..., "shares": ..., "value": ...}, ...],
        "periode_tage": int,
        "details": str,
      }
    """
    txns = get_form4_transactions(ticker, days_back=days_back,
                                  user_agent=user_agent)
    # Filter: nur Open-Market-Kaeufe (Code "P")
    buys = [t for t in txns if t["transaction_code"] == "P" and t["shares"] > 0]
    if not buys:
        return {"signal": "neutral", "staerke": "keine",
                "anzahl_buyer": 0, "anzahl_kauf_transaktionen": 0,
                "gesamtwert_usd": 0.0, "buyer": [],
                "periode_tage": days_back,
                "details": "keine Open-Market-Kaeufe (Code P) im Fenster"}
    # Gruppiere nach Insider (ueber owner_name; ein Insider kann mehrere Käufe haben)
    by_buyer = {}
    for b in buys:
        name = b["owner_name"] or "?"
        if name not in by_buyer:
            by_buyer[name] = {
                "name": name, "role": b["owner_role"],
                "transactions": [], "total_shares": 0, "total_value": 0.0,
            }
        by_buyer[name]["transactions"].append(b)
        by_buyer[name]["total_shares"] += b["shares"]
        by_buyer[name]["total_value"] += b["value_eur"]
    buyer_list = sorted(by_buyer.values(), key=lambda x: -x["total_value"])
    n_buyers = len(buyer_list)
    total_value = sum(b["total_value"] for b in buyer_list)
    n_trans = len(buys)

    if n_buyers >= min_buyers and total_value >= min_total_value:
        # Staerke: mehr Buyer + höheres Volumen = stärker
        if n_buyers >= 5 or total_value >= 1_000_000:
            staerke = "stark"
        elif n_buyers >= 3 or total_value >= 250_000:
            staerke = "mittel"
        else:
            staerke = "schwach"
        return {
            "signal": "bullish",
            "staerke": staerke,
            "anzahl_buyer": n_buyers,
            "anzahl_kauf_transaktionen": n_trans,
            "gesamtwert_usd": round(total_value, 2),
            "buyer": buyer_list,
            "periode_tage": days_back,
            "details": f"{n_buyers} Insider kaufen, Gesamtvolumen ${total_value:,.0f}",
        }
    return {
        "signal": "neutral",
        "staerke": "schwach",
        "anzahl_buyer": n_buyers,
        "anzahl_kauf_transaktionen": n_trans,
        "gesamtwert_usd": round(total_value, 2),
        "buyer": buyer_list,
        "periode_tage": days_back,
        "details": f"nur {n_buyers} Buyer oder Volumen ${total_value:,.0f} unter Schwelle",
    }


# ----------------------------------------------------------------------
# Convenience: Eine Funktion fuer alles pro Ticker
# ----------------------------------------------------------------------
def smart_money_snapshot(ticker: str, days_back: int = 30,
                         user_agent: str = None) -> dict:
    """Hole alle Smart-Money-Signale fuer einen Ticker.

    Aktuell: Insider-Cluster-Signal. 13F folgt spaeter (siehe TODO).
    """
    insider = insider_cluster_signal(ticker, days_back=days_back,
                                     user_agent=user_agent)
    return {
        "ticker": ticker.upper(),
        "stand": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "insider_cluster": insider,
        "13f": {"status": "noch nicht implementiert (queue)"},
    }


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    print(f"=== Smart-Money-Snapshot fuer {t} ===")
    out = smart_money_snapshot(t)
    print(json.dumps(out, ensure_ascii=False, indent=2))
