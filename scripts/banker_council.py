"""banker_council.py — Code-Teil des "5-Banker"-Screenings (P6).

5 Rollenspiel-Prompts (Goldman-Sachs-Screener, Morgan-Stanley-DCF,
Bridgewater-Risiko, JPMorgan-Earnings, BlackRock-Portfoliokonstruktion).
Woertlich befolgt wuerde ein LLM dabei ueberzeugend klingende, aber frei
erfundene Kennzahlen produzieren. Dieses Script liefert stattdessen die
FAKTEN aus echten yfinance-Daten -- Kernregel des Projekts ("Code liefert
Fakten, LLM liefert Urteil") auf die 5-Varianten-Idee angewandt.

Generalisiert einen urspruenglichen Ad-hoc-Durchlauf zu einem wiederholbaren
Werkzeug, das jeder Nutzer fuer eigene Watchlist-Screenings einsetzen kann.

Was es NICHT tut: keine Kauf-/Verkaufsempfehlung, keine "5 Banker sitzen
zusammen"-Erzaehlung. Das ist der naechste Schritt -- siehe
prompts/P6_banker_council.txt, das die JSON-Ausgabe dieses Scripts als
Faktenbasis fuer genau diese Synthese nimmt (LLM-Urteil, nicht Code).

Aufruf:
    python scripts/banker_council.py                    # volles Universum
    python scripts/banker_council.py --top 20            # nur Top 20 zeigen
    python scripts/banker_council.py --min-analysten 10   # duennes Coverage raus
    python scripts/banker_council.py --refresh-ticker     # Ticker-Aufloesung neu pruefen
    python scripts/banker_council.py --no-smart-money     # 13F-Faktor abschalten (schneller)

Smart-Money-Faktor (seit 2026-08-17, auf Nik's Wunsch "extra Faktor"): nutzt
lib/thirteen_f.py (13F-Holdings der in data/tracked_institutions.json
gepflegten Fonds) als zusaetzlichen Score-Bonus. Ein Titel, den mehrere
getrackte Fonds halten, bekommt einen kleinen Bonus (max. +8 von ~100
Basis-Punkten) -- er soll die Bewertung tilten, nicht dominieren. Matching
ist Namens-basiert (13F-Filings haben CUSIP, keine Ticker) -- siehe
smart_money_lookup(), kann bei sehr unterschiedlichen Firmennamen daneben-
liegen; ein Miss bedeutet nur "kein Bonus", kein falsches Ticker-Risiko wie
bei der eigentlichen Ticker-Aufloesung.

Situational Awareness LP (Leopold Aschenbrenner) wird seit 2026-08-17
mitgetrackt, zaehlt aber seit 2026-08-18 NICHT mehr in den Score-Bonus
(Nik-Entscheidung) -- der Fonds ist im Juli 2026 nach 4x-Hebel kollabiert
und musste sein gesamtes oeffentliches Aktienportfolio notverkaufen; der
Q2-13F-Snapshot zeigt vermutlich laengst abgestossene Positionen. Bleibt
als informatives Feld (smart_money_salp) im Report, siehe score_one()
Kommentar fuer Details/Quellen.

Universum: alle *.md in wissensdatenbank/aktien/ (ausser README.md) -- wird
bei jedem Lauf neu eingelesen, damit neu hinzugefuegte Titel automatisch
mitlaufen. Ticker-Aufloesung (Boersen-Suffixe) wird in data/ticker_resolution.json
zwischengespeichert; neue/unbekannte Kuerzel werden per Kandidaten-Probe
aufgeloest und dort ergaenzt (--refresh-ticker prueft auch bestehende neu).

Bekannte Grenzen (bewusst nicht geloest, siehe uebergabe-5-banker-2026-08-17.md):
- DCF ist ein grobes 1-Pfad-Modell, bei ADRs mit Fremdwaehrungs-Bilanz
  (z.B. BABA/TSM/PDD/TCEHY) methodisch unbrauchbar (Waehrungs-Mismatch) und
  bei sehr niedrigem Beta systematisch zu hoch (Terminal-Value-Verzerrung).
  In der JSON-Ausgabe trotzdem enthalten, aber mit "dcf_warnung" geflaggt --
  wer die Zahl weiterverwendet, muss dieses Flag lesen.
- Sektor-Durchschnitte fuer KGV-Vergleiche werden NICHT live gezogen (keine
  Sektor-Datenbank angebunden) -- der Score nutzt stattdessen Analysten-
  Kurszielabstand und PEG, nicht KGV-vs-Sektor.
- Korrelationsmatrix ist optional (--correlation), weil sie fuer grosse
  Universen einen zusaetzlichen yf.download-Batch braucht (langsamer).
"""
import os
import re
import sys
import json
import time
import argparse
import glob as globmod
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from paths import konsole_utf8, DATA_DIR, load_config
konsole_utf8()

