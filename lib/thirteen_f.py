"""thirteen_f.py — 13F-HR Holdings-Analyse fuer Smart-Money-Trigger.

Liest die juengste 13F-HR-Filing einer kuratierten Liste von Top-Fonds
(Berkshire, Bridgewater, Renaissance, Citadel, Two Sigma, Pershing
Square, ...) und aggregiert die Holdings (ein Issuer kann mehrfach
auftauchen, weil 13F-Berichte alle Sub-Fonds kombinieren).

Speichert pro Institution einen Snapshot unter
`data/13f_history/<cik>_<period>.json`. Beim Vergleich
aktueller vs. vorheriger Snapshot werden signifikante Aenderungen
detektiert:
  - NEUE POSITION: Issuer war im Vorquartal NICHT enthalten, jetzt schon
  - +5% AUFSTOCKUNG: Issuer hat Position um >= 5% vergroessert
  - -50% REDUKTION: Issuer hat Position um >= 50% verkleinert
  - KOMPLETT-VERKAUFT: Issuer hat Position vollstaendig abgestossen

Datenquelle: SEC EDGAR, gratis, quartalsweise. SEC verlangt User-Agent mit
Kontaktdaten (siehe sec_edgar.py fuer die Konfiguration).
"""
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import requests

from sec_edgar import (get_recent_submissions, fetch_filing_xml,
                        _cached_get_text, _rate_limit, _get_session,
                        _load_user_agent, BASE, ARCHIVES)

# Kuratierte Liste von Top-Fonds (CIK 10-stellig mit fuehrenden Nullen).
# Nik kann diese Liste in `data/tracked_institutions.json` erweitern.
DEFAULT_TRACKED_INSTITUTIONS = [
    {"name": "Berkshire Hathaway",     "cik": "0001067983"},
    {"name": "Bridgewater Associates", "cik": "0001350694"},
    {"name": "Renaissance Technologies","cik": "0001037389"},
    {"name": "Citadel Advisors",       "cik": "0001423053"},
    {"name": "Two Sigma Investments",  "cik": "0001437389"},
    {"name": "Pershing Square (Ackman)","cik": "0001336528"},
    {"name": "Tiger Global Management","cik": "0001167483"},
    {"name": "Coatue Management",      "cik": "0001532196"},
    {"name": "Vanguard Group",          "cik": "0000102909"},
    {"name": "BlackRock",              "cik": "0001364742"},
]

# Wo werden die Snapshots gespeichert
_SNAPSHOT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "13f_history")
)
os.makedirs(_SNAPSHOT_DIR, exist_ok=True)

# Ticker -> Issuer-Name Mapping (aus data/ticker_name_map.json)
_NAME_MAP_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "ticker_name_map.json")
)
_NAME_MAP_CACHE = None


def _load_name_map() -> dict:
    """Lade Issuer-Name -> Ticker Mapping (gelesen aus .json, gecached)."""
    global _NAME_MAP_CACHE
    if _NAME_MAP_CACHE is not None:
        return _NAME_MAP_CACHE
    if os.path.exists(_NAME_MAP_PATH):
        try:
            with open(_NAME_MAP_PATH, encoding="utf-8") as f:
                _NAME_MAP_CACHE = json.load(f)
        except Exception:
            _NAME_MAP_CACHE = {}
    else:
        _NAME_MAP_CACHE = {}
    return _NAME_MAP_CACHE


def _load_tracked_institutions() -> list:
    """Lade die Tracked-Institutions-Liste. Custom-Liste aus data/ hat Vorrang."""
    cfg_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "data", "tracked_institutions.json")
    )
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                custom = json.load(f)
                if isinstance(custom, list) and custom:
                    return custom
        except Exception:
            pass
    return DEFAULT_TRACKED_INSTITUTIONS


