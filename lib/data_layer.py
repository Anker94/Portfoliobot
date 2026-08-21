"""Daten-Layer: yfinance + Indikatoren + Robustheit gegen API-Ausfaelle.

Kernregel (SKILL.md): Code zieht Kurse, LLM NIEMALS. Alle Funktionen hier
fangen API-Fehler ab und liefern (None, Grund) statt zu crashen -- wichtig,
weil yfinance auf manchen IPs (wie dieser) haeufig 429 liefert.

Pitfalls aus SKILL.md sind als Kommentare + Guards eingebaut.
"""
import yfinance as yf
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import AverageTrueRange

# Indizes duerfen NICHT ueber get_stock_data() laufen (Indicator-Crash)
INDEX_TICKERS = {"^GSPC", "^IXIC", "^VIX", "^TNX", "^IRX", "^RUT"}


def safe_history(ticker, period="5d", interval="1d"):
    """yfinance history mit Fehlerabfang. Liefert DataFrame oder None."""
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval,
                                        auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        return None


def get_stock_data(ticker, period="1y"):
    """Vollstaendige Aktien-Analyse: RSI/SMA/MACD/ATR + 52w-Range.
    Wirft bei Indizes ValueError (siehe Pitfall: get_market_sentiment_data)."""
    if ticker in INDEX_TICKERS:
        raise ValueError(f"{ticker} ist ein Index -- verwende get_index_data()")
    df = safe_history(ticker, period=period)
    if df is None or len(df) < 30:
        return None
    close = df["Close"]
    out = {
        "ticker": ticker,
        "spot": float(close.iloc[-1]),
        "rsi_14": _rsi(close),
        "sma_50": float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None,
        "sma_200": float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None,
        "macd": _macd(close),
        "atr": _atr(df),
        "low_52w": float(close.min()),
        "high_52w": float(close.max()),
        "perf_1m": _perf(close, 21),
        "perf_3m": _perf(close, 63),
        "perf_6m": _perf(close, 126),
    }
    return out


def get_index_data(ticker, period="1mo"):
    """Nur Close extrahieren, KEINE Indikatoren (Pitfall)."""
    df = safe_history(ticker, period=period)
    if df is None or df.empty:
        return None
    return {"ticker": ticker, "spot": float(df["Close"].iloc[-1]),
            "close": df["Close"].tolist()}


def get_performance(ticker):
    """Aktueller Close fuer Performance/Exit-Check via kurzes Period (Pitfall: nicht get_stock_data)."""
    df = safe_history(ticker, period="5d")
    if df is None or df.empty:
        return None
    return float(df["Close"].iloc[-1])


def _rsi(close):
    try:
        return float(RSIIndicator(close, window=14).rsi().iloc[-1])
    except Exception:
        return None


def _macd(close):
    try:
        m = MACD(close)
        return {
            "macd": float(m.macd().iloc[-1]),
            "signal": float(m.macd_signal().iloc[-1]),
            "hist": float(m.macd_diff().iloc[-1]),
        }
    except Exception:
        return None


def _atr(df):
    try:
        return float(AverageTrueRange(df["High"], df["Low"], df["Close"],
                                      window=14).average_true_range().iloc[-1])
    except Exception:
        return None


def _perf(close, days):
    if len(close) <= days:
        return None
    return float((close.iloc[-1] / close.iloc[-days] - 1) * 100)


def get_info(ticker):
    """yfinance info (Name, shortPercentOfFloat, ...). Bricht bei Fehler nicht."""
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


def get_news_yfinance(ticker, limit=10):
    """yfinance .news (Zweitquelle fuer TSX-V Blindheit K2)."""
    try:
        news = yf.Ticker(ticker).news or []
        return [n.get("title", "") for n in news[:limit]]
    except Exception:
        return []


def download_closes(tickers, period="6mo"):
    """Mehrfach-Download der Close-Preise fuer Korrelation (E1).
    Liefert DataFrame (Datumsindex, Spalten=tickers) oder None.
    Pitfall: einzelner Ticker -> kein MultiIndex -> behandeln."""
    try:
        if len(tickers) == 1:
            df = yf.Ticker(tickers[0]).history(period=period, auto_adjust=True)
            if df is None or df.empty:
                return None
            return df[["Close"]].rename(columns={"Close": tickers[0]})
        data = yf.download(tickers, period=period, auto_adjust=True)["Close"]
        return data
    except Exception:
        return None


if __name__ == "__main__":
    print("get_stock_data NVDA:", get_stock_data("NVDA"))
