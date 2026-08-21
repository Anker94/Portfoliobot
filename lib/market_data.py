"""Marktdaten-Layer fuer das Dashboard-Deep-Dive-Feature.

Abstraktion: heute yfinance, spaeter ggf. Pipeline-Cache von Hermes.
Liefert OHLCV + technische Indikatoren als JSON-serialisierbares Dict,
damit das Frontend (Plotly im Browser) es ohne weitere Python-Logik
rendern kann.

Datentyp-Konvention: alle Zahlen als `float` oder `None`. Zeitstempel
als ISO-String. NaN/NaT werden vor der Rueckgabe zu `None`.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import yfinance as yf


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
DEFAULT_PERIOD = "6mo"  # 6 Monate reichen fuer MA200 + saubere Signale
INDICATOR_PERIODS = {"ma20": 20, "ma50": 50, "ma200": 200, "rsi14": 14, "vol_ma20": 20}
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2.0
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
WINDOW_52W = 252  # ~1 Jahr Handelstage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_float(v) -> Optional[float]:
    """NaN/NaT/inf sicher zu None oder float konvertieren."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _series_to_list(s: pd.Series) -> list[Optional[float]]:
    """pd.Series -> Liste[float|None], NaN/NaT zu None, Index zu ISO-Datum."""
    out: list[Optional[float]] = []
    for v in s:
        out.append(_to_float(v))
    return out


def _index_to_iso(idx) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in idx]


def _pct_vs_ma(price: Optional[float], ma: Optional[float]) -> Optional[float]:
    if price is None or ma is None or ma == 0:
        return None
    return (price - ma) / ma * 100.0


# ---------------------------------------------------------------------------
# Indikatoren (pandas / numpy, keine externen Libs)
# ---------------------------------------------------------------------------
def _sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(window=n, min_periods=1).mean()


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=1).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0.0, 1e-12)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = _sma(close, n)
    std = close.rolling(window=n, min_periods=1).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    return upper, mid, lower


# ---------------------------------------------------------------------------
# Signale (einfache, klassische Daumenregeln)
# ---------------------------------------------------------------------------
def _rsi_signal(rsi: Optional[float]) -> dict:
    if rsi is None:
        return {"label": "n/a", "tone": "neutral"}
    if rsi >= 70:
        return {"label": f"ueberkauft ({rsi:.1f})", "tone": "sell"}
    if rsi <= 30:
        return {"label": f"ueberverkauft ({rsi:.1f})", "tone": "buy"}
    if rsi >= 60:
        return {"label": f"bullisch ({rsi:.1f})", "tone": "buy"}
    if rsi <= 40:
        return {"label": f"baerisch ({rsi:.1f})", "tone": "sell"}
    return {"label": f"neutral ({rsi:.1f})", "tone": "neutral"}


def _macd_signal(macd: Optional[float], signal: Optional[float], hist: Optional[float]) -> dict:
    if macd is None or signal is None:
        return {"label": "n/a", "tone": "neutral"}
    cross = "ueber" if macd > signal else "unter"
    tone = "buy" if macd > signal else "sell"
    if hist is not None and abs(hist) < 0.01:
        tone = "neutral"
    return {
        "label": f"MACD {cross} Signal ({hist:+.2f})" if hist is not None else f"MACD {cross} Signal",
        "tone": tone,
    }


# ---------------------------------------------------------------------------
# Ticker-Resolution
# ---------------------------------------------------------------------------
# Bekannte Boersensuffixe -- werden bei der Aufloesung von der Basis abgezogen,
# damit der Suffix-Stripping-Bug (Suffix an vollen Ticker angehaengt -> "DR0.DE.F")
# nicht mehr auftritt. Wiki-Files koennen den Suffix im Namen tragen (z.B. "DR0.DE"
# fuer XETRA-gelistete Dermapharm), aber die Resolution muss an der Basis ansetzen.
SUFFIX_FALLBACKS = (
    ".AX", ".HK", ".L", ".DE", ".T", ".PA", ".MI", ".AS",
    ".KS",  # Korea (KRX)
    ".CO",  # Copenhagen (Nasdaq Copenhagen)
    ".TO",  # Toronto Stock Exchange
    ".V",   # TSX Venture
    ".CN",  # Canadian Securities Exchange (CSE)
    ".LS",  # Euronext Lisbon
    ".MC",  # Madrid (BME)
)


