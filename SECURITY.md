# Security Policy

## Supported Versions

Aktuell wird nur die **neueste Hauptversion** auf `main` mit
Sicherheits-Patches versorgt. Ältere Versionen erhalten keine Updates.

| Version | Supported          |
|---------|--------------------|
| main    | :white_check_mark: |
| < main  | :x:                |

## Reporting a Vulnerability

Wenn du eine Sicherheitslücke findest, **bitte kein öffentliches Issue**
eröffnen — das gibt Angreifern Zeit, bevor ein Fix bereitsteht.

Stattdessen:

1. **Email** an `<maintainer-email>` (siehe Repo-Maintainer-Profil)
2. **GitHub Security Advisory** (Tab "Security" → "Advisories" → "New
   draft security advisory") — private, nur Maintainer sehen es

Was du uns geben solltest:
- Kurze Beschreibung der Lücke
- Reproduktions-Schritte (PoC-Code, falls vorhanden)
- Betroffene Version/Commit
- Geschätzte Schwere (informell reicht)

## Was du erwarten kannst

- **Antwort innerhalb 7 Tagen** (Best-Effort, kein SLA)
- **Koordinierter Disclosure** — wir veröffentlichen den Fix gemeinsam
  mit dir, sobald ein Patch bereitsteht
- **Credit in den Release-Notes** (sofern du möchtest)

## Out of Scope

- Best Practices, die du anders haben würdest (gerne als Diskussion)
- Rate-Limiting-Bypässe an öffentlichen APIs (yfinance etc.) — die sind
  nicht in unserer Kontrolle
- Abhängigkeits-Schwachstellen, die noch keinen Patch haben

Vielen Dank, dass du verantwortungsvoll reportest.
