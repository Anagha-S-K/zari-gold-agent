"""International gold (USD/oz) and USD/INR from Yahoo Finance's free chart endpoint.

GRT/MJDTA set each morning's rate (~9:45 AM IST) from these, so their move since the last
rate was set estimates the NEXT rate before it is announced."""
from __future__ import annotations

import datetime as dt
import json
import math

import pandas as pd

from zari.config import DATA, IST
from zari.web import get

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
GOLD, USDINR = "GC=F", "INR=X"
MARKET = DATA / "market.csv"


def parse_chart(text: str) -> pd.Series:
    """Yahoo chart JSON -> close prices indexed by UTC timestamp."""
    res = json.loads(text)["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    s = pd.Series(closes, index=pd.to_datetime(res["timestamp"], unit="s", utc=True), dtype=float)
    return s.dropna()


def chart(symbol: str, range_: str = "10y", interval: str = "1d") -> pd.Series:
    return parse_chart(get(CHART.format(symbol=symbol), params={"range": range_, "interval": interval}))


def gold_inr_series(range_: str = "10y", interval: str = "1d") -> pd.DataFrame:
    """Gold USD/oz, USD/INR and their product (INR/oz), aligned on time."""
    g, fx = chart(GOLD, range_, interval), chart(USDINR, range_, interval)
    if interval == "1d":                       # daily bars: align on calendar date
        g.index, fx.index = g.index.date, fx.index.date
        df = pd.concat({"gold_usd": g, "usdinr": fx}, axis=1).sort_index().ffill().dropna()
        df.index = pd.to_datetime(df.index)
    else:                                      # intraday: align on time, carry last value
        df = pd.concat({"gold_usd": g, "usdinr": fx}, axis=1).sort_index().ffill().dropna()
    df["gold_inr"] = df["gold_usd"] * df["usdinr"]
    return df


def live_gold_inr() -> tuple[float, dt.datetime]:
    """Latest gold x USD/INR (5-minute data) and its time in IST."""
    df = gold_inr_series("1d", "5m")
    return float(df["gold_inr"].iloc[-1]), df.index[-1].tz_convert(IST).to_pydatetime()


def gold_inr_at(when_ist: dt.datetime, df: pd.DataFrame | None = None) -> float:
    """Gold x USD/INR at (or just before) a given IST moment, from recent intraday data."""
    df = gold_inr_series("5d", "5m") if df is None else df
    t = pd.Timestamp(when_ist).tz_convert("UTC")
    before = df[df.index <= t]
    if before.empty:
        raise ValueError(f"no market data before {when_ist}")
    return float(before["gold_inr"].iloc[-1])


def estimate_next(rate_today: float, gold_inr_at_set: float, gold_inr_now: float) -> tuple[float, float]:
    """Tomorrow's rate if it moves with gold x INR since today's rate was set.
    Returns (estimated rate, log change)."""
    x = math.log(gold_inr_now / gold_inr_at_set)
    return rate_today * math.exp(x), x


def save_daily(df: pd.DataFrame) -> None:
    DATA.mkdir(exist_ok=True)
    df.to_csv(MARKET, index_label="date")
