# Contributing to Portfoliobot

Vielen Dank für dein Interesse! Portfoliobot ist ein offenes Tool, das
von der Community weiterentwickelt wird. Jeder Beitrag — Bug-Report,
Doku-Korrektur, neues Feature, neuer Ticker-Template — ist willkommen.

## Quick links

- **Bug melden:** [Issue-Tracker](https://github.com/<owner>/portfoliobot/issues/new?template=bug_report.md)
- **Feature vorschlagen:** [Feature-Request](https://github.com/<owner>/portfoliobot/issues/new?template=feature_request.md)
- **Sicherheitslücke melden:** siehe [SECURITY.md](SECURITY.md)
- **Diskussion / Fragen:** [Discussions](https://github.com/<owner>/portfoliobot/discussions)

## Development-Setup

```bash
git clone https://github.com/<owner>/portfoliobot.git
cd portfoliobot
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/config.example.json config/config.json   # Keys eintragen, optional
```

## Tests ausführen

```bash
python -m unittest dashboard.tests.test_parser_variants -v
```

CI läuft automatisch bei jedem Push (siehe `.github/workflows/test.yml`).

## Pull-Request-Guidelines

1. **Fork + Feature-Branch** (`git checkout -b feature/xy`)
2. **Commits klein und fokussiert** halten
3. **Tests laufen lokal grün**, bevor du pushst
4. **PR-Template ausfüllen** (siehe `.github/PULL_REQUEST_TEMPLATE.md`)
5. **Auf Review warten** — Squash-Merge nach Approval

## Coding-Konventionen

- Python 3.11+ (Type-Hints, walrus-Operator, etc.)
- Modul-Docstrings mit Zweck + Pfad-Konvention
- Funktionen klein halten, Pure-Functions wo möglich
- Tests mit `unittest` (kein pytest nötig für v1)
- Commit-Messages auf Deutsch oder Englisch, kurz und aussagekräftig
- Keine secrets, keine API-Keys, keine personenbezogenen Daten committen

## Was wir aktuell brauchen

Schau in die [Issues](https://github.com/<owner>/portfoliobot/issues)
mit Label `good first issue` — das sind Beiträge, die ohne tiefes
Code-Verständnis gehen (Doku, einfache Bug-Fixes, neue Templates).

## Code of Conduct

Sei respektvoll. Konstruktive Kritik ja, persönliche Angriffe nein.
Maintainer behalten sich das Recht vor, Beiträge abzulehnen, die nicht
zum Projekt passen.
