"""Does the overnight move in gold x USD/INR predict the next GRT/MJDTA rate? And does a
pay rule built on it beat paying on the 1st?

Two versions of the market reading used for "tomorrow's estimate":
  * hourly (last ~2 years): gold x INR at 9 PM IST and 8 AM IST vs. when today's rate was set -
    exactly what Zari will see live.
  * daily (since 2016): last US daily close before each date - a longer but rougher proxy.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from zari.analysis import complete_windows, fixed_day_price, score
from zari.config import IST, settings
from zari.store import daily_close, load_rates


def rate_days(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per MJDTA day: first (morning) rate + time, last rate + time."""
    from zari.store import drop_spikes
    df = load_rates() if df is None else df
    c = settings()["checks"]
    m = df[(df.source == "mjdta") & df.rate_22k.between(c["min_rate"], c["max_rate"])].copy()
    m = m[m.time.str.len() > 0].sort_values(["date", "time"])
    g = m.groupby("date")
    out = pd.DataFrame({"open": g.rate_22k.first(), "open_time": g.time.first(),
                        "close": g.rate_22k.last(), "close_time": g.time.last()})
    out.index = pd.to_datetime(out.index)
    keep = drop_spikes(out["close"]).index
    return out.loc[keep]


def _at(series: pd.Series, when: pd.Timestamp) -> float | None:
    before = series[series.index <= when]
    return float(before.iloc[-1]) if len(before) else None


def _ist(day: pd.Timestamp, hhmm: str) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(dt.datetime(day.year, day.month, day.day, h, m), tz=IST).tz_convert("UTC")


def pairs_hourly(days: pd.DataFrame, gi: pd.Series) -> pd.DataFrame:
    """For each consecutive pair of rate days (D -> next): the signal at 9 PM on D and at 8 AM on
    next, both relative to gold x INR when D's last rate was set; and the actual change."""
    rows = []
    idx = days.index
    for d, nxt in zip(idx[:-1], idx[1:]):
        if gi.index[0] > _ist(d, "00:00") or gi.index[-1] < _ist(nxt, "08:00"):
            continue
        base = _at(gi, _ist(d, days.at[d, "close_time"]))
        pm9 = _at(gi, _ist(d, "21:00"))
        am8 = _at(gi, _ist(nxt, "08:00"))
        if None in (base, pm9, am8):
            continue
        rows.append({"date": d, "next": nxt, "x_9pm": np.log(pm9 / base), "x_8am": np.log(am8 / base),
                     "y": np.log(days.at[nxt, "open"] / days.at[d, "close"])})
    return pd.DataFrame(rows).set_index("date")


def pairs_daily(days: pd.DataFrame, gi_daily: pd.Series) -> pd.DataFrame:
    """Same idea with daily US closes: last close before next day vs last close before D."""
    rows = []
    idx = days.index
    for d, nxt in zip(idx[:-1], idx[1:]):
        a = gi_daily[gi_daily.index < d]
        b = gi_daily[gi_daily.index < nxt]
        if len(a) == 0:
            continue
        rows.append({"date": d, "next": nxt, "x_close": np.log(b.iloc[-1] / a.iloc[-1]),
                     "y": np.log(days.at[nxt, "open"] / days.at[d, "close"])})
    return pd.DataFrame(rows).set_index("date")


def accuracy(p: pd.DataFrame, col: str) -> dict:
    x, y = p[col], p["y"]
    moved = y.abs() > 1e-9
    beta = float((x @ y) / (x @ x)) if (x @ x) > 0 else 0.0
    err = (y - x).abs()
    return {"days": int(len(p)),
            "direction_right_pct": round(float((np.sign(x[moved]) == np.sign(y[moved])).mean() * 100), 1),
            "correlation": round(float(np.corrcoef(x, y)[0, 1]), 3),
            "pass_through": round(beta, 3),
            "median_error_pct": round(float(err.median() * 100), 3),
            "median_error_if_no_change_pct": round(float(y.abs().median() * 100), 3),
            "big_moves_caught_pct": round(float(((np.sign(x) == np.sign(y)) & (y.abs() > 0.01)).sum()
                                                 / max(1, (y.abs() > 0.01).sum()) * 100), 1)}


# ------------------------------------------------------------ pay rule with the signal

THRESHOLDS = [0.0, 0.001, 0.002, 0.003, 0.005, 0.0075, 0.01, 0.015]


def signal_rule_price(w: pd.Series, sig: pd.Series, threshold: float) -> float:
    """Each window evening: if tomorrow's estimate is at least `threshold` lower, wait;
    otherwise pay today's (last) rate. Pay on the last window day regardless."""
    days = list(w.index)
    for i, d in enumerate(days):
        if i == len(days) - 1:
            return float(w.iloc[i])
        x = sig.get(d)
        if x is None or np.isnan(x) or x > -threshold:
            return float(w.iloc[i])
    return float(w.iloc[-1])


def backtest_signal(days: pd.DataFrame, sig: pd.Series, train_end_year: int = 2022,
                    thresholds=THRESHOLDS) -> dict:
    cfg = settings()["plan"]
    deadline, amount = cfg["deadline_day"], cfg["monthly_amount"]
    close = days["close"]
    recs = []
    for y, m, w in complete_windows(close, deadline):
        if w.index[0] < sig.index[0]:
            continue
        rec = {"year": y, "month": m, "day1": float(w.iloc[0]), "low": float(w.min()),
               "by_day": {int(d.day): float(v) for d, v in w.items()},
               "sig": {str(t): signal_rule_price(w, sig, t) for t in thresholds}}
        recs.append(rec)
    train = [r for r in recs if r["year"] <= train_end_year]
    test = [r for r in recs if r["year"] > train_end_year]
    tune_on = train or recs
    best = max(thresholds, key=lambda t: score(tune_on, lambda r: r["sig"][str(t)], amount)
               .get("avg_extra_grams_per_month", -1e9))
    strategies = {"pay_on_1st": lambda r: r["day1"],
                  "zari_signal_rule": lambda r: r["sig"][str(best)],
                  "perfect_hindsight": lambda r: r["low"]}
    return {"tuned_threshold": best,
            "train_years": f"{train[0]['year']}-{train[-1]['year']}" if train else "-",
            "test_years": f"{test[0]['year']}-{test[-1]['year']}" if test else "-",
            "train": {k: score(train, f, amount) for k, f in strategies.items()},
            "test": {k: score(test, f, amount) for k, f in strategies.items()},
            "by_threshold_all": {str(t): score(recs, lambda r, t=t: r["sig"][str(t)], amount) for t in thresholds},
            "months": len(recs)}
