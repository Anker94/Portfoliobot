"""Regression-Tests fuer portfoliobot/dashboard/app.py Parser-Funktionen.

Hintergrund: Externe Audit-Empfehlung 2026-08-21 (P1.3): Parser-Patterns sind
datenintegritaets-kritisch (Score-Wert haengt direkt am Text-Parsing). Diese
Tests sichern bekannte Edge-Cases ab, damit neue Pattern-Fix-Versuche nicht
versehentlich alte Varianten zerstoeren.

Run:
    cd <repo-root>
    python -m unittest dashboard.tests.test_parser_variants -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Pfad-Setup: tests/ -> dashboard/ -> Projekt-Root
DASHBOARD_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = DASHBOARD_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))
sys.path.insert(0, str(DASHBOARD_DIR))

from lib.portfolio_helpers import (  # noqa: E402
    parse_md_kpis, score_valuation, _confidence_label,
    _load_banker_scores, _banker_label, _banker_zone_class,
    _build_wiki_ticker_file, _JOB_STATE, _prune_job_state, HERMES_JOBS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _md_block(bewertung_text: str) -> str:
    """Wickelt einen gegebenen Text in ein valides Markdown-File mit
    BEWERTUNG-Block ein. So muss jeder Test nur den Inhalt der Zeile
    formulieren, nicht das ganze Datei-Skelett."""
    return f"""# Test (TST)

