# Wiki-Daten-Schema — wissensdatenbank/aktien/*.md

Stand: 2026-08-21 · Erstellt von `valuation_refresh.py` (Portfoliobot)
Letzte Aktualisierung: 2026-08-21 (sanitized für Public Release)

Dieses Dokument beschreibt das erwartete Schema der Markdown-Dateien in
`wissensdatenbank/aktien/` und die automatisch erzeugten numerischen Blöcke,
damit Parser (insbesondere `dashboard/app.py`) auf denselben Strukturrahmen
aufbauen können.

## Dateistruktur (allgemein)

Jede `.md`-Datei folgt einem Abschnittsmodell:

```markdown
# <Firmenname> (<TICKER>)

## CORE
(freitextliche Beschreibung des Geschäftsmodells, Umsatztreiber, Marge/Bilanz)

## FAKTOREN
(Treiber, Risiken, Abhängigkeiten)

## BEWERTUNG
(qualitative Einschätzung + handgeschriebene Multiples/Konsens/Zeitstempel)

## LIVE-DATA
(yfinance-Snapshot ISO8601Z; erzeugt von valuation_refresh.py)

## EINORDNUNG
(Portfolio-Rolle, Bezug These)

## QUELLEN
(Links, Zugriffsdatum)
```

## BEWERTUNG-Block (manuell/redaktionell)

Der **BEWERTUNG-Block** enthält freitextliche Inhalte (keine automatische
Überschreibung). Automatisch überschrieben werden darin nur die folgenden
numerischen Live-Score-Zeilen (s. `valuation_refresh.py`):

```markdown
- **Live-Score:** X / 100 → Zone: <zone>
- **52W-Position:** X %
- **PEG-Score:** X / 100
- **KGV-Score:** X / 100
- **Stand automatische Bewertung:** <ISO8601Z>
```

Die Zeilen werden vollständig durch den neusten Durchlauf ersetzt; andere
Inhalte im BEWERTUNG-Block bleiben unberührt.

## LIVE-DATA-Block (automatisch, valuation_refresh.py)

Wird durch `scripts/valuation_refresh.py --apply` erzeugt und nach dem
BEWERTUNG-Block (vor Live-Score-Zeilen und vor `## EINORDNUNG`) eingefügt.
Idempotenz: bei wiederholten Aufrufen wird nur der Zeitstempel + die
Live-Score-Zeilen aktualisiert; kein zweiter Block wird eingefügt.

```markdown
## LIVE-DATA
(yfinance-Snapshot 2026-08-20T18:00:00Z)

**Bewertung:** KGV ttm X / fwd Y | PEG Z | P/S A | EV/Rev B | EV/EBITDA C
**Wachstum (j/j):** Earnings +X % | Revenue +Y %
**Margen:** Brutto X % | Operating Y % | Profit Z % | EBITDA A %
**Returns:** ROE X % | ROA Y % | D/E Z | Current A | Quick B
**Cashflow:** FCF X Mrd USD | OCF Y Mrd USD
**Analysten:** PT X USD low – high USD | recommendationKey | N Analysten
**Sentiment:** Insider X % | Institution Y % | Short Z %
**Technik:** 50d MA X | 200d MA Y | 52W-Change +Z %
```

### Felder und Formatierung

Alle Felder werden ohne Markdown-Formatierung innerhalb der Werte geschrieben;
nur die Label-Zeilen werden **fett** (`**Label:** …`).

