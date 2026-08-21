# wissensdatenbank/

Ticker-Wiki fuer den Portfoliobot. Jeder Watchlist-Eintrag ist eine
Markdown-Datei in `aktien/`.

## Schnellstart

1. **Beispiele ansehen** — die 10 Ticker in `aktien/` (NVDA, AAPL, MSFT,
   AMZN, GOOGL, TSLA, JPM, JNJ, XOM, PG) sind sanitisierte Templates mit
   TODO-Markern. Sie zeigen die Struktur, sind aber noch leer.
2. **Eigene Watchlist aufbauen** — entweder die Beispiele nutzen und mit
   eigener Recherche befuellen, oder neue `.md`-Dateien in `aktien/`
   anlegen.
3. **Live-Daten + Score holen** — `python scripts/valuation_refresh.py --apply`
   laufen lassen. Das Skript schreibt/aktualisiert den `## LIVE-DATA`-Block
   und den Live-Score in jeder Datei mit `## BEWERTUNG`-Block. Idempotent
   — sicher bei jedem Refresh.

## Datei-Schema

Siehe [Wiki-Daten-Schema.md](./Wiki-Daten-Schema.md) fuer die vollstaendige
Spezifikation. Kurzfassung:

| Sektion | Inhalt | Wird befuellt durch |
|---|---|---|
| `# <Name> (<TICKER>)` | Header (Fakt) | manuell |
| `## CORE` | Geschaeftsmodell, Treiber, Margen | manuell (Recherche) |
| `## FAKTOREN` | Wachstum, Risiken, Abhaengigkeiten | manuell (Recherche) |
| `## BEWERTUNG` | qualitative Bewertung + Live-Score-Zeilen | manuell + automatisch |
| `## LIVE-DATA` | yfinance-Snapshot (KGV, PEG, Margen, Analysten, ...) | automatisch |
| `## EINORDNUNG` | Portfolio-Rolle, Strategie-Bezug | manuell |
| `## QUELLEN` | IR-Links, Recherche-Quellen | manuell |

## Ticker-Symbole

Der Dateiname ist der Ticker in Grossbuchstaben (ohne Suffix). yfinance
loest den Ticker automatisch auf; Suffixe wie `.T` (Tokio), `.DE`
(XETRA), `.PA` (Paris) werden ueber `_SUFFIXES` in
`scripts/valuation_refresh.py` probiert, bis einer funktioniert.

## Parser-Hinweise

Der Dashboard-Parser (`dashboard/app.py` → `parse_md_kpis()`) liest
ausschliesslich den `## LIVE-DATA`-Block und die Live-Score-Zeilen im
`## BEWERTUNG`-Block. Der manuelle Bewertungstext bleibt unberuehrt.

Felder ohne Wert rendern als `n/a` — kein Skip der Datei.

## Beispiel-Templates loeschen

Sobald du deine eigene Watchlist hast, kannst du die 10 Beispiel-Ticker
einfach loeschen. Sie dienen nur als Struktur-Referenz.