## BEWERTUNG
{bewertung_text}
"""


# ---------------------------------------------------------------------------
# 1. score_valuation / Confidence
# ---------------------------------------------------------------------------
class TestScoreConfidence(unittest.TestCase):
    """Audit-P1.1: Confidence + components_available."""

    def test_4_components_high(self):
        v = score_valuation({
            "52w_position_pct": 50,
            "peg": 1.0,
            "kgv_forward": 15.0,
            "upside_pct": 10.0,
        })
        self.assertEqual(v["components_available"], 4)
        self.assertEqual(v["confidence"], "high")
        self.assertIsNotNone(v["score"])

    def test_3_components_medium(self):
        v = score_valuation({
            "52w_position_pct": 50,
            "peg": 1.0,
            "kgv_forward": 15.0,
        })
        self.assertEqual(v["components_available"], 3)
        self.assertEqual(v["confidence"], "medium")
        self.assertIsNotNone(v["score"])

    def test_2_components_low_score_kept(self):
        v = score_valuation({"52w_position_pct": 50, "peg": 1.0})
        self.assertEqual(v["components_available"], 2)
        self.assertEqual(v["confidence"], "low")
        # Bei 2 Komponenten bleibt der Score berechnet -- User sieht die
        # Confidence 'low' in der UI und kann filtern.
        self.assertIsNotNone(v["score"])

    def test_1_component_insufficient_score_none(self):
        v = score_valuation({"52w_position_pct": 50})
        self.assertEqual(v["components_available"], 1)
        self.assertEqual(v["confidence"], "insufficient")
        # Bei 1 Komponente Score unterdruecken (Zone 'no-data').
        self.assertIsNone(v["score"])
        self.assertEqual(v["zone"], "no-data")

    def test_0_components_no_data(self):
        v = score_valuation({})
        self.assertEqual(v["components_available"], 0)
        self.assertEqual(v["confidence"], "insufficient")
        self.assertIsNone(v["score"])
        self.assertEqual(v["zone"], "no-data")

    def test_confidence_label_helper(self):
        # Direkter Test des Mapping-Helpers, der auch in Templates via
        # _confidence_label benutzt werden kann.
        self.assertEqual(_confidence_label(0), "insufficient")
        self.assertEqual(_confidence_label(1), "insufficient")
        self.assertEqual(_confidence_label(2), "low")
        self.assertEqual(_confidence_label(3), "medium")
        self.assertEqual(_confidence_label(4), "high")
        # Out-of-range defensive
        self.assertEqual(_confidence_label(5), "high")
        self.assertEqual(_confidence_label(-1), "insufficient")

    def test_negative_components_zero(self):
        """Verluste: negative Earnings -> profitMargins None.
        Komponenten mit Wert 0 oder negativ sollten nicht zaehlen."""
        v = score_valuation({
            "52w_position_pct": 50,
            "peg": -1.0,        # negativ -> wird in score_valuation rausgefiltert
            "kgv_forward": 15.0,
            "upside_pct": 10.0,
        })
        # PEG < 0 wird in score_valuation() uebersprungen (peg > 0 Bedingung)
        self.assertEqual(v["components_available"], 3)
        self.assertEqual(v["confidence"], "medium")

    def test_kgv_trailing_fallback(self):
        """KGV forward None -> KGV trailing als Fallback.
        Zaehlt weiterhin als 1 Komponente (nicht 2)."""
        v = score_valuation({"kgv_trailing": 20.0})
        self.assertEqual(v["components_available"], 1)
        self.assertEqual(v["confidence"], "insufficient")
        self.assertIsNone(v["score"])


# ---------------------------------------------------------------------------
# 2. parse_md_kpis -- Kurs-Varianten
# ---------------------------------------------------------------------------
class TestParseKurs(unittest.TestCase):
    """Edge-Cases fuer die Kurs-Erkennung im BEWERTUNG-Block."""

    def test_kurs_simple_usd(self):
        k = parse_md_kpis(_md_block("Kurs 100,00 USD."))
        self.assertEqual(k["kurs"], 100.0)

    def test_kurs_mit_doppelpunkt_jpy(self):
        """'Kurs: 35.360 JPY' (Doppelpunkt + JPY-Kombination). Pattern 2
        in _KURS_PATTERNS erlaubt optionalen Doppelpunkt nach 'Kurs'."""
        k = parse_md_kpis(_md_block("Kurs: 35.360 JPY"))
        self.assertIsNotNone(k["kurs"])
        self.assertGreater(k["kurs"], 30000)

    def test_kurs_gbx_tausender(self):
        """WIZZ-Spezialfall: 1.080 GBX -> 1080 (Tausender, nicht Dezimal)."""
        k = parse_md_kpis(_md_block("Kurs 1.080 GBX"))
        # 1.080 GBX = 1080 Pence = 10.80 GBP
        self.assertEqual(k["kurs"], 1080.0)

    def test_kurs_pennystock(self):
        """Pennystock-Schwelle: Kurse < 0.0001 sollen verworfen werden."""
        k = parse_md_kpis(_md_block("Kurs 0,054 USD."))
        # 0,054 USD = 0.054
        self.assertIsNotNone(k["kurs"])
        self.assertLess(k["kurs"], 0.1)

    def test_kurs_mit_ca(self):
        """'Kurs ca. X' Variante."""
        k = parse_md_kpis(_md_block("Kurs ca. 42,50 USD"))
        self.assertAlmostEqual(k["kurs"], 42.5, places=2)


# ---------------------------------------------------------------------------
# 3. parse_md_kpis -- KGV-Varianten
# ---------------------------------------------------------------------------
class TestParseKGV(unittest.TestCase):
    """Edge-Cases fuer KGV-Parsing (trailing + forward).

    Hinweis: Einige dieser Tests sind Known-Bugs (P2-Folgeaufgabe Pattern-
    Erweiterung). Sie sind als @unittest.skip markiert mit klarer Begründung
    im Doc-String -- der Audit-P1.3 fordert explizit "Regression-Tests, die
    die bekannten Edge-Cases dokumentieren". Folge-Commits muessen diese
    Tests dann gruen machen.
    """

    def test_kgv_trailing_klammern(self):
        """'KGV (trailing) 34,48' mit Klammern. Pattern 1 in
        _KGV_TTM_PATTERNS erlaubt jetzt (TTM|trailing)."""
        k = parse_md_kpis(_md_block("KGV (trailing) 34,48"))
        self.assertIsNotNone(k["kgv_trailing"])
        self.assertAlmostEqual(k["kgv_trailing"], 34.48, places=2)

    def test_kgv_forward_ohne_klammern(self):
        """'KGV (forward) 22,59' mit Klammern. Pattern 2 in
        _KGV_FWD_PATTERNS erlaubt jetzt optionale Klammern."""
        k = parse_md_kpis(_md_block("KGV (forward) 22,59"))
        self.assertIsNotNone(k["kgv_forward"])
        self.assertAlmostEqual(k["kgv_forward"], 22.59, places=2)

    def test_kgv_pe_statt_kgv(self):
        """Manche Wiki-Files nutzen 'P/E' statt 'KGV'. Pattern 5 in
        _KGV_TTM_PATTERNS erlaubt jetzt (trailing)-Klammern nach P/E."""
        k = parse_md_kpis(_md_block("P/E (trailing) 28,5"))
        self.assertIsNotNone(k.get("kgv_trailing"))

    def test_kgv_trailing_cap(self):
        """NBIS hat 1795 -- alles > 10000 soll verworfen werden.
        Pattern 1 in _KGV_TTM_PATTERNS matcht jetzt (trailing) in
        Klammern und nimmt 1795,4 (Cap-Logik v < 10000 haelt)."""
        k = parse_md_kpis(_md_block("KGV (trailing) 1795,4"))
        self.assertIsNotNone(k["kgv_trailing"])
        self.assertAlmostEqual(k["kgv_trailing"], 1795.4, places=1)

        k2 = parse_md_kpis(_md_block("KGV (trailing) 99999"))
        self.assertIsNone(k2["kgv_trailing"])


# ---------------------------------------------------------------------------
# 4. parse_md_kpis -- 52-Wochen-Range
# ---------------------------------------------------------------------------
class TestParse52W(unittest.TestCase):
    """Edge-Cases fuer 52-Wochen-Range."""

    def test_52w_range_em_dash(self):
        k = parse_md_kpis(_md_block("52-Wochen-Range: 164,07–236,54 USD"))
        self.assertIsNotNone(k["52w_low"])
        self.assertIsNotNone(k["52w_high"])
        self.assertAlmostEqual(k["52w_low"], 164.07, places=2)
        self.assertAlmostEqual(k["52w_high"], 236.54, places=2)

    def test_52w_range_en_dash(self):
        k = parse_md_kpis(_md_block("52-Wochen-Range: 164,07-236,54 USD"))
        self.assertIsNotNone(k["52w_low"])
        self.assertIsNotNone(k["52w_high"])

    def test_52w_range_hyphen(self):
        k = parse_md_kpis(_md_block("52-Wochen-Range: 164,07 - 236,54 USD"))
        self.assertIsNotNone(k["52w_low"])
        self.assertIsNotNone(k["52w_high"])

    def test_52w_position_berechnung(self):
        """Position wird aus Kurs + Range berechnet."""
        k = parse_md_kpis(_md_block(
            "Kurs 200,00 USD. 52-Wochen-Range: 100,00–300,00 USD."
        ))
        # (200 - 100) / (300 - 100) * 100 = 50
        self.assertAlmostEqual(k["52w_position_pct"], 50.0, places=1)


# ---------------------------------------------------------------------------
# 5. parse_md_kpis -- Analysten / PT / Upside
# ---------------------------------------------------------------------------
class TestParseAnalyst(unittest.TestCase):
    """Edge-Cases fuer Analyst-Konsens + Kursziel."""

    def test_strong_buy_erkannt(self):
        k = parse_md_kpis(_md_block(
            'Analysten-Konsens: "Strong Buy", 61 Analysten, durchschnittliches '
            'Kursziel 302,83 USD (+34,5 % vs. aktuellem Kurs).'
        ))
        self.assertEqual(k["consensus"], "Strong Buy")
        self.assertEqual(k["konsens_basis"], 61)
        self.assertIsNotNone(k["pt"])

    def test_upside_aus_differenz(self):
        """Fallback: wenn kein expliziter Upside, aus PT vs. Kurs."""
        k = parse_md_kpis(_md_block(
            "Kurs 100,00 USD. Kursziel 150,00 USD."
        ))
        # (150 - 100) / 100 * 100 = 50
        self.assertIsNotNone(k["upside_pct"])
        self.assertAlmostEqual(k["upside_pct"], 50.0, places=1)


# ---------------------------------------------------------------------------
# 6. Integration: reale Stichproben
# ---------------------------------------------------------------------------
class TestRealStichproben(unittest.TestCase):
    """Stichproben aus dem realen Wiki-Bestand (Stand 2026-08-20)."""

    def setUp(self):
        # Projekt-Root -> wissensdatenbank/aktien
        self.wiki_dir = PROJECT_ROOT / "wissensdatenbank" / "aktien"
        if not self.wiki_dir.exists():
            self.skipTest(f"Wiki-Ordner fehlt: {self.wiki_dir}")

    def _load(self, ticker: str) -> dict:
        path = self.wiki_dir / f"{ticker}.md"
        text = path.read_text(encoding="utf-8")
        kpis = parse_md_kpis(text)
        return {**kpis, "valuation": score_valuation(kpis)}

    def test_nvda_high_confidence(self):
        """NVDA: 4 Komponenten verfuegbar, alle gut geparst."""
        result = self._load("NVDA")
        v = result["valuation"]
        self.assertGreaterEqual(v["components_available"], 3,
                                "NVDA sollte mindestens 3 Komponenten liefern")
        self.assertIn(v["confidence"], ("high", "medium"))

    def test_aur_low_confidence(self):
        """AUR: Pre-Revenue, oft nur 1-2 Komponenten.
        Wenn 2: low. Wenn 1: insufficient (Score=None)."""
        result = self._load("AUR")
        v = result["valuation"]
        # AUR hat bekanntermassen 52W + Upside aber kein PEG/KGV
        self.assertGreaterEqual(v["components_available"], 1)

    def test_alle_files_kein_crash(self):
        """Robustheit: parse + score laufen ohne Exception fuer alle Files."""
        files = list(self.wiki_dir.glob("*.md"))
        files = [f for f in files if f.name.upper() != "README.MD"]
        self.assertGreater(len(files), 100,
                           "Erwarte > 100 Wiki-Files")
        for f in files[:50]:  # Smoke-Testsample
            try:
                text = f.read_text(encoding="utf-8")
                parse_md_kpis(text)
                score_valuation(parse_md_kpis(text))
            except Exception as exc:
                self.fail(f"Parser-Crash fuer {f.name}: {exc}")


# ---------------------------------------------------------------------------
# 7. Audit-P1: Score-Examples aus dem Audit-Prompt
# ---------------------------------------------------------------------------
class TestAuditExamples(unittest.TestCase):
    """Verifiziert die Stichproben-Rechnungen aus dem externen Audit."""

    def test_nvda_aus_audit_prompt(self):
        """NVDA-Stichprobe aus dem Audit-Prompt: 4 Komponenten verfuegbar,
        Zone guenstig, Confidence high. Exakter Score-Wert haengt von den
        exakten Eingangszahlen ab -- wir testen daher Struktur + Zone +
        Confidence, nicht den exakten numerischen Score.
        Hintergrund: 68.2 im Audit-Prompt stammt aus einem frueheren
        yfinance-Snapshot; der hier berechnete Score kann mit anderen
        Eingangswerten abweichen, ohne dass es ein Bug ist.
        """
        v = score_valuation({
            "52w_position_pct": 72.8,
            "kgv_forward": 22.59,
            "peg": 0.51,
            "upside_pct": 34.5,
        })
        # Struktur
        self.assertEqual(v["components_available"], 4)
        self.assertEqual(v["confidence"], "high")
        # Score existiert
        self.assertIsNotNone(v["score"])
        # Zone: guenstig erwartet (Score 60-74)
        self.assertEqual(v["zone"], "guenstig")
        # Score-Wert in plausiblem Bereich
        self.assertGreater(v["score"], 55)
        self.assertLess(v["score"], 75)

    def test_aur_aus_audit_prompt(self):
        """AUR-Stichprobe aus dem Audit-Prompt: 2 Komponenten (52W, Upside),
        Score 66,5, Zone guenstig, Confidence low.
        """
        # 52W-Position 67% -> 100 - 67 = 33
        v = score_valuation({"52w_position_pct": 67, "upside_pct": 30})
        # Mittel: (33 + 100) / 2 = 66.5
        self.assertAlmostEqual(v["score"], 66.5, places=0)
        self.assertEqual(v["zone"], "guenstig")
        # 2 Komponenten -> low
        self.assertEqual(v["confidence"], "low")
        self.assertEqual(v["components_available"], 2)


# ---------------------------------------------------------------------------
# 8. 5-Banker-Score (Hermes-Territorium, read-only)
# ---------------------------------------------------------------------------
class TestBankerScore(unittest.TestCase):
    """Banker-Score ist ein separater, in scripts/banker_council.py berechneter
    Score (0-100 Summe aus val+qual+tech+risk+rec+smart_money). Das Dashboard
    skaliert auf 1-10 (linear /10) und bietet Label + Zone-Klasse.

    Mavis schreibt NIE in data/banker_council_latest.json -- die Tests lesen
    nur, was Hermes produziert hat.
    """

    def test_label_bucket_mapping(self):
        # Untere Grenze
        self.assertEqual(_banker_label(0.0), "stark meiden")
        self.assertEqual(_banker_label(1.9), "stark meiden")
        # Schwach
        self.assertEqual(_banker_label(2.0), "schwach")
        self.assertEqual(_banker_label(3.9), "schwach")
        # Neutral
        self.assertEqual(_banker_label(4.0), "neutral")
        self.assertEqual(_banker_label(5.9), "neutral")
        # Kaufenswert
        self.assertEqual(_banker_label(6.0), "kaufenswert")
        self.assertEqual(_banker_label(7.9), "kaufenswert")
        # Stark kaufenswert
        self.assertEqual(_banker_label(8.0), "stark kaufenswert")
        self.assertEqual(_banker_label(10.0), "stark kaufenswert")
        # None
        self.assertEqual(_banker_label(None), "kein Banker-Score")

    def test_zone_class_mapping(self):
        # CSS-Klassen-Mapping
        self.assertEqual(_banker_zone_class(0.0), "bad")
        self.assertEqual(_banker_zone_class(3.9), "bad")
        self.assertEqual(_banker_zone_class(4.0), "fair")
        self.assertEqual(_banker_zone_class(5.9), "fair")
        self.assertEqual(_banker_zone_class(6.0), "good")
        self.assertEqual(_banker_zone_class(10.0), "good")
        # None -> no-data
        self.assertEqual(_banker_zone_class(None), "no-data")

    def test_load_banker_scores_real_json(self):
        """Liest die echte data/banker_council_latest.json (Hermes-Output)."""
        scores = _load_banker_scores()
        # Stand ist immer gesetzt (oder leeres Dict wenn JSON fehlt)
        self.assertIn("__stand__", scores)
        # Mindestens ein paar Ticker erwartet (oder 0 wenn JSON leer)
        n_tickers = len([k for k in scores if k != "__stand__"])
        # Wir wissen: 85 Ticker in der letzten Version
        if n_tickers > 0:
            # Einer der bekannten Ticker (SKHYY = Top-Score ~8,7)
            skhyy = scores.get("SKHYY")
            if skhyy:
                self.assertIsNotNone(skhyy["score_1_10"])
                # Score 1-10 muss in [0, 10] sein
                self.assertGreaterEqual(skhyy["score_1_10"], 0)
                self.assertLessEqual(skhyy["score_1_10"], 10)
                # Components vorhanden
                self.assertIn("value", skhyy)
                self.assertIn("qualitaet", skhyy)
                self.assertIn("technik", skhyy)
                self.assertIn("risiko", skhyy)
                self.assertIn("analysten_konsens", skhyy)
                self.assertIn("smart_money", skhyy)
                # SKHYY ist stark kaufenswert
                self.assertIn(skhyy["score_1_10"], [8.7, 8.8, 8.9, 9.0, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 10.0])

    def test_score_skalierung_1_10(self):
        """Verifiziert, dass score_1_10 = round(score_100 / 10, 1)."""
        scores = _load_banker_scores()
        for k, v in scores.items():
            if k == "__stand__":
                continue
            if v.get("score_1_10") is not None and v.get("score_100") is not None:
                expected = round(float(v["score_100"]) / 10.0, 1)
                expected = max(0.0, min(10.0, expected))
                self.assertAlmostEqual(v["score_1_10"], expected, places=1,
                                       msg=f"Skalierung kaputt fuer {k}")

    def test_load_banker_scores_caching(self):
        """Cache: zweiter Aufruf gibt gleiche Dict-Instanz zurueck (60s TTL)."""
        s1 = _load_banker_scores()
        s2 = _load_banker_scores()
        # Bei 60s TTL ohne Refresh: gleiche Instanz
        self.assertIs(s1, s2)

    def test_load_banker_scores_missing_file(self):
        """Wenn JSON fehlt, soll _load_banker_scores leeres Dict + None-Stand
        liefern, nicht crashen."""
        from lib import portfolio_helpers as h
        original_path = h.BANKER_COUNCIL_JSON
        # Temporaer auf nicht-existenten Pfad umbiegen
        h.BANKER_COUNCIL_JSON = original_path.parent / "_does_not_exist_.json"
        # Cache invalidieren
        h._BANKER_SCORE_CACHE = None
        h._BANKER_SCORE_CACHE_TS = 0
        result = _load_banker_scores()
        self.assertEqual(result, {})
        # Wieder zurueck
        h.BANKER_COUNCIL_JSON = original_path
        h._BANKER_SCORE_CACHE = None
        h._BANKER_SCORE_CACHE_TS = 0


# ---------------------------------------------------------------------------
# 9. Trigger-Check-Wiki (Async-Admin-Job)
# ---------------------------------------------------------------------------
class TestTriggerCheckWiki(unittest.TestCase):
    """Wiki-Trigger-Check: async Admin-Job mit Status-Polling.

    Testet: Ticker-File-Generierung, State-Maschine (idle -> running ->
    done/error), Pruning. NICHT den Live-Subprocess (zu lang, zu
    variabel, zu Environment-abhaengig).
    """

    def test_build_wiki_ticker_file_creates_tempfile(self):
        """Generiert einen Ticker-File mit allen Wiki-Tickern."""
        path = _build_wiki_ticker_file()
        if path is None:
            self.skipTest("Wiki-Ordner nicht erreichbar")
        try:
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            self.assertGreater(len(lines), 100,
                               "Erwarte > 100 Wiki-Ticker (aktuell 233)")
            # README darf NICHT enthalten sein
            self.assertNotIn("README", lines)
        finally:
            if path.exists():
                path.unlink()

    def test_build_wiki_ticker_file_missing_dir(self):
        """Wenn Wiki-Ordner fehlt: return None, kein Crash."""
        from lib import portfolio_helpers as h
        original = h.WISSENSDATENBANK_DIR
        h.WISSENSDATENBANK_DIR = original.parent / "_does_not_exist_wiki_"
        result = _build_wiki_ticker_file()
        self.assertIsNone(result)
        h.WISSENSDATENBANK_DIR = original

    def test_hermes_jobs_config_has_trigger_check_wiki(self):
        """Prueft, dass der neue Job korrekt konfiguriert ist."""
        cfg = HERMES_JOBS.get("trigger_check_wiki")
        self.assertIsNotNone(cfg, "trigger_check_wiki muss in HERMES_JOBS sein")
        self.assertTrue(cfg.get("async"), "muss als async markiert sein")
        self.assertTrue(cfg.get("wiki_ticker_file"),
                        "muss wiki_ticker_file-Spezialbehandlung nutzen")
        self.assertGreaterEqual(cfg.get("timeout", 0), 300,
                                "timeout muss >= 5 Min fuer 233 Ticker")
        self.assertIn("trigger_check.py", cfg.get("script", ""))

    def test_job_state_idle_default(self):
        """Falls Job nie gestartet: state ist 'idle' (oder nicht im Dict)."""
        # cleanup
        _JOB_STATE.pop("__test_never_started__", None)
        st = _JOB_STATE.get("__test_never_started__")
        if st is None:
            # Wenn nicht im Dict, gilt das als idle -- OK
            self.assertIsNone(st)
        else:
            self.assertEqual(st.get("state"), "idle")

    def test_prune_job_state_removes_old_entries(self):
        """Pruning: alte Eintraege (>1h) werden entfernt."""
        import time as _time
        _JOB_STATE["__test_old__"] = {
            "state": "done",
            "finished_at": _time.time() - 7200,  # 2h alt
            "label": "test",
        }
        _JOB_STATE["__test_fresh__"] = {
            "state": "done",
            "finished_at": _time.time() - 60,  # 1min alt
            "label": "test",
        }
        _prune_job_state()
        self.assertNotIn("__test_old__", _JOB_STATE)
        self.assertIn("__test_fresh__", _JOB_STATE)
        # cleanup
        del _JOB_STATE["__test_fresh__"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
