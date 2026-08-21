# REPO-PREPARATION.md — Sanitisierungs-Log für den Public Release

> **Zweck:** Dokumentiert, was aus dem privaten Portfoliobot
> (`07-Finanzen/Projekte/Portfoliobot/`) in den Public Release
> (`08-Online/portfoliobot/`) übernommen wurde und warum.
> Stand: 2026-08-21.

## Scope-Entscheidung

Der Public Release enthält das **bestehende Grundgerüst**: Dashboard,
Scoring-Mechanismus, Admin-Skripte, Library, Prompts und 10 anonymisierte
Ticker-Templates als Struktur-Beispiele. **Sparplan-Positionen, persönliche
Bewertungen und Watchlist-Daten sind nicht enthalten** — jeder Nutzer
richtet sich seine eigene `sparplan-tracking.json` und seine eigenen
`.md`-Files in `wissensdatenbank/aktien/` ein.

## Lizenz

**MIT** — gewählt am 2026-08-21. Begründung:
- Maximale User-Freiheit (kein Copyleft)
- Standard für kleine/mittlere Tools
- Kommerziell nutzbar (auch closed-source Derivate)
- Patent-Schutz (Apache-Extra) für ein privates Scoring-Tool nicht nötig

## Was reinkommt

| Pfad | Inhalt | Anmerkung |
|---|---|---|
| `dashboard/` | Flask-App, Templates, Static, Tests | Vollständig übernommen |
| `scripts/` | valuation_refresh, sparplan_perf_refresh, trigger_check, banker_council, etc. | Vollständig übernommen |
| `lib/` | db.py, data_layer.py, market_data.py, portfolio_helpers.py, ... | Vollständig übernommen, **mit Path-Cleanup** (s.u.) |
| `prompts/` | LLM-Prompts P1-P7, advocate, guardrails | Vollständig übernommen |
| `config/config.example.json` | Vorlage | Bereits sanitized im Source-Repo |
| `wissensdatenbank/Wiki-Daten-Schema.md` | Schema-Doku | Kopie |
| `wissensdatenbank/README.md` | Kurzanleitung | Neu geschrieben |
| `wissensdatenbank/aktien/{NVDA,AAPL,MSFT,AMZN,GOOGL,TSLA,JPM,JNJ,XOM,PG}.md` | 10 anonymisierte Templates | Neu generiert (s.u.) |
| `README.md` | Public-facing Doku | Englisch, neu geschrieben |
| `LICENSE` | MIT | Standardtext |
| `.gitignore` | Public-Pattern | Neu geschrieben |

## Was raus ist (per .gitignore oder gar nicht kopiert)

| Pfad | Grund |
|---|---|
| `config/config.json` | Persönliche API-Keys |
| `data/` komplett | Runtime-Cache, persönliche Portfolio-Daten, jederzeit reproduzierbar |
| `data/stock.db` | Private SQLite-DB mit echten Positionen |
| `data/sparplan-tracking*.json` | Private Sparplan-Daten |
| `data/13f_history/`, `data/sec_cache/` | SEC-Cache |
| `data/financials_cache.json`, `banker_council_latest.json`, `tagesform.log`, `markt_refresh.log`, `trigger_log.json`, `trigger_wb_results.json`, `sparplan_aktivierungsliste_rerun.json` | Working-Output |
| `dashboard/stdout.log`, `stderr.log`, `watchdog.log` | Runtime-Logs |
| `stock.db` (im Root) | Persönliche DB-Kopie |
| `quality_review_results.json` | Working-Output |
| `AUFTRAEGE.md`, `AUDIT-*.md`, `HANDOVER-*.md`, `INGE-*.md`, `Wiki-Quality-Review-*.md`, `uebergabe-*.md`, `screening-*.json`/`*.md` | Persönliche Working-Notes, Briefing-Dokumente |
| `ANLEITUNG-aktien-scout-fuer-inge-marcel.md` | Inge-/Marcel-spezifisch, könnte in v2 sanitized werden |
| `werkzeug/` | Nik-spezifische Custom-Tools, könnte in v2 als `tools/` generalisiert werden |
| `test_financials.py` | Test-Skript; vorerst raus, kann in v2 rein |
| `.hermes/`, `.venv311/`, `__pycache__/` | Maschinen-/Runtime-State |

