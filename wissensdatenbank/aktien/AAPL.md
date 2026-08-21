# Apple Inc. (AAPL)

> Template fuer den Portfoliobot Public Release.
> Erste Schritte: (1) `## CORE` und `## FAKTOREN` mit eigener Recherche befuellen,
> (2) `python scripts/valuation_refresh.py --apply` laufen lassen, um `## LIVE-DATA`
> und den Live-Score in `## BEWERTUNG` automatisch zu befuellen,
> (3) `## EINORDNUNG` und `## QUELLEN` ergaenzen.

**Sektor / Industrie:** Consumer Electronics / Software
**Markt:** US (Beispiel-Ticker, du kannst ihn anpassen oder loeschen)

## CORE
<!-- TODO: 2-4 Saetze zum Geschaeftsmodell, wichtigste Umsatztreiber,
     Margen-/Bilanz-Charakter. -->

## FAKTOREN
<!-- TODO: 2-3 Wachstumstreiber, 2-3 Hauptrisiken, kritische Abhaengigkeiten
     (Lieferanten, Kunden, Regulatorik). -->

## BEWERTUNG
<!-- Bewertungstext: manuell, ueberlebt jeden valuation_refresh-Lauf.
     Live-Score und PEG/KGV/52W-Komponenten werden automatisch befuellt
     (siehe "## LIVE-DATA" + "Live-Score-Zeilen" weiter unten). -->

## LIVE-DATA
<!-- Wird beim ersten `valuation_refresh.py --apply`-Lauf befuellt.
     Idempotent: weitere Laufe aktualisieren nur den Snapshot, kein
     zweiter Block. Schema siehe wissensdatenbank/Wiki-Daten-Schema.md. -->

- **Live-Score:** n/a
- **52W-Position:** n/a
- **PEG-Score:** n/a
- **KGV-Score:** n/a
- **Stand automatische Bewertung:** n/a

## EINORDNUNG
<!-- TODO: Welche Rolle spielt der Titel in deiner Strategie? Bezug zu deiner
     Investmentthese? Watchlist-Kandidat, Core-Position, Spekulation? -->

## QUELLEN
- Investor Relations: siehe EDGAR (https://www.sec.gov/cgi-bin/browse-edgar)
- Yahoo Finance: https://finance.yahoo.com/quote/AAPL
- Wikipedia (Branchen-Kontext, falls noetig)