def _split_ticker(ticker: str) -> tuple[str, str]:
    """Trennt bekannten Boersen-Suffix von der Basis. Beispiele:
    "DR0.DE"  -> ("DR0", ".DE")
    "006400.KS" -> ("006400", ".KS")
    "NVDA"    -> ("NVDA", "")
    "BRK.B"   -> ("BRK.B", "")  # Klassen-Suffix, kein Boersen-Suffix
    """
    if "." not in ticker:
        return ticker, ""
    base, _, suffix = ticker.rpartition(".")
    suffix = "." + suffix
    if suffix.upper() in {s.upper() for s in SUFFIX_FALLBACKS}:
        return base, suffix
    return ticker, ""


def _resolve_ticker(ticker: str) -> tuple[str, str]:
    """Versucht bare-ticker zuerst, faellt auf gaengige Boersensuffixe zurueck.

    Wenn der ticker einen bekannten Boersen-Suffix enthaelt (z.B. "DR0.DE"),
    wird die Basis ("DR0") an die Suffix-Liste angehaengt, statt den vollen
    Ticker zu duplizieren (alter Bug: "DR0.DE" + ".F" = "DR0.DE.F").

    Rueckgabe: (effektiv_verwendeter_ticker, modus)
        modus in {"direct", "suffix-fallback", "all-failed"}
    """
    base, known_suffix = _split_ticker(ticker)
    # 1) Voller Ticker (inkl. Suffix) zuerst versuchen -- das ist bei "DR0.DE" etc.
    # bereits die kanonische yfinance-Form und liefert meistens direkt Daten.
    try:
        t = yf.Ticker(ticker)
        h = t.history(period="5d", auto_adjust=False)
        if h is not None and not h.empty:
            return ticker, "direct"
    except Exception:
        pass
    # 2) Wenn nicht, Basis + Suffix-Liste durchprobieren. Wenn der Ticker schon
    # einen Suffix hatte, diesen NICHT erneut an die Basis haengen.
    skip = {known_suffix.upper()} if known_suffix else set()
    for suffix in SUFFIX_FALLBACKS:
        if suffix.upper() in skip:
            continue
        candidate = f"{base}{suffix}"
        try:
            t = yf.Ticker(candidate)
            h = t.history(period="5d", auto_adjust=False)
            if h is not None and not h.empty:
                return candidate, "suffix-fallback"
        except Exception:
            continue
    return ticker, "all-failed"


# ---------------------------------------------------------------------------
# Time-based in-memory cache (5 min TTL, Mavis 2026-08-21)
# ---------------------------------------------------------------------------
import threading
import time as _time

_FETCH_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_FETCH_CACHE_LOCK = threading.Lock()
_FETCH_CACHE_TTL_S = 300  # 5 Minuten


def _cache_get(key: tuple[str, str]) -> dict | None:
    """Holt gecachtes fetch_market_state-Ergebnis, None wenn stale/missing."""
    with _FETCH_CACHE_LOCK:
        entry = _FETCH_CACHE.get(key)
    if entry is None:
        return None
    ts, payload = entry
    if _time.time() - ts > _FETCH_CACHE_TTL_S:
        return None
    return payload


def _cache_put(key: tuple[str, str], payload: dict) -> None:
    with _FETCH_CACHE_LOCK:
        _FETCH_CACHE[key] = (_time.time(), payload)


def cache_clear() -> int:
    """Manuelle Cache-Invalidierung (fuer Tests / forced refresh)."""
    with _FETCH_CACHE_LOCK:
        n = len(_FETCH_CACHE)
        _FETCH_CACHE.clear()
        return n


