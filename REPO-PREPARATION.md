# REPO-PREPARATION.md — Public-Release-Übersicht

> **Zweck:** Dokumentiert Scope, Aufbau und GitHub-About-Settings
> des Public Release. Stand: 2026-08-21.

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
| `lib/` | db.py, data_layer.py, market_data.py, portfolio_helpers.py, ... | Vollständig übernommen |
| `prompts/` | LLM-Prompts P1-P7, advocate, guardrails | Vollständig übernommen |
| `config/config.example.json` | Vorlage | Vollständig übernommen |
| `wissensdatenbank/Wiki-Daten-Schema.md` | Schema-Doku | Vollständig übernommen |
| `wissensdatenbank/README.md` | Kurzanleitung | Neu geschrieben |
| `wissensdatenbank/aktien/{NVDA,AAPL,MSFT,AMZN,GOOGL,TSLA,JPM,JNJ,XOM,PG}.md` | 10 sanitized Templates | Neu geschrieben |
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

## Was bewusst NICHT im Public Release ist

- **Persönliche Sparplan-Daten** (`sparplan-tracking*.json`) — jeder
  Nutzer richtet sich seine eigene Datei ein (in `data/sparplan-tracking.json`).
- **Persönliche Watchlist-Analyse** (234 .md-Files im Source) — der
  Public Release liefert nur 10 leere Templates als Struktur-Beispiele.
- **Trigger-Log-Daten** — Working-Output, jeden Tag neu erzeugbar.
- **API-Keys** — in `config/config.json` (gitignored).

## GitHub-About-Settings (nach'm Push auf github.com setzen)

### Description (gewählt: Englisch, international)

```
Self-hosted stock-watchlist + auto-scoring dashboard. Pulls live data
from yfinance, scores each ticker 0-100, runs entirely on your machine.
MIT-licensed, no API keys required for core features.
```

### Topics (12, in Reihenfolge der Wichtigkeit)

```
yfinance
flask
python
stock-screener
watchlist
scoring
self-hosted
personal-finance
markdown-wiki
mit-license
fundamental-analysis
ticker
```

### Repo-Features (auf GitHub-Seite setzen)

| Feature | Status |
|---|---|
| Wikis | ❌ ausschalten (eigene `wissensdatenbank/` reicht) |
| Projects | ❌ ausschalten |
| Discussions | ✅ anschalten (passt zu CONTRIBUTING.md) |
| Sponsorship | optional, nur wenn gewünscht |
| Branch-Protection auf `main` | „Require pull request review" + „Require status checks to pass" → erzwingt CI |

## Offene Punkte für v2

- `ANLEITUNG-aktien-scout-fuer-inge-marcel.md` könnte generalisiert werden
  ("ANLEITUNG: So scoutest du neue Aktien")
- `werkzeug/` könnte zu `tools/` generalisiert werden
- `test_financials.py` könnte aufgenommen werden mit CI-Setup
- Docker-Container / `pyproject.toml` für einfachere Installation
- i18n: README + UI nur Englisch aktuell; deutsche Übersetzung optional