def _find_holdings_xml(cik: str, accession: str, primary_doc: str,
                       user_agent: str = None) -> Optional[str]:
    """Findet die XML-Datei mit den Holdings (informationTable) im Filing.

    Im neuen 13F-Format (X0202) sind Holdings NICHT in primary_doc.xml,
    sondern in einer separaten Datei (z.B. 56757.xml). Strategie:
      1. Versuche erst die bekannten Naming-Patterns
      2. Fallback: SGML-Index lesen und nach <informationTable> suchen
    """
    cik_int = int(cik)
    acc_clean = accession.replace("-", "")
    base = f"{ARCHIVES}/edgar/data/{cik_int}/{acc_clean}"

    # Bekannte Patterns
    candidates = []
    if primary_doc:
        candidates.append(primary_doc)
        cleaned = re.sub(r"^xsl[^/]+/", "", primary_doc).lstrip("/")
        if cleaned != primary_doc:
            candidates.append(cleaned)

    # SGML-Index lesen und nach <informationTable> suchen
    try:
        idx_text = _cached_get_text(
            f"{base}/", cache_key=f"13f_index_{accession}",
            ttl=86400 * 7, user_agent=user_agent,
        )
        if idx_text:
            for href in re.findall(r'href="([^"]*\.xml)"', idx_text):
                fname = href.rsplit("/", 1)[-1]
                if fname not in candidates:
                    candidates.append(fname)
    except Exception:
        pass

    # Versuche jede Kandidatin und pruefe, ob sie <informationTable> enthaelt
    for c in candidates:
        url = f"{base}/{c}"
        fname = c.replace("/", "_").replace(":", "")
        text = _cached_get_text(
            url, cache_key=f"13f_holdings_{accession}_{fname}",
            ttl=86400 * 7, user_agent=user_agent,
        )
        if text and "<informationTable" in text and not text.lstrip().startswith("<!DOCTYPE html"):
            return text
    return None


def parse_13f_holdings(xml_text: str) -> list:
    """Parsed das Holdings-XML einer 13F-HR.

    Liefert aggregierte Holdings pro Issuer: [{name, cusip, total_shares,
    total_value_usd, table_count}, ...]

    Behandelt XML-Namespaces robust: 13F-XML hat xmlns-Default, dadurch sind
    alle Child-Tags namespace-qualifiziert (z.B. {http://...}infoTable).
    Wir extrahieren den Namespace aus root.tag.
    """
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    # Namespace aus root.tag extrahieren (Format: {URI}localname)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag[1:].split("}", 1)[0]

    def _q(tagname):
        """Qualifiziere einen Tag-Namen mit dem Namespace."""
        return f"{{{ns}}}{tagname}" if ns else tagname

    def _findall(elem, tagname):
        return elem.findall(f".//{_q(tagname)}")

    def _findtext(elem, tagname):
        for child in _findall(elem, tagname):
            return (child.text or "").strip()
        return ""

    by_issuer = defaultdict(lambda: {
        "total_shares": 0, "total_value_usd": 0.0, "table_count": 0,
    })
    for it in _findall(root, "infoTable"):
        name = _findtext(it, "nameOfIssuer")
        cusip = _findtext(it, "cusip")
        klass = _findtext(it, "titleOfClass")
        if not name or not cusip:
            continue
        key = (name.upper(), cusip, klass.upper())
        try:
            # Im neuen 13F-Format (X0202) ist der Wert BEREITS in USD, nicht
            # mehr in Tausend. Wir verifizieren per Per-Share-Berechnung:
            # 227917808 sh x $289 = $65.9B (passt zu 65950296923 im XML)
            value = float(_findtext(it, "value") or 0)
        except ValueError:
            value = 0.0
        try:
            shares = float(_findtext(it, "sshPrnamt") or 0)
        except ValueError:
            shares = 0.0
        by_issuer[key]["total_shares"] += shares
        by_issuer[key]["total_value_usd"] += value
        by_issuer[key]["table_count"] += 1
    out = []
    for (name, cusip, klass), d in by_issuer.items():
        out.append({
            "name": name,
            "cusip": cusip,
            "title_of_class": klass,
            "total_shares": int(d["total_shares"]),
            "total_value_usd": round(d["total_value_usd"], 2),
            "table_count": d["table_count"],
        })
    out.sort(key=lambda x: -x["total_value_usd"])
    return out