def cache_stats() -> dict:
    """Fuer Debug-Output im /healthz oder Admin-Panel."""
    with _FETCH_CACHE_LOCK:
        now = _time.time()
        fresh = stale = 0
        for ts, _ in _FETCH_CACHE.values():
            if now - ts <= _FETCH_CACHE_TTL_S:
                fresh += 1
            else:
                stale += 1
        return {"size": len(_FETCH_CACHE), "fresh": fresh, "stale": stale, "ttl_s": _FETCH_CACHE_TTL_S}


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------
def fetch_market_state(ticker: str, period: str = DEFAULT_PERIOD) -> dict:
    """Lade OHLCV + Indikatoren fuer ticker. Fehler werden ins `error`-Feld
    geschrieben, der Aufrufer entscheidet, was er anzeigt.

    Cache (Mavis, 2026-08-21): 5-Min-TTL In-Memory-Cache auf (ticker, period).
    Spart bei wiederholten Dashboard-Refreshs die yfinance-Roundtrips.
    manuelle Invalidierung via `market_data.cache_clear()`.
    """
    cache_key = (ticker.upper(), period)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    out: dict[str, Any] = {
        "ok": False,
        "ticker": ticker,
        "effective_ticker": ticker,
        "resolution_mode": "direct",
        "error": None,
        "fetched_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "period": period,
    }
    effective_ticker, mode = _resolve_ticker(ticker)
    if mode == "all-failed":
        out["error"] = f"Keine Daten fuer {ticker} (auch mit Suffix-Fallback .AX/.HK/.L/.DE/.T/.PA/.MI/.AS nicht gefunden)"
        return out
    try:
        t = yf.Ticker(effective_ticker)
        hist = t.history(period=period, auto_adjust=False)
    except Exception as exc:
        out["error"] = f"yfinance-Fetch fehlgeschlagen: {type(exc).__name__}: {exc}"
        return out

    if hist is None or hist.empty:
        out["error"] = f"Keine Daten fuer {effective_ticker} (Zeitraum: {period})"
        return out

    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)

    close = hist["Close"].astype(float)
    high = hist["High"].astype(float)
    low = hist["Low"].astype(float)
    opn = hist["Open"].astype(float)
    vol = hist["Volume"].astype(float)

    ma20 = _sma(close, INDICATOR_PERIODS["ma20"])
    ma50 = _sma(close, INDICATOR_PERIODS["ma50"])
    ma200 = _sma(close, INDICATOR_PERIODS["ma200"])
    boll_up, boll_mid, boll_low = _bollinger(close, BOLLINGER_PERIOD, BOLLINGER_STD)
    vol_ma20 = _sma(vol, INDICATOR_PERIODS["vol_ma20"])
    rsi = _rsi(close, INDICATOR_PERIODS["rsi14"])
    macd_line, macd_signal_line, macd_hist = _macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)

    last_close = _to_float(close.iloc[-1])
    last_volume = _to_float(vol.iloc[-1])
    last_ma20 = _to_float(ma20.iloc[-1])
    last_ma50 = _to_float(ma50.iloc[-1])
    last_ma200 = _to_float(ma200.iloc[-1])
    last_boll_up = _to_float(boll_up.iloc[-1])
    last_boll_low = _to_float(boll_low.iloc[-1])
    last_rsi = _to_float(rsi.iloc[-1])
    last_macd = _to_float(macd_line.iloc[-1])
    last_macd_sig = _to_float(macd_signal_line.iloc[-1])
    last_macd_hist = _to_float(macd_hist.iloc[-1])
    last_vol_ma20 = _to_float(vol_ma20.iloc[-1])

    last_252 = close.tail(WINDOW_52W)
    high_52w = _to_float(last_252.max())
    low_52w = _to_float(last_252.min())
    pos_52w = None
    if high_52w is not None and low_52w is not None and high_52w != low_52w and last_close is not None:
        pos_52w = (last_close - low_52w) / (high_52w - low_52w) * 100.0

    boll_pb = None
    if (
        last_boll_up is not None
        and last_boll_low is not None
        and (last_boll_up - last_boll_low) != 0
        and last_close is not None
    ):
        boll_pb = (last_close - last_boll_low) / (last_boll_up - last_boll_low) * 100.0

    # MA-Trend
    if last_ma200 is None or last_close is None:
        ma_trend = {"label": "n/a", "tone": "neutral"}
    elif last_close > last_ma200 * 1.02:
        ma_trend = {"label": f"ueber MA200 (+{_pct_vs_ma(last_close, last_ma200):.1f}%)", "tone": "buy"}
    elif last_close < last_ma200 * 0.98:
        ma_trend = {"label": f"unter MA200 ({_pct_vs_ma(last_close, last_ma200):.1f}%)", "tone": "sell"}
    else:
        ma_trend = {"label": f"um MA200 ({_pct_vs_ma(last_close, last_ma200):.1f}%)", "tone": "neutral"}

    out.update(
        {
            "ok": True,
            "ticker": effective_ticker,
            "effective_ticker": effective_ticker,
            "resolution_mode": mode,
            "bars": int(len(hist)),
            "dates": _index_to_iso(hist.index),
            "ohlc": {
                "open": _series_to_list(opn),
                "high": _series_to_list(high),
                "low": _series_to_list(low),
                "close": _series_to_list(close),
                "volume": _series_to_list(vol),
            },
            "indicators": {
                "ma20": _series_to_list(ma20),
                "ma50": _series_to_list(ma50),
                "ma200": _series_to_list(ma200),
                "boll_upper": _series_to_list(boll_up),
                "boll_mid": _series_to_list(boll_mid),
                "boll_lower": _series_to_list(boll_low),
                "vol_ma20": _series_to_list(vol_ma20),
            },
            "summary": {
                "last_close": last_close,
                "last_volume": int(last_volume) if last_volume is not None else None,
                "rsi14": last_rsi,
                "macd": last_macd,
                "macd_signal": last_macd_sig,
                "macd_hist": last_macd_hist,
                "ma20": last_ma20,
                "ma50": last_ma50,
                "ma200": last_ma200,
                "vol_ma20": last_vol_ma20,
                "high_52w": high_52w,
                "low_52w": low_52w,
                "pos_52w_pct": pos_52w,
                "pct_vs_ma20": _pct_vs_ma(last_close, last_ma20),
                "pct_vs_ma50": _pct_vs_ma(last_close, last_ma50),
                "pct_vs_ma200": _pct_vs_ma(last_close, last_ma200),
                "boll_pct_b": boll_pb,
                "signals": {
                    "rsi": _rsi_signal(last_rsi),
                    "macd": _macd_signal(last_macd, last_macd_sig, last_macd_hist),
                    "ma_trend": ma_trend,
                },
            },
        }
    )
    # Cache-Store: nur erfolgreiche Fetchs cachen (errors sollen retry-faehig bleiben)
    _cache_put(cache_key, out)
    return out