import yfinance as yf
from data_layer import get_info, get_stock_data, download_closes
from financials import get_financials
from thirteen_f import _load_tracked_institutions, fetch_13f_snapshot

WISSENSDATENBANK = os.path.normpath(os.path.join(_ROOT, "..", "..", "wissensdatenbank", "aktien"))
RESOLUTION_JSON = os.path.join(DATA_DIR, "ticker_resolution.json")
REPORT_JSON = os.path.join(DATA_DIR, "banker_council_latest.json")

RISK_FREE = 0.043
ERP = 0.05

# Kandidaten-Suffixe fuer neue/unbekannte Kuerzel, in Prioritaetsreihenfolge.
# Manuell bekannte Sonderfaelle (ADR/Primaerboerse weicht vom Kuerzel ab)
# zuerst -- reine Suffix-Rateverfahren schlagen bei sowas wie D4S/4DS fehl
# (siehe Postmortem in uebergabe-5-banker-2026-08-17.md), deshalb IMMER den
# zurueckgegebenen Firmennamen gegen den erwarteten pruefen (siehe validieren()).
SUFFIX_KANDIDATEN = ["", ".DE", ".F", ".L", ".CO", ".BR", ".AX", ".T", ".KS", ".HK", ".CN", ".NE",
                    ".TO", ".V", ".LS", ".MC", ".PA", ".MI", ".AS", ".SW", ".OL", ".ST", ".PA"]


def _split_ticker_for_suffixes(ticker: str) -> tuple[str, set[str]]:
    """Trennt bekannte Boersen-Suffixe von der Basis. Beispiele:
    "DR0.DE"  -> ("DR0", {".DE"})
    "006400.KS" -> ("006400", {".KS"})
    "NVDA"    -> ("NVDA", set())
    "BRK.B"   -> ("BRK.B", set())  # Klassen-Suffix ist KEIN Boersensuffix
    """
    if "." not in ticker:
        return ticker, set()
    base, _, last_suffix = ticker.rpartition(".")
    last_suffix = "." + last_suffix
    skip = {last_suffix.upper()} if last_suffix.upper() in {s.upper() for s in SUFFIX_KANDIDATEN if s} else set()
    if skip:
        return base, skip
    return ticker, set()


def discover_universe():
    """Liste aller Kurz-Ticker aus den Dateinamen in wissensdatenbank/aktien/."""
    keys = []
    for path in sorted(globmod.glob(os.path.join(WISSENSDATENBANK, "*.md"))):
        name = os.path.splitext(os.path.basename(path))[0]
        if name.upper() == "README":
            continue
        keys.append(name)
    return keys