| Kategorie      | Felder (yfinance-Key → Label)                                                                                                     | Format                                                                                                             | n/a-Konvention               |
|----------------|-----------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|------------------------------|
| Bewertung      | trailingPE → KGV ttm, forwardPE → KGV fwd, trailingPegRatio/pegRatio → PEG, priceToSalesTrailing12Months → P/S, enterpriseToRevenue → EV/Rev, enterpriseToEbitda → EV/EBITDA | 1 Dezimalstelle, Komma als Dezimaltrenner, ohne Einheit, **kein %**                                                | n/a                          |
| Wachstum (y/y) | earningsGrowth → Earnings, revenueGrowth → Revenue                                                                                | **yfinance Dezimalbruch × 100**, +X % / -Y % (Vorzeichen bei positiven Werten), 1 Dezimalstelle                   | n/a                          |
| Margen         | grossMargins, operatingMargins, profitMargins, ebitdaMargins                                                                     | **yfinance Dezimalbruch × 100**, X % (1 Dezimalstelle)                                                             | n/a                          |
| Returns        | returnOnEquity → ROE, returnOnAssets → ROA, debtToEquity → D/E, currentRatio → Current, quickRatio → Quick                    | **ROE/ROA: yfinance Dezimalbruch × 100**, in % (1 Dezimalstelle), D/E/Current/Quick als Zahl (kein %)            | n/a für fehlende Werte       |
| Cashflow       | freeCashflow → FCF, operatingCashflow → OCF                                                                                      | Rohbetrag in USD: **≥ 1 Mrd → "Mrd USD"**, sonst "Mio USD", Format: `X,1 Mrd USD`                                | n/a                          |
| Analysten      | targetMeanPrice → PT, targetLowPrice → low, targetHighPrice → high, recommendationKey → recommendationKey, numberOfAnalystOpinions → N | PT als Zahl (1 Dezimalstelle), Range low–high USD, Empfehlungsschlüssel direkt ohne Formatierung, N als Integer     | n/a für fehlende Unter-/Obergrenze |
| Sentiment      | heldPercentInsiders → Insider, heldPercentInstitutions → Institution, shortPercentOfFloat → Short                               | **yfinance Dezimalbruch × 100**, X % (1 Dezimalstelle)                                                             | n/a                          |
| Technik        | fiftyDayAverage → 50d MA, twoHundredDayAverage → 200d MA, fiftyTwoWeekChange → 52W-Change                                     | MA als Zahl (1 Dezimalstelle, **kein %**), **52W-Change: yfinance Dezimalbruch × 100**, +X % / -Y %              | n/a für fehlende Werte       |

### Prozent-Konvention (wichtig — Bug-Fix 2026-08-20)

yfinance liefert folgende Felder als **Dezimalbruch** (0.74 = 74%):

- earningsGrowth, revenueGrowth
- grossMargins, operatingMargins, profitMargins, ebitdaMargins
- returnOnEquity, returnOnAssets
- heldPercentInsiders, heldPercentInstitutions, shortPercentOfFloat
- fiftyTwoWeekChange

Diese werden von `valuation_refresh.py` mit **× 100 multipliziert** und als
Prozentzahl ausgegeben (z.B. 0.74 → `74,0 %`). Parser (Mavis/dashboard/app.py)
müssen darauf achten, dass diese Werte **nicht erneut × 100** multipliziert
werden.

### Hinweise für Parser (Mavis / Dashboard)

- **Prozentwerte**: Werte aus der Tabelle "Prozent-Konvention" sind bereits
  × 100 transformiert. Ein Parser, der diese Werte weiterverarbeitet, muss
  **nicht** erneut mit 100 multiplizieren. Direkt als Prozentzahl lesen.
- **Technische Indikatoren**: MA-Werte als Zahl (ohne Einheit, ohne %).
- **Cashflow**: Rohbetrag aus yfinance wird in Mrd. oder Mio. umgerechnet
  (`≥ 1 Mrd → "Mrd USD"`). Für Parser, die den Rohwert erwarten, ist der
  Rohbetrag nicht mehr direkt abrufbar — bitte eine neue Komponente in
  `app.py` für Roh-Cashflow ergänzen.
- **Analysten-PT-Bereich**: low–high wird als "X,XX – Y,YY USD" geschrieben;
  "n/a" wenn kein Range vorhanden. Rush/PDF-Felder: yfinance liefert
  `numberOfAnalystOpinions` als Float — in `N Analysten` gerundet.
- **Empfehlungsschlüssel**: `recommendationKey` wird unverändert übernommen
  (`strong_buy`, `buy`, `hold`, `sell`), nie als Markdown formatiert.