# ---------------------------------------------------------------------------
# Markdown-Export
# ---------------------------------------------------------------------------
def market_state_to_markdown(state: dict, ticker_label: str | None = None) -> str:
    if not state.get("ok"):
        return f"# Deep-Dive {state.get('ticker', '?')}\n\nFehler: {state.get('error', 'unbekannt')}\n"

    s = state["summary"]
    sigs = s.get("signals", {})
    lines: list[str] = []
    label = ticker_label or state["ticker"]
    lines.append(f"# Deep-Dive {label}")
    lines.append("")
    lines.append(f"- Ticker: `{state['ticker']}`")
    lines.append(f"- Zeitraum: {state['period']} ({state['bars']} Bars)")
    lines.append(f"- Abgerufen: {state['fetched_at']}")
    if s.get("last_close") is not None:
        lines.append(f"- Letzter Schlusskurs: **{s['last_close']:.2f}**")
    if s.get("last_volume") is not None:
        lines.append(f"- Volumen (letzte Kerze): {s['last_volume']:,}")
    lines.append("")
    lines.append("## Signale (Daumenregeln)")
    lines.append("")
    for k, sig in sigs.items():
        lines.append(f"- **{k.upper()}**: {sig.get('label', 'n/a')} (tone={sig.get('tone', 'n/a')})")
    lines.append("")
    lines.append("## Indikatoren")
    lines.append("")
    lines.append("| Metrik | Wert |")
    lines.append("|---|---|")
    for label, val in [
        ("RSI(14)", s.get("rsi14")),
        ("MACD", s.get("macd")),
        ("MACD Signal", s.get("macd_signal")),
        ("MACD Hist", s.get("macd_hist")),
        ("MA20", s.get("ma20")),
        ("MA50", s.get("ma50")),
        ("MA200", s.get("ma200")),
        ("% vs MA20", s.get("pct_vs_ma20")),
        ("% vs MA50", s.get("pct_vs_ma50")),
        ("% vs MA200", s.get("pct_vs_ma200")),
        ("52W High", s.get("high_52w")),
        ("52W Low", s.get("low_52w")),
        ("52W Position (%)", s.get("pos_52w_pct")),
        ("Bollinger %b", s.get("boll_pct_b")),
    ]:
        if val is None:
            continue
        if isinstance(val, float) and abs(val) < 1e6:
            lines.append(f"| {label} | {val:.2f} |")
        else:
            lines.append(f"| {label} | {val} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# TradingView-Symbol-Resolution
# ---------------------------------------------------------------------------
# yfinance info['exchange'] -> TradingView EXCHANGE:TICKER prefix
TV_EXCHANGE_MAP = {
    # US
    "NMS": "NASDAQ",  # Nasdaq Global Select
    "NGM": "NASDAQ",  # Nasdaq Global Market
    "NCM": "NASDAQ",  # Nasdaq Capital Market
    "NYQ": "NYSE",    # NYSE
    "PCX": "AMEX",    # NYSE Arca / AMEX
    "ASE": "AMEX",
    "OB": "OTC",      # OTC Bulletin Board
    "PNK": "OTC",     # Pink Sheets
    "OQX": "OTC",     # CBOE-listed (OTC equivalent)
    # EU
    "GER": "XETRA",   # XETRA (regular)
    "FRA": "XETRA",   # Frankfurt (often dual-listed)
    "ETR": "XETRA",   # XETRA alt
    "STU": "XETRA",   # Stuttgart
    "MUN": "XETRA",   # Munich
    "BER": "XETRA",   # Berlin
    "HAM": "XETRA",   # Hamburg
    "DUS": "XETRA",   # Duesseldorf
    "MIL": "MIL",     # Milan
    "MIB": "MIL",
    "EPA": "EPA",     # Paris
    "PAR": "EPA",
    "AMS": "AMS",     # Amsterdam
    "BRU": "EBR",     # Brussels
    "LSE": "LSE",     # London
    "SIX": "SIX",     # Swiss
    "SWX": "SIX",
    "VIE": "VIE",     # Vienna
    "OSL": "OSE",     # Oslo
    "STO": "STO",     # Stockholm
    "CPH": "CPH",     # Copenhagen
    "HEL": "HEX",     # Helsinki
    "LIS": "LIS",     # Lisbon
    "BME": "BME",     # Madrid
    "MCE": "BME",     # Mercado Continuo Espanol (Madrid) -- yfinance liefert "MCE"
    "ATH": "ATSE",    # Athens
    "WSE": "WSE",     # Warsaw
    "PRA": "PRA",     # Prague
    "BUD": "BUX",     # Budapest
    # Asia
    "ASX": "ASX",
    "HKG": "HKEX",
    "JPX": "TSE",
    "TSE": "TSE",
    "KSC": "KSE",
    "KSE": "KSE",
    "KOSDAQ": "KOSDAQ",
    "SGX": "SGX",
    "SET": "SET",
    "TAI": "TWSE",
    "NSE": "NSE",
    "BSE": "BSE",
    # Canada
    "TOR": "TSX",
    "CVE": "TSXV",
    "VAN": "TSXV",    # TSX Venture -- yfinance liefert "VAN"
    "CNQ": "CSE",
}


def _get_tv_symbol(ticker: str) -> dict:
    """Map wiki-ticker zu TradingView EXCHANGE:SYMBOL.

    Returns: {symbol: 'NASDAQ:NVDA' | None, exchange: str | None, mode: str}
        mode in {"yfinance-resolved", "yfinance-fallback-suffix", "yfinance-failed", "all-failed"}
    """
    # 1) suffix-fallback (bare-ticker oft ohne exchange-info bei yfinance)
    effective_ticker, mode = _resolve_ticker(ticker)
    if mode == "all-failed":
        return {"symbol": None, "exchange": None, "mode": "all-failed"}
    try:
        info = yf.Ticker(effective_ticker).info or {}
    except Exception:
        return {"symbol": None, "exchange": None, "mode": "yfinance-failed"}
    exch_code = (info.get("exchange") or "").upper()
    tv_exch = TV_EXCHANGE_MAP.get(exch_code, "NASDAQ")
    # TradingView-Format: EXCHANGE:SYMBOL, wobei der Suffix im Symbol NICHT
    # mitgegeben wird (der ist durch den Exchange bereits impliziert).
    # Beispiel: yfinance "DR0.DE" mit Exchange "GER" -> TV "XETRA:DR0",
    # NICHT "XETRA:DR0.DE". Sonst rendert das Widget nichts.
    base_symbol, _ = _split_ticker(effective_ticker)
    final_mode = "yfinance-resolved"
    if mode == "suffix-fallback":
        final_mode = "yfinance-fallback-suffix"
    return {
        "symbol": f"{tv_exch}:{base_symbol}",
        "exchange": tv_exch,
        "mode": final_mode,
    }
