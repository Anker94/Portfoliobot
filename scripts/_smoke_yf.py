"""Smoke-Check: yfinance + data_layer im Portfoliobot-Kontext."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from data_layer import get_performance  # noqa: E402
for ticker in ["NVDA", "TSM", "ORCL", "PSIX", "RZLV", "PDD", "1810.HK", "CPNG", "VUL.AX", "TCEHY"]:
    print(ticker, get_performance(ticker))