def _expected_name_hint(path):
    """Erster '# ...'-Header der .md-Datei, fuer die Namens-Validierung."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("# "):
                    return line[2:].strip()
    except Exception:
        pass
    return None


def validieren(key, yf_name, expected_name):
    """Prueft, ob der yfinance-Name zum erwarteten Wiki-H1-Header passt.

    Erkennt 3 Klassen von Mismatches:
    - Vollstaendig falsche Company (z.B. GLEN.CN -> GLEN.L = Glencore statt
      Glenstar Minerals)
    - Korrektes Land/Boerse, aber falsches Wertpapier (z.B. ABL.TO ->
      ABL.DE = Abbott statt Ability Metals)
    - Konzern-Match aber falsches Ticker-Segment (selten)

    Logik: Tokenisiere expected_name, entferne Stopwords und kurze Tokens,
    pruefe ob mind. 1 signifikantes Token im normalisierten yf-Namen
    vorkommt. Bei Micro-Caps (1-2 Wort Company) reicht der Match auf
    einem signifikanten Token (>= 4 Zeichen).
    """
    if not yf_name or not expected_name:
        return True  # Nichts zu validieren
    yf_norm = _normalize_company_name(yf_name)
    exp_norm = _normalize_company_name(expected_name)
    if not exp_norm:
        return True
    # Signifikante Tokens (>= 4 Zeichen, keine Stopwords)
    STOP = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY",
            "LTD", "LIMITED", "PLC", "LLC", "LP", "NV", "AG", "SA", "GROUP",
            "HOLDINGS", "HLDGS", "HLDG", "THE", "AND", "FOR", "VON", "DER",
            "CLASS", "SERIES", "ETF", "TRUST", "FUND"}
    exp_tokens = [t for t in exp_norm.split() if len(t) >= 4 and t not in STOP]
    if not exp_tokens:
        # expected_name zu kurz fuer Token-Match (z.B. "Bitfarms Ltd")
        # Fallback: erster 4+ Zeichen Substring muss in yf_norm vorkommen
        return any(s[:4] in yf_norm for s in exp_norm.split() if len(s) >= 4)
    # Mindestens die Haelfte der signifikanten Tokens muss im yf-Namen vorkommen
    hits = sum(1 for t in exp_tokens if t in yf_norm)
    # Bei 1-2 Tokens: 1 Match reicht. Bei 3+: 50% reicht.
    threshold = 1 if len(exp_tokens) <= 2 else max(1, len(exp_tokens) // 2)
    return hits >= threshold


def resolve_ticker(key, known_symbol=None):
    """Findet einen funktionierenden yfinance-Ticker fuer `key`.
    Prueft zuerst known_symbol (Cache), sonst Kandidaten-Suffixe.
    Gibt (symbol, yf_name) oder (None, None).

    Wenn `key` bereits einen bekannten Boersensuffix traegt (z.B. "DR0.DE"),
    wird die Basis an die Suffix-Liste angehaengt -- nicht der volle Ticker.
    Sonst kam bei "DR0.DE" + ".F" = "DR0.DE.F" raus (kein Match).
    """
    base, skip_suffixes = _split_ticker_for_suffixes(key)
    # 1) Voller Ticker (inkl. Suffix) zuerst versuchen -- das ist bei "DR0.DE" etc.
    # bereits die kanonische yfinance-Form und liefert meistens direkt Daten.
    kandidaten = []
    if known_symbol:
        kandidaten.append(known_symbol)
    kandidaten.append(key)
    # 2) Basis + Suffix-Liste, aber schon vorhandene Suffixe auslassen.
    for s in SUFFIX_KANDIDATEN:
        if s.upper() in skip_suffixes:
            continue
        kandidaten.append(base + s)
    for cand in kandidaten:
        if not cand:
            continue
        try:
            h = yf.Ticker(cand).history(period="5d")
            if h is not None and not h.empty:
                info = get_info(cand)
                return cand, info.get("shortName")
        except Exception:
            continue
    return None, None


def load_resolution():
    if os.path.exists(RESOLUTION_JSON):
        try:
            with open(RESOLUTION_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"resolved": {}, "failed": {}}


def save_resolution(res):
    with open(RESOLUTION_JSON, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)


def ensure_resolution(keys, refresh=False):
    """Aktualisiert die Ticker-Aufloesung fuer alle `keys`. Neue Kuerzel werden
    aufgeloest und persistiert; bei --refresh-ticker werden auch bestehende
    neu geprueft (z.B. falls ein Ticker inzwischen delisted wurde, siehe
    SKHYY-Fall in uebergabe-5-banker-2026-08-17.md).

    validieren(): nach erfolgreichem resolve_ticker() wird der yfinance-Name
    gegen den Wiki-H1-Header abgeglichen. Bei Mismatch (z.B. GLEN.CN matched
    auf GLEN.L = Glencore statt Glenstar Minerals) wird der Ticker als
    failed markiert und der Lauf ueberspringt ihn -- siehe validieren()."""
    res = load_resolution()
    changed = False
    for key in keys:
        cached = res["resolved"].get(key)
        if cached and not refresh:
            continue
        sym, name = resolve_ticker(key, known_symbol=cached)
        if sym:
            # Wiki-H1 lesen, gegen yf-Namen validieren
            md_path = os.path.join(WISSENSDATENBANK, f"{key}.md")
            expected = _expected_name_hint(md_path)
            if expected and not validieren(key, name, expected):
                # Mismatch -- Ticker liefert falsche Company
                changed = True
                res["resolved"].pop(key, None)
                res["failed"][key] = res["failed"].get(key, []) + [
                    f"MISMATCH: yfinance {sym} liefert '{name}', Wiki erwartet '{expected}'"
                ]
                print(f"  WARNUNG: {key} MISMATCH -- yfinance={sym} ('{name}'), Wiki='{expected}'. Ticker ausgeschlossen.")
                time.sleep(0.05)
                continue
            if sym != cached:
                changed = True
            res["resolved"][key] = sym
            res.setdefault("names", {})[key] = name
            res["failed"].pop(key, None)
        else:
            changed = True
            res["failed"][key] = res["failed"].get(key, []) + ["kein Kandidat funktionierte"]
            print(f"  WARNUNG: {key} nicht aufloesbar (delisted/umbenannt?) -- ausgeschlossen.")
        time.sleep(0.05)
    if changed:
        save_resolution(res)
    return res["resolved"]


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


_CORP_SUFFIXES = (
    " INC", " INCORPORATED", " CORP", " CORPORATION", " CO", " COMPANY",
    " LTD", " LIMITED", " PLC", " LLC", " LP", " N V", " NV", " SA", " AG",
    " GROUP", " HOLDINGS", " HLDGS", " HLDG", " ENTERPRISES", " THE",
)


def _normalize_company_name(name):
    """Grobe Normalisierung fuer Namensvergleich 13F-Issuer vs. yfinance-shortName.
    13F-Namen sind oft auf ~28 Zeichen abgeschnitten -- deshalb praefix-tolerant."""
    if not name:
        return ""
    n = re.sub(r"[.,]", " ", name.upper())
    n = re.sub(r"\s+", " ", n).strip()
    for suf in _CORP_SUFFIXES:
        if n.endswith(suf):
            n = n[: -len(suf)].strip()
    return n


def build_smart_money_index():
    """Ein Snapshot-Fetch pro getrackter Institution (nicht pro Ticker!), dann
    ein flaches Lookup fuer smart_money_lookup(). Nutzt thirteen_f.py's eigenes
    HTTP-Caching (TTL 1 Tag/7 Tage) -- bei kaltem Cache erster Lauf langsamer,
    danach schnell."""
    index = []
    for inst in _load_tracked_institutions():
        try:
            snap = fetch_13f_snapshot(inst["cik"])
        except Exception:
            snap = None
        if not snap:
            print(f"  Smart-Money: {inst['name']} -- kein 13F-Snapshot verfuegbar, uebersprungen")
            continue
        for h in snap.get("holdings", []):
            index.append({
                "institution": inst["name"], "name": h["name"],
                "name_norm": _normalize_company_name(h["name"]),
                "cusip": h["cusip"], "value_usd": h["total_value_usd"],
                "shares": h["total_shares"], "period": snap["period_of_report"],
            })
    return index


def smart_money_lookup(company_name, index):
    """Findet Institutionen, die `company_name` (yfinance shortName) halten.
    13F-Namen sind oft abgeschnitten -> Praefix-/Contains-Match auf beiden Seiten."""
    target = _normalize_company_name(company_name)
    if not target or not index:
        return []
    matches = []
    for entry in index:
        n = entry["name_norm"]
        if not n:
            continue
        if target == n or target.startswith(n) or n.startswith(target):
            matches.append(entry)
    return matches


def score_one(key, sym, smart_money_index=None):
    info = get_info(sym)
    fin = get_financials(sym, force=False)
    sd = get_stock_data(sym, period="1y")
    if not info or info.get("currentPrice") is None and info.get("regularMarketPrice") is None:
        return None

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    beta = info.get("beta") or 1.2
    de = info.get("debtToEquity") or 0
    n_an = info.get("numberOfAnalystOpinions") or 0
    rev_g = info.get("revenueGrowth")
    net_m = info.get("profitMargins")
    peg = info.get("pegRatio")
    target_mean = info.get("targetMeanPrice")
    rec = info.get("recommendationKey") or ""
    rsi = sd.get("rsi_14") if sd else None
    low52 = sd.get("low_52w") if sd else None
    high52 = sd.get("high_52w") if sd else None
    pos_rng = None
    if low52 is not None and high52 is not None and price:
        rng = high52 - low52
        if rng > 0:
            pos_rng = round((price - low52) / rng * 100, 1)

    upside = ((target_mean - price) / price * 100) if target_mean and price else None
    val_score = 0.0
    if upside is not None:
        val_score += clamp(upside, -20, 60) / 60 * 15
    if peg is not None and peg > 0:
        val_score += clamp(2.0 - peg, -1, 2) / 2 * 10
    else:
        val_score += 3

    qual_score = 0.0
    if rev_g is not None:
        qual_score += clamp(rev_g, -0.3, 0.6) / 0.6 * 10
    if net_m is not None:
        qual_score += clamp(net_m, -0.5, 0.5) / 0.5 * 10
    if de <= 30:
        qual_score += 10
    elif de <= 100:
        qual_score += 6
    elif de <= 250:
        qual_score += 2

    tech_score = 0.0
    if rsi is not None:
        if 45 <= rsi <= 65:
            tech_score += 10
        elif 35 <= rsi < 45 or 65 < rsi <= 75:
            tech_score += 6
        else:
            tech_score += 2
    if pos_rng is not None:
        if 15 <= pos_rng <= 55:
            tech_score += 10
        elif 5 <= pos_rng < 15 or 55 < pos_rng <= 75:
            tech_score += 6
        else:
            tech_score += 2

    risk_pts = 15.0
    risk_pts -= clamp(beta, 0, 3) / 3 * 5
    risk_pts -= 5 if de > 150 else (2 if de > 50 else 0)
    risk_pts -= 4 if n_an < 10 else 0
    if net_m is not None and net_m < 0:
        risk_pts -= 3
    risk_pts = clamp(risk_pts, 0, 15)

    rec_pts = {"strong_buy": 10, "buy": 7, "hold": 2, "underperform": 0, "sell": 0}.get(rec, 3)

    # Smart-Money-Faktor (13F-Holdings getrackter Fonds, siehe Modul-Docstring)
    sm_matches = smart_money_lookup(info.get("shortName") or key, smart_money_index or [])
    salp_hit = any(m["institution"] == "Situational Awareness LP" for m in sm_matches)
    # SALP-Sonderbonus deaktiviert (2026-08-18, Nik-Entscheidung): der Fonds ist im
    # Juli 2026 nach 4x-Hebel und Margin Calls kollabiert und musste sein GESAMTES
    # oeffentliches Aktienportfolio an Citadel notverkaufen (45 Mrd. $ -> ~10 Mrd. $,
    # siehe CNBC/Bloomberg 2026-07-30/31). Der Q2-13F-Snapshot (Stichtag 30.06., erst
    # 14.08. eingereicht, SEC erlaubt 45 Tage Verzug) zeigt also mit hoher
    # Wahrscheinlichkeit Positionen, die es nicht mehr gibt -- als "haelt gerade" zu
    # werten waere irrefuehrend. SALP zaehlt deshalb NICHT mehr in den Score, bleibt
    # aber informativ im Report (smart_money_salp) UND im getrackten
    # Fonds-Universum (data/tracked_institutions.json), falls er mit kleinerem,
    # entlevertem Portfolio wieder normal filed.
    sm_institutions = sorted(set(m["institution"] for m in sm_matches
                                 if m["institution"] != "Situational Awareness LP"))
    smart_money_pts = min(len(sm_institutions) * 2.0, 8.0)

    total = round(val_score + qual_score + tech_score + risk_pts + rec_pts + smart_money_pts, 1)

    # DCF (grobes 1-Pfad-Modell, siehe Modul-Docstring fuer Grenzen)
    fcf = info.get("freeCashflow")
    shares = info.get("sharesOutstanding")
    wacc = RISK_FREE + beta * ERP
    g5 = rev_g if rev_g is not None and rev_g < 0.6 else 0.15
    g5 = max(g5, 0.03)
    terminal_g = 0.025
    dcf_val = None
    if fcf and shares and wacc > terminal_g:
        pv, cf = 0.0, fcf
        for yr in range(1, 6):
            cf = cf * (1 + g5) if yr == 1 else cf * (1 + g5 * (1 - (yr - 1) * 0.15))
            pv += cf / ((1 + wacc) ** yr)
        terminal = (cf * (1 + terminal_g)) / (wacc - terminal_g)
        pv_terminal = terminal / ((1 + wacc) ** 5)
        equity_val = pv + pv_terminal + (info.get("totalCash") or 0) - (info.get("totalDebt") or 0)
        dcf_val = equity_val / shares
    dcf_warnung = None
    if info.get("currency") and info.get("financialCurrency") and info["currency"] != info["financialCurrency"]:
        dcf_warnung = f"Waehrungs-Mismatch: Kurs in {info['currency']}, Bilanz in {info['financialCurrency']}"
    elif beta < 0.6:
        dcf_warnung = "Niedriges Beta -> Modell ueberschaetzt Terminal-Value systematisch"

    return {
        "key": key, "yf_symbol": sym, "name": info.get("shortName"),
        "sector": info.get("sector"), "industry": info.get("industry"),
        "currency": info.get("currency"), "country": info.get("country"),
        "price": price, "beta": beta, "debt_equity": de, "n_analysten": n_an,
        "rev_growth": rev_g, "net_margin": net_m, "peg": peg,
        "target_mean": target_mean, "upside_pct": round(upside, 1) if upside is not None else None,
        "recommendation": rec, "rsi14": rsi, "pos_in_range_pct": pos_rng,
        "dcf_fair_value": dcf_val, "dcf_warnung": dcf_warnung,
        "score_value": round(val_score, 1), "score_qualitaet": round(qual_score, 1),
        "score_technik": round(tech_score, 1), "score_risiko": round(risk_pts, 1),
        "score_analysten_konsens": rec_pts,
        "score_smart_money": round(smart_money_pts, 1),
        "smart_money_institutionen": sm_institutions,
        "smart_money_salp": salp_hit,
        "score_gesamt": total,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=None, help="nur die Top N anzeigen")
    ap.add_argument("--min-analysten", type=int, default=0, help="Titel mit weniger Analysten ausschliessen")
    ap.add_argument("--refresh-ticker", action="store_true", help="Ticker-Aufloesung fuer alle Titel neu pruefen")
    ap.add_argument("--correlation", action="store_true", help="zusaetzlich Korrelationsmatrix (6M) berechnen")
    ap.add_argument("--no-smart-money", action="store_true", help="13F-Smart-Money-Faktor abschalten")
    ap.add_argument("--json", default=REPORT_JSON, help="Ausgabepfad fuer den vollen Report")
    args = ap.parse_args()

    keys = discover_universe()
    print(f"Universum: {len(keys)} Titel in {WISSENSDATENBANK}")
    resolution = ensure_resolution(keys, refresh=args.refresh_ticker)

    smart_money_index = None
    if not args.no_smart_money:
        print("Smart-Money-Index (13F der getrackten Fonds, inkl. Situational Awareness LP) wird geladen...")
        smart_money_index = build_smart_money_index()
        print(f"  {len(smart_money_index)} Holdings ueber "
              f"{len(_load_tracked_institutions())} Institutionen indiziert.")

    rows = []
    for i, key in enumerate(keys, 1):
        sym = resolution.get(key)
        if not sym:
            continue
        r = score_one(key, sym, smart_money_index=smart_money_index)
        if r:
            rows.append(r)
        print(f"  [{i}/{len(keys)}] {key} ({sym})", "OK" if r else "keine Daten")
        time.sleep(0.15)

    rows = [r for r in rows if r["n_analysten"] >= args.min_analysten]
    rows.sort(key=lambda r: r["score_gesamt"], reverse=True)

    corr_info = None
    if args.correlation:
        symbols = [r["yf_symbol"] for r in rows] + ["SPY"]
        data = download_closes(symbols, period="6mo")
        if data is not None:
            corr = data.pct_change().dropna(how="all").corr(min_periods=40)
            corr_info = corr.round(3).to_dict()

    shown = rows[: args.top] if args.top else rows
    print(f"\n{'#':3} {'KEY':8} {'SYM':10} {'SCORE':>6} {'UPS%':>6} {'AN':>3} {'REC':10} {'SM':>4} {'DCF-WARNUNG':30} NAME")
    for i, r in enumerate(shown, 1):
        ups = f"{r['upside_pct']:.1f}" if r["upside_pct"] is not None else "n/a"
        warn = (r["dcf_warnung"] or "-")[:30]
        sm = f"{r['score_smart_money']:.0f}" if r.get("score_smart_money") else "-"
        if r.get("smart_money_salp"):
            sm += "*"
        print(f"{i:3} {r['key']:8} {r['yf_symbol']:10} {r['score_gesamt']:6.1f} {ups:>6} "
              f"{r['n_analysten']:3d} {r['recommendation']:10} {sm:>4} {warn:30} {r['name']}")
    if not args.no_smart_money:
        print("(SM = Smart-Money-Bonus-Punkte, * = auch von Situational Awareness LP gehalten)")

    report = {
        "stand": datetime.now(timezone.utc).isoformat(),
        "universum_groesse": len(keys),
        "min_analysten_filter": args.min_analysten,
        "titel": rows,
        "korrelation_6m": corr_info,
    }
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str, ensure_ascii=False)
    print(f"\nVoller Report ({len(rows)} Titel): {args.json}")
    print("Naechster Schritt fuer eine Kauf-Synthese: prompts/P6_banker_council.txt "
          "mit diesem JSON fuettern (LLM-Urteil, nicht dieses Script).")


if __name__ == "__main__":
    main()
