"""Robuster JSON-Parser fuer LLM-Antworten.

Claude/andere Modelle wrappen JSON oft in Markdown-Codebloecke (```json ... ```),
geben manchmal ein Array statt eines Objekts zurueck, oder texten davor/danach.
Siehe SKILL.md Pitfall 'LLM JSON-Parsing'.
"""
import re
import json


def parse_llm_json(text, fallback=None):
    """Liefert ein dict (oder list). Bei Fehler: fallback oder {}."""
    if text is None:
        return fallback if fallback is not None else {}
    s = text.strip()

    # 1) Markdown-Fences entfernen
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL | re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()

    # 2) Array -> erstes Element (wenn dict)
    arr = re.search(r"^\[\s*(\{.*\})\s*\]$", s, re.DOTALL)
    if arr:
        s = arr.group(1)

    # 3) Erstes { ... } substring extrahieren (falls Text drumherum)
    if "{" in s:
        start = s.index("{")
        # suche passendes schliessendes } (tiefen-zaehlend)
        depth = 0
        end = -1
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
        if end > start:
            s = s[start:end + 1]

    try:
        return json.loads(s)
    except Exception:
        # Regex-Fallback: versuche alle "key": wert Paare zu extrahieren
        try:
            obj = {}
            for m in re.finditer(r'"([^"]+)"\s*:\s*("([^"]*)"|[\d.\-]+|true|false|null|\[[^\]]*\]|\{[^}]*\})', s):
                key = m.group(1)
                raw = m.group(2)
                try:
                    val = json.loads(raw)
                except Exception:
                    val = raw
                obj[key] = val
            if obj:
                return obj
        except Exception:
            pass
        return fallback if fallback is not None else {}


if __name__ == "__main__":
    samples = [
        '```json\n{"typ":"KAUFEN","confidence":"hoch"}\n```',
        'Hier ist die Analyse:\n[{"typ":"HALTEN"}]\nNoch ein Satz.',
        'Kein JSON hier',
    ]
    for s in samples:
        print(repr(s), "->", parse_llm_json(s))