- **Zeitstempel**: `yfinance-Snapshot` verwendet ISO8601Z
  (`YYYY-MM-DDTHH:MM:SSZ`), nicht `+00:00` oder andere Varianten.
- **Fehlende Werte**: `n/a` (klein, kein Sternchen, kein Bindestrich).
  Parser-Patterns sollten auf `n/a` statt auf `—` oder `0` prüfen.
- **Bewertungs-Multiple**: KGV, PEG, EV/Rev etc. werden **ohne %** ausgegeben.

### Validierung

Nach `--apply` läuft `valuation_refresh.py` einen Selbstcheck: alle Files
sollten einen `## LIVE-DATA` Block enthalten. Verifikation:

```bash
# im Projekt-Root
grep -c "## LIVE-DATA" wissensdatenbank/aktien/*.md
```

Sollte die Anzahl der Ticker-Files ausgeben (eine Fundstelle pro File).

## Akzeptanz-Kriterien

1. Jede Datei in `wissensdatenbank/aktien/*.md` (ausser `README.md`)
   enthält nach einem `--apply`-Durchlauf einen `## LIVE-DATA`-Block.
2. Felder mit Werten ≥ 80 % (was yfinance auch liefert) werden ausgeschrieben;
   bei Verlust-Firmen werden PEG/KGV etc. als `n/a` geschrieben, nicht
   übersprungen.
3. Der Live-Score-Block (mit Score + Komponenten) bleibt funktional identisch.
4. Smoke-Test: `python scripts/valuation_refresh.py --apply` läuft ohne Fehler,
   exit code 0.
5. Verifikation: `grep -c "## LIVE-DATA" wissensdatenbank/aktien/*.md`
   zeigt 220 (aktuell: 220 aktualisierte Files, 13 ohne BEWERTUNG-Block).
6. **Prozent-Bug-Fix (2026-08-20)**: Margen/Zinsen/Wachstums/Sentiment-Werte
   müssen nach dem Fix korrekt mit × 100 multipliziert sein (z.B.
   Brutto 74,1 % statt 0,7 %).

## Koordination mit Mavis (Dashboard-Parser)

Mavis erweitert parallel die Parser-Patterns in `dashboard/app.py`, damit die
neuen Felder im UI sichtbar werden. Falls Felder benötigt werden, die geparst
werden sollen, gibt valuation_refresh.py das Schema (z.B. "PEG als Dezimalzahl,
n/a wenn None") in diesem Dokument an.

### Zu parsende Felder (Startset für Mavis)

- **Bewertung**: KGV ttm, KGV fwd, PEG, P/S, EV/Rev, EV/EBITDA (ohne %)
- **Wachstum**: Earnings y/y (+X %), Revenue y/y (+Y %)
- **Margen**: Brutto, Operating, Profit, EBITDA (alle × 100 aus yfinance)
- **Returns**: ROE, ROA (× 100 aus yfinance)
- **Cashflow**: FCF, OCF (formatiert als "X Mrd USD" — Parser muss die Einheit
  entfernen und den Zahlenwert extrahieren)
- **Analysten**: PT (Zahl), Analysten-Anzahl (Integer), recommendationKey
- **Sentiment**: Insider %, Institution %, Short % (alle × 100 aus yfinance)
- **Technik**: 50d MA, 200d MA (Zahlen), 52W-Change % (× 100 aus yfinance)

### Parser-Pitfalls

- Prozentwerte (Wachstum, Margen, ROE, ROA, Sentiment, 52W-Change) sind
  **bereits × 100** transformiert aus yfinance-Dezimalbruch. Nicht erneut
  mit 100 multiplizieren.
- Die Cashflow-Felder enthalten eine Einheit ("Mrd USD"/"Mio USD"); der Zahlen-
  wert steht vor der Einheit.
- Der PT-Bereich wird als "X,XX – Y,YY USD" geschrieben; die Einheit "USD"
  steht am Ende des Bereichs.
- `n/a` ist die konstante für fehlende Werte (niemals `—`, `0`, oder leer).
- Bewertungs-Multiple werden **ohne %** ausgegeben (KGV, PEG etc.).