def get_latest_13f_filing(cik: str, user_agent: str = None) -> Optional[dict]:
    """Findet die juengste 13F-HR-Filing einer Institution.

    Liefert {form, filing_date, accession_number, primary_document, period_of_report}.
    None wenn keine 13F gefunden.
    """
    subs = _cached_get_text(
        f"{BASE}/submissions/CIK{cik}.json",
        cache_key=f"submissions_{cik}",
        ttl=86400,
        user_agent=user_agent,
    )
    if not subs:
        return None
    import json
    data = json.loads(subs)
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    periods = recent.get("reportDate", [])
    for i, f in enumerate(forms):
        if f != "13F-HR":
            continue
        return {
            "form": f,
            "filing_date": dates[i],
            "accession_number": accs[i],
            "primary_document": primary_docs[i],
            "period_of_report": periods[i] if i < len(periods) else "",
        }
    return None


def fetch_13f_snapshot(cik: str, user_agent: str = None) -> Optional[dict]:
    """Hole die aggregierten Holdings der juengsten 13F-HR-Filing.

    Liefert dict mit institution_name, cik, period_of_report, filing_date,
    accession_number, holdings (Liste). None bei API-Fehler.
    """
    filing = get_latest_13f_filing(cik, user_agent=user_agent)
    if not filing:
        return None
    holdings_xml = _find_holdings_xml(cik, filing["accession_number"],
                                     filing["primary_document"],
                                     user_agent=user_agent)
    if not holdings_xml:
        return None
    holdings = parse_13f_holdings(holdings_xml)
    return {
        "cik": cik,
        "period_of_report": filing["period_of_report"],
        "filing_date": filing["filing_date"],
        "accession_number": filing["accession_number"],
        "holdings_count": len(holdings),
        "holdings": holdings,
    }


