# Portfoliobot

[![Tests](https://github.com/<owner>/portfoliobot/actions/workflows/test.yml/badge.svg)](https://github.com/<owner>/portfoliobot/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A self-hosted stock-watchlist + scoring dashboard for private investors. Pulls
live data from `yfinance`, scores each ticker on a 0-100 scale, and serves
everything in a local Flask dashboard. The whole pipeline (refresh scripts,
scoring, dashboard) runs on your own machine — no SaaS, no telemetry, no
external API keys required for the core features.

## Features

- **Live-data refresh** for any watchlist (single command, idempotent,
  resume-safe). Updates a structured `## LIVE-DATA` block in each ticker
  markdown file with yfinance snapshots: KGV/PEG/P/S/EV-Rev/EV-EBITDA,
  margins, growth, returns, cashflow, analyst targets, insider/institution
  ownership, 52-week position.
- **Auto-scoring** (0-100, zone-tagged) computed from the LIVE-DATA block —
  PEG-, KGV-, 52-week-position-based, refreshed in the same run.
- **Flask dashboard** (mobile-responsive, dark-mode) with watchlist
  overview, ticker deepdive, sparplan tracker, trigger log, admin console.
- **Admin console** for manual triggers of every refresh job (sync and
  async/background variants) with live status polling.
- **Sparplan tracker** (German: "Savings plan") — weekly recurring buys with
  per-track performance attribution.
- **Trigger engine** — insider-activity, 13F, options-flow, macro-driver
  signals on every ticker.
- **Wissensdatenbank** — markdown-based wiki where each ticker has a
  structured `.md` file combining manual analysis with auto-refreshed live
  data.

## Quick start

```bash
# 1. Clone the repo
git clone https://github.com/<your-org>/portfoliobot.git
cd portfoliobot

# 2. Create a virtualenv and install dependencies
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt

# 3. Set up your config (copy the example, fill in keys)
cp config/config.example.json config/config.json
# edit config/config.json — see "Configuration" below

# 4. Initialise the database
python scripts/migrate_v5.py   # or whichever is the latest migration

# 5. Seed the watchlist
# Put your ticker .md files into wissensdatenbank/aktien/ (10 templates are
# shipped as examples; see wissensdatenbank/README.md)

# 6. Run the dashboard
python dashboard/app.py
# → open http://127.0.0.1:5000

# 7. Trigger a refresh from the admin page (or CLI)
python scripts/valuation_refresh.py --apply
```

## Architecture

```
portfoliobot/
├── dashboard/              Flask app (Jinja2 + anime.js + bleach)
│   ├── app.py              routes, admin-job runner, scoring parser
│   ├── templates/          HTML pages
│   ├── static/             CSS/JS assets
│   └── tests/              parser + smoke tests
├── scripts/                CLI entrypoints (one per job)
│   ├── valuation_refresh.py        refresh LIVE-DATA + scores
│   ├── sparplan_perf_refresh.py    refresh sparplan performance
│   ├── trigger_check.py            insider/13F/options check
│   ├── banker_council.py           5-banker screening
│   └── ...
├── lib/                    core data layer
│   ├── db.py               SQLite wrapper (positionen, empfehlungen, ...)
│   ├── data_layer.py       yfinance get_performance + aggregation
│   ├── market_data.py      symbol resolution, suffix fallbacks
│   ├── portfolio_helpers.py  HERMES_JOBS config, dashboard helpers
│   └── ...
├── prompts/                LLM prompts (P1-P7 + advocate)
├── config/
│   ├── config.example.json template — copy to config.json
│   └── config.json         (gitignored, your secrets)
├── wissensdatenbank/
│   ├── Wiki-Daten-Schema.md  schema for ticker .md files
│   └── aktien/              ticker .md files (your watchlist)
├── data/                   runtime data (gitignored, auto-created)
│   ├── stock.db            SQLite database
│   ├── sparplan-tracking.json
│   └── ...
├── .gitignore
├── LICENSE                 MIT
├── README.md               this file
├── REPO-PREPARATION.md     history of sanitisation + scope decisions
└── requirements.txt
```

### Data flow

```
   yfinance ──┐
              │
   SEC EDGAR ─┼─→ scripts/valuation_refresh.py
   13F data ──┤         │
              │         ▼
              │   wissensdatenbank/aktien/*.md   ←   "## LIVE-DATA" block
              │         │                            + Live-Score in BEWERTUNG
              │         │
              │         ▼
              └──→ dashboard/app.py (parser) ──→ HTML
                        │
                        ▼
                   http://127.0.0.1:5000
```

The `## LIVE-DATA` block is the single source of truth for both prices and
score inputs. This is intentional: separating prices and scores into two
data paths would invite drift. The `--apply` mode of `valuation_refresh.py`
overwrites the existing block (idempotent — safe to re-run on every refresh).

## Configuration

`config/config.json` (copy from `config.example.json`):

| Key | Required | Purpose |
|---|---|---|
| `db_path` | yes | Path to SQLite DB, default `data/stock.db` |
| `llm.provider` | for LLM jobs | `mock` for testing, or your LLM provider |
| `llm.api_key` | for LLM jobs | Empty string if using `mock` |
| `telegram.bot_token` / `.chat_id` | for Telegram jobs | Optional, for daily reports |
| `finnhub_api_key` | for trigger checks | Optional, improves coverage |
| `broker.provider` | for paper trading | `paper` (offline simulator, no key) or `alpaca_paper` |
| `broker.startkapital` | for paper trading | Starting capital in your account currency |

The scoring pipeline (`valuation_refresh.py`) does **not** require any API
keys — it uses `yfinance` which is unauthenticated for most retail
endpoints. LLM jobs (screening, council) require keys; the core refresh
works without them.

## Scoring

The auto-score is a weighted combination of PEG, KGV, and 52-week position
(see `dashboard/app.py` → `score_valuation()`). It produces:

- a numeric score (0-100),
- a zone label (`teuer`, `neutral`, `guenstig`),
- per-component sub-scores (PEG-Score, KGV-Score, 52W-Position).

Zone thresholds are tuned for medium-to-long-term buy-and-hold orientation,
not short-term trading. The score is meant as a quick filter, not a
substitute for actual analysis — that's what the rest of the markdown
(`## CORE`, `## FAKTOREN`, `## EINORDNUNG`) is for.

## Data sources

- **yfinance** — prices, fundamentals, multiples, analyst targets, insider
  ownership. Primary source for LIVE-DATA.
- **SEC EDGAR** — 13F filings, insider transactions. Used by `trigger_check.py`.
- **FRED / macro APIs** — macro drivers, regime classification.
- **News feeds** — for sentiment + event watch.

The code falls back gracefully when a data source is missing: missing
fields render as `n/a` in LIVE-DATA, missing tickers are skipped, and the
run continues.

## Customisation

- **Add tickers:** create a new `.md` file in `wissensdatenbank/aktien/`
  following the schema. Next `valuation_refresh.py --apply` will populate
  LIVE-DATA + Live-Score.
- **Tune the score:** edit `score_valuation()` in `dashboard/app.py` or
  `lib/portfolio_helpers.py`. The function returns `(score, zone, sub)` —
  sub-scores are exposed for per-ticker breakdown in the UI.
- **Custom refresh job:** add an entry to `HERMES_JOBS` in
  `lib/portfolio_helpers.py` with `script`, `args`, `risk`, `desc`, and
  (optionally) `async` for background execution. The admin page
  auto-renders a button per entry.
- **Multiple sparplan tracks:** create separate `sparplan-tracking.json`
  files (e.g. `-mavis`, `-banker`) and reference them in
  `lib/portfolio_helpers.py` → `SPARPLAN_FILES`.

## Contributing

Beiträge sind willkommen — Bugs, Doku-Korrekturen, neue Features,
neue Ticker-Templates. Bitte lies [CONTRIBUTING.md](CONTRIBUTING.md)
vor dem ersten PR.

- **Bug melden:** [Issue-Tracker](https://github.com/<owner>/portfoliobot/issues/new?template=bug_report.md)
- **Feature vorschlagen:** [Feature-Request](https://github.com/<owner>/portfoliobot/issues/new?template=feature_request.md)
- **Sicherheitslücke:** siehe [SECURITY.md](SECURITY.md) — **bitte kein
  öffentliches Issue**, koordinierte Disclosure bevorzugt

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

Built and battle-tested as a personal-tooling project. The
sanitised public version ships with 10 example ticker templates (NVDA,
AAPL, MSFT, AMZN, GOOGL, TSLA, JPM, JNJ, XOM, PG) so you can see the
schema and the LIVE-DATA layout before adding your own watchlist.
