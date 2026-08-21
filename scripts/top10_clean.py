"""Bereinigte Top-10: Microcap-Rauschen raus, Score mit KZ-Cap."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import yfinance as yf
from Portfoliobot.scripts.trigger_check import trigger_check

# F1-Pass-Titel (Pos%<20) + Mindest-Kurs 5 USD + Analysten >= 10 (verhindert Microcap-Muell)
TICKER = [
    "PSIX", "ORCL", "PDD", "CPNG", "TCEHY", "NFLX",
    "1810.HK", "RWE", "FSLR", "ENPH", "BABA", "JD", "PG", "JNJ",
    "NOW", "ADBE", "PANW", "CRWD", "MSFT", "GOOGL", "AMZN", "SAP",
    "NVDA", "AMD", "TSM", "AVGO", "ANET", "ASML", "VRT", "NEE",
]


def main():
    print("TOP-10 EINSTIEGS-SCAN BEREINIGT - Kurs>=5, Analysten>=10 - 2026-08-16")
    print("=" * 95)
    results = []
    for t in TICKER:
        try:
            r = trigger_check(t)
            e = r["entscheidung"]
            sm = r["smart_money"]
            n_bull = sm.get("anzahl_bullish", 0)
            n_bear = sm.get("anzahl_bearish", 0)
            k = r["kurs"]
            sup = r["cluster"]["naechste_unterstuetzung"]["zentrum"]
            sup_pct = r["cluster"]["naechste_unterstuetzung"]["abstand_pct"]
            sup_staerke = r["cluster"]["naechste_unterstuetzung"]["staerke"]
            rsi = r["cluster"]["rsi_14"]
            fired = e["buy_trigger_fired"]
            quellen = ",".join([s["quelle"][:5] for s in sm.get("bullish_signale", [])])

            # F1-Daten aus Screening-Tabelle (verifiziert) statt yfinance-Info
            # Microcap-Titel explizit raus (Kurs < 5)
            try:
                info = yf.Ticker(t).info
                kz = info.get("targetMeanPrice")
                analysten = info.get("numberOfAnalystOpinions", 0) or 0
                kz_upside = ((kz - k) / k * 100) if kz and k else 0
            except Exception:
                kz = None
                kz_upside = 0
                analysten = 0

            # Microcap-Schutz: Kurs < 5 USD raus
            if k < 5:
                continue

            # KZ bei 100% capen (alles drueber ist Microcap-Wahnsinn)
            kz_capped = min(max(kz_upside, 0), 100)

            # Score OHNE Trigger-Bonus (auch ohne Trigger einstiegsfaehig)
            score = (
                (n_bull * 2.0)
                + (kz_capped / 10.0)
                - (abs(sup_pct) / 3.0)
                + (sup_staerke / 20.0)
                + (10 if fired else 0)
            )
            results.append({
                "ticker": t, "kurs": k, "sup": sup, "sup_pct": sup_pct,
                "sup_staerke": sup_staerke, "rsi": rsi,
                "n_bull": n_bull, "n_bear": n_bear, "fired": fired,
                "quellen": quellen, "kz_upside": kz_capped, "kz_roh": kz_upside,
                "analysten": int(analysten), "score": score,
            })
        except Exception as ex:
            print(f"ERR {t}: {type(ex).__name__}: {ex}")

    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"{'TICKER':8} {'KURS':>8} {'SUP':>8} {'DIST':>6} {'ST':>4} {'RSI':>4} {'B':>2} {'KZ%':>5} {'AN':>3} {'SCORE':>6}  SIG/TRIG")
    print("-" * 95)
    for i, r in enumerate(results[:15], 1):
        flag = "***BUY***" if r["fired"] else "   -    "
        print(
            f"{r['ticker']:8} {r['kurs']:8.2f} {r['sup']:8.2f} {r['sup_pct']:+5.1f}% "
            f"{r['sup_staerke']:4.1f} {r['rsi']:4.1f} {r['n_bull']:2d} {r['kz_upside']:4.0f}% "
            f"{r['analysten']:3d} {r['score']:5.2f}  {flag} {r['quellen']}"
        )

    print()
    print("=" * 95)
    print("TOP 10 BEREINIGT (sortiert nach Einstiegs-Score):")
    print("=" * 95)
    for i, r in enumerate(results[:10], 1):
        in_plan = "[im Plan]" if r["ticker"] in ("PSIX", "ORCL", "PDD", "CPNG", "TCEHY", "NFLX", "1810.HK", "VUL.AX", "RZLV") else ""
        print(
            f"{i:2}. {r['ticker']:8} Score {r['score']:5.2f}  "
            f"Kurs {r['kurs']:.2f} Sup {r['sup']:.2f} ({r['sup_pct']:+.1f}%)  "
            f"Bull {r['n_bull']} KZ +{r['kz_upside']:.0f}% RSI {r['rsi']:.0f}  {in_plan}"
        )


if __name__ == "__main__":
    main()