def save_snapshot(snapshot: dict) -> str:
    """Speichere einen Snapshot unter 13f_history/<cik>_<period>.json."""
    cik = snapshot["cik"]
    period = snapshot["period_of_report"].replace("-", "")
    path = os.path.join(_SNAPSHOT_DIR, f"{cik}_{period}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return path


def load_previous_snapshot(cik: str, current_period: str) -> Optional[dict]:
    """Laed den vorherigen Snapshot (Q-vs-Q-Vergleich)."""
    if not os.path.isdir(_SNAPSHOT_DIR):
        return None
    files = sorted(
        [f for f in os.listdir(_SNAPSHOT_DIR) if f.startswith(cik + "_")],
        reverse=True,
    )
    for f in files:
        # Pfad-Format: <cik>_<period>.json
        period_in_file = f.replace(cik + "_", "").replace(".json", "")
        if period_in_file != current_period.replace("-", ""):
            try:
                with open(os.path.join(_SNAPSHOT_DIR, f), encoding="utf-8") as fp:
                    return json.load(fp)
            except Exception:
                return None
    return None


def detect_changes(previous: dict, current: dict, threshold_pct: float = 5.0,
                    threshold_complete_sell: float = 50.0) -> list:
    """Vergleicht zwei Snapshots und liefert signifikante Aenderungen."""
    if not previous or not current:
        return []
    prev_by_cusip = {h["cusip"]: h for h in previous.get("holdings", [])}
    curr_by_cusip = {h["cusip"]: h for h in current.get("holdings", [])}
    changes = []
    # Neue Positionen
    for cusip, c in curr_by_cusip.items():
        if cusip not in prev_by_cusip:
            changes.append({
                "typ": "NEUE_POSITION",
                "name": c["name"],
                "cusip": cusip,
                "shares_neu": c["total_shares"],
                "value_neu_usd": c["total_value_usd"],
            })
    # Komplett-Verkäufe
    for cusip, p in prev_by_cusip.items():
        if cusip not in curr_by_cusip:
            changes.append({
                "typ": "KOMPLETT_VERKAUFT",
                "name": p["name"],
                "cusip": cusip,
                "shares_vorher": p["total_shares"],
                "value_vorher_usd": p["total_value_usd"],
            })
    # Aufstockungen / Reduktionen
    for cusip in set(prev_by_cusip) & set(curr_by_cusip):
        p = prev_by_cusip[cusip]
        c = curr_by_cusip[cusip]
        if p["total_shares"] == 0:
            continue
        delta_pct = (c["total_shares"] - p["total_shares"]) / p["total_shares"] * 100
        if delta_pct >= threshold_pct:
            changes.append({
                "typ": f"AUFSTOCKUNG_+{delta_pct:.1f}%",
                "name": c["name"],
                "cusip": cusip,
                "shares_vorher": p["total_shares"],
                "shares_neu": c["total_shares"],
                "delta_pct": round(delta_pct, 1),
                "value_neu_usd": c["total_value_usd"],
            })
        elif delta_pct <= -threshold_complete_sell:
            changes.append({
                "typ": f"REDUKTION_{delta_pct:.1f}%",
                "name": c["name"],
                "cusip": cusip,
                "shares_vorher": p["total_shares"],
                "shares_neu": c["total_shares"],
                "delta_pct": round(delta_pct, 1),
                "value_vorher_usd": p["total_value_usd"],
            })
    return changes


def thirteen_f_signal(ticker: str, tracked_only: list = None,
                     user_agent: str = None) -> dict:
    """Pruefe, ob eine der tracked Institutions den `ticker` haelt.

    Liefert: {signal: 'bullish' | 'neutral', holdings: [...], changes: [...]}
    """
    tracked = tracked_only or _load_tracked_institutions()
    if not tracked:
        return {"signal": "n/a", "details": "keine tracked institutions", "holdings": []}
    found_holdings = []
    all_changes = []
    for inst in tracked:
        try:
            snap = fetch_13f_snapshot(inst["cik"], user_agent=user_agent)
            if not snap:
                continue
            # Speichere Snapshot (fuer kuenftige Vergleiche)
            save_snapshot(snap)
            # Versuche Holdings fuer unseren Ticker zu finden
            ticker_holdings = _filter_by_ticker(snap["holdings"], ticker)
            for h in ticker_holdings:
                found_holdings.append({
                    "institution": inst["name"],
                    "cik": inst["cik"],
                    "period": snap["period_of_report"],
                    "shares": h["total_shares"],
                    "value_usd": h["total_value_usd"],
                })
            # Change-Detection
            prev = load_previous_snapshot(inst["cik"], snap["period_of_report"])
            if prev:
                changes = detect_changes(prev, snap)
                # Filtere auf Aenderungen, die unseren Ticker betreffen
                for ch in changes:
                    if _change_matches_ticker(ch, ticker):
                        all_changes.append({**ch, "institution": inst["name"]})
        except Exception as e:
            continue
    signal = "neutral"
    details = []
    if found_holdings:
        signal = "bullish"
        total_value = sum(h["value_usd"] for h in found_holdings)
        details.append(f"{len(found_holdings)} Holdings ueber {len(set(h['institution'] for h in found_holdings))} Institutionen, ${total_value:,.0f}")
    if all_changes:
        # Aenderungen sind bullisch wenn neue Position oder Aufstockung
        bullish_changes = [c for c in all_changes
                           if c["typ"].startswith(("NEUE_POSITION", "AUFSTOCKUNG"))]
        bearish_changes = [c for c in all_changes if "REDUKTION" in c["typ"] or "KOMPLETT" in c["typ"]]
        if bullish_changes:
            signal = "bullish"
            for c in bullish_changes:
                details.append(f"{c['institution']}: {c['typ']} - {c['name']}")
        if bearish_changes and not bullish_changes:
            signal = "bearish"
            for c in bearish_changes:
                details.append(f"{c['institution']}: {c['typ']} - {c['name']}")
    return {
        "signal": signal,
        "staerke": "stark" if len(found_holdings) >= 3 else "mittel" if found_holdings else "keine",
        "anzahl_holdings": len(found_holdings),
        "anzahl_aenderungen": len(all_changes),
        "holdings": found_holdings,
        "changes": all_changes,
        "details": "; ".join(details) if details else "kein 13F-Match",
    }


def _filter_by_ticker(holdings: list, ticker: str) -> list:
    """Filtert Holdings, deren Issuer-Name zum Ticker passt (fuzzy)."""
    out = []
    ticker_upper = ticker.upper()
    for h in holdings:
        if _ticker_in_name(h["name"], ticker_upper):
            out.append(h)
    return out


def _ticker_in_name(name: str, ticker: str) -> bool:
    """Prueft, ob der Ticker zum Issuer-Namen passt (Mapping-Tabelle)."""
    name_upper = name.upper().strip()
    ticker_upper = ticker.upper().strip()
    # 1) Direkter Substring-Match (funktioniert fuer "PDD HOLDINGS INC" -> PDD,
    #    "ALIBABA GROUP" -> nicht BABA, weil BABA nicht im Namen steht)
    if ticker_upper in name_upper:
        return True
    # 2) Mapping-Tabelle (Issuer-Name -> Ticker)
    name_map = _load_name_map().get("by_issuer", {})
    # Exakter Match
    if name_upper in name_map:
        return name_map[name_upper] == ticker_upper
    # Normalisierter Match (Punkte, Kommas, mehrfache Leerzeichen)
    name_normalized = re.sub(r"[.,]\s*", " ", name_upper).strip()
    name_normalized = re.sub(r"\s+", " ", name_normalized)
    for issuer, tk in name_map.items():
        issuer_normalized = re.sub(r"[.,]\s*", " ", issuer).strip()
        issuer_normalized = re.sub(r"\s+", " ", issuer_normalized)
        if name_normalized == issuer_normalized:
            return tk == ticker_upper
        # Substring-Match auf normalisierten Namen
        if issuer_normalized in name_normalized or name_normalized in issuer_normalized:
            if tk == ticker_upper:
                return True
    return False


def _change_matches_ticker(change: dict, ticker: str) -> bool:
    """Prueft, ob eine Change-Notification den Ticker betrifft."""
    if _ticker_in_name(change.get("name", ""), ticker.upper()):
        return True
    return False


# ----------------------------------------------------------------------
# Convenience: Snapshot + Signal in einem Aufruf
# ----------------------------------------------------------------------
def refresh_13f_snapshots(user_agent: str = None) -> dict:
    """Holt frische Snapshots fuer alle tracked Institutions, speichert sie,
    und liefert eine Change-Map pro Institution.

    Liefert: {cik: {name, current_period, change_count, changes}, ...}
    """
    tracked = _load_tracked_institutions()
    out = {}
    for inst in tracked:
        try:
            snap = fetch_13f_snapshot(inst["cik"], user_agent=user_agent)
            if not snap:
                continue
            save_snapshot(snap)
            prev = load_previous_snapshot(inst["cik"], snap["period_of_report"])
            changes = detect_changes(prev, snap) if prev else []
            out[inst["cik"]] = {
                "name": inst["name"],
                "current_period": snap["period_of_report"],
                "holdings_count": snap["holdings_count"],
                "previous_period": prev.get("period_of_report") if prev else None,
                "change_count": len(changes),
                "changes": changes,
            }
        except Exception:
            continue
    return out


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    if t == "refresh":
        print("=== 13F-Snapshots aktualisieren ===")
        result = refresh_13f_snapshots()
        for cik, data in result.items():
            print(f"  {data['name']} ({cik}): {data['holdings_count']} Holdings, "
                  f"{data['change_count']} Aenderungen "
                  f"(current={data['current_period']}, prev={data['previous_period']})")
    else:
        print(f"=== 13F-Signal fuer {t} ===")
        result = thirteen_f_signal(t)
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
