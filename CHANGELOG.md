# Changelog

Alle nennenswerten Änderungen an Portfoliobot. Format orientiert sich an
[Keep a Changelog](https://keepachangelog.com/), Versionierung folgt
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-08-21

### Erste öffentliche Version

Initial Public Release des Portfoliobot-Grundgerüsts als MIT-lizensiertes
Open-Source-Projekt.

**Inhalt**
- Dashboard (Flask + Jinja2 + anime.js + bleach + markdown)
- 25 Library-Module (Daten-Layer, yfinance-Wrapper, Scoring, Trigger,
  SEC-EDGAR, Broker, Macro, Fear&Greed, Regime, Fibonacci, …)
- 22 Scripts (valuation_refresh, trigger_check, banker_council, …)
- 10 LLM-Prompts (P1–P7 + Advocate + Guardrails)
- Wiki-Schema + 10 sanitized Ticker-Templates (NVDA, AAPL, MSFT, AMZN,
  GOOGL, TSLA, JPM, JNJ, XOM, PG) als Struktur-Beispiele
- Sanitisierter Build-Pfad: keine privaten Daten, keine API-Keys, alle
  Daten-Pfade innerhalb des Projekt-Roots

**Community**
- README mit Quick-Start, Architektur, Konfiguration, Customisation
- CONTRIBUTING.md (Setup, Tests, PR-Guidelines, Konventionen)
- CODE_OF_CONDUCT.md (Contributor Covenant v2.1)
- SECURITY.md (private Reporting-Wege, koordinierte Disclosure)
- GitHub-Issue-Templates (Bug, Feature-Request)
- GitHub-PR-Template
- CI-Workflow (Python 3.11/3.12 × Ubuntu/Windows/macOS)
- Dependabot-Konfiguration (pip + GitHub-Actions, wöchentlich)

**Lizenz**
- MIT

[Unreleased]: https://github.com/Anker94/portfoliobot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Anker94/portfoliobot/releases/tag/v0.1.0