## Path-Cleanup im Code

`lib/portfolio_helpers.py` hatte drei Annahmen über die Verzeichnisstruktur
des Source-Repos, die im Public Release brechen würden:

1. **`HERMES_PYTHON = Path(r"C:\Users\funik\...")`** — hardcoded auf Nik's
   Hermes-Installation. **Fix:** Python aus `sys.executable` lesen (oder
   via Config überschreibbar).
2. **`TRIGGER_LOG_PATH = PROJECT_ROOT.parent / "trigger_log.json"`** — ging
   eine Ebene über dem Projekt-Root. Im Public Release nicht sinnvoll.
   **Fix:** `PROJECT_ROOT / "data" / "trigger_log.json"`.
3. **`SPARPLAN_DIR = PROJECT_ROOT.parent.parent`** — ging zwei Ebenen hoch
   (zum `07-Finanzen/`-Root). Im Public Release nicht sinnvoll.
   **Fix:** `PROJECT_ROOT / "data"`.

Diese Fixes werden in `lib/portfolio_helpers.py` im Public-Repo
umgesetzt. Die Hermes-Trennung ("Read-only in Mavis, gehoert
Hermes-Territorium") wird im Public-Repo irrelevant, weil es keine
Multi-Agent-Trennung gibt — `HERMES_JOBS` ist dort einfach eine Job-Config.

## Anonymisierung der 10 Templates

Gewählt: NVDA, AAPL, MSFT, AMZN, GOOGL, TSLA, JPM, JNJ, XOM, PG.
Begründung: universal bekannt, breite Sektoren-Streuung, alle bereits im
Source-Repo vorhanden.

**Anonymisierungs-Regeln (eingehalten für alle 10):**
- Header + Ticker + Firmenname bleiben (Fakt, öffentlich)
- `## CORE` und `## FAKTOREN`: TODO-Marker, keine Nik-spezifischen
  Bewertungen, keine Port1-/Sparplan-Bezüge, keine Trigger-Log-Verweise
- `## BEWERTUNG`: leer mit TODO-Markern, **keine** persönliche Bewertung,
  **keine** historischen Multiples, **keine** Analysten-Stand-Texte
- `## LIVE-DATA`: yfinance-Snapshot (universelle Fakten), mit TODO-Hinweis
  dass `valuation_refresh.py --apply` den Block beim ersten Run überschreibt
- `## EINORDNUNG`: leer, TODO-Marker (Portfolio-Rolle +
  These-Bezug sind subjektiv)
- `## QUELLEN`: generische IR-/Yahoo-Links, keine spezifischen Report-Daten

## Was bewusst NICHT im Public Release ist

- **Persönliche Sparplan-Daten** (`sparplan-tracking*.json`) — jeder
  Nutzer richtet sich seine eigene Datei ein (in `data/sparplan-tracking.json`).
- **Persönliche Watchlist-Analyse** (234 .md-Files im Source) — der
  Public Release liefert nur 10 leere Templates als Struktur-Beispiele.
- **Trigger-Log-Daten** — Working-Output, jeden Tag neu erzeugbar.
- **API-Keys** — in `config/config.json` (gitignored).

## Offene Punkte für v2

- `ANLEITUNG-aktien-scout-fuer-inge-marcel.md` könnte generalisiert werden
  ("ANLEITUNG: So scoutest du neue Aktien")
- `werkzeug/` könnte zu `tools/` generalisiert werden
- `test_financials.py` könnte aufgenommen werden mit CI-Setup
- Docker-Container / `pyproject.toml` für einfachere Installation
- i18n: README + UI nur Englisch aktuell; deutsche Übersetzung optional
