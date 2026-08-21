## Was ändert sich?

Kurze Beschreibung (1-3 Sätze). Was war vorher, was ist nachher?

## Motivation / Kontext

Warum ist die Änderung nötig? Verlinke das Issue, falls vorhanden
(„closes #123" oder „fixes #123").

## Art der Änderung

- [ ] Bug-Fix (nicht-breaking, behebt ein Problem)
- [ ] Neues Feature (nicht-breaking, fügt Funktionalität hinzu)
- [ ] Breaking Change (fix oder feature, das bestehende API/Verhalten ändert)
- [ ] Doku-Update (nur Doku, kein Code)

## Wie wurde getestet?

- [ ] Lokal: `python -m unittest dashboard.tests.test_parser_variants -v` grün
- [ ] Manuell getestet (Schritte unten)
- [ ] CI grün (siehe Checks unten)

**Manuelle Test-Schritte:**
1. ...
2. ...
3. ...

## Checkliste

- [ ] Code folgt den Konventionen (siehe `CONTRIBUTING.md`)
- [ ] Eigene Review der Diff (keine Debug-Prints, keine Secrets)
- [ ] Tests hinzugefügt/angepasst, falls Logik sich geändert hat
- [ ] Doku aktualisiert, falls öffentliche API/CLI sich geändert hat
- [ ] Keine `print()`-Statements im Code (nur `logging` oder stderr)

## Screenshots / Output

Falls relevant: Screenshot, CLI-Output, Beispiel-`.md`-Diff.
