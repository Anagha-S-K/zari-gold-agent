"""Extra pay-day strategy experiments. Every variant is tuned (if it has a knob) on 2016-2022
and judged on unseen 2023-2026, against "pay on the 1st" (morning rate)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from zari.analysis import complete_windows, decide, score
from zari.config import settings
from zari.signal import THRESHOLDS

TRAIN_END = 2022


def _windows(days: pd.DataFrame, start: pd.Timestamp, deadline: int):
    for y, m, w in complete_windows(days["close"], deadline):
        if w.index[0] >= start:
            yield y, m, days.loc[w.index]


def strategies(days: pd.DataFrame, sig: pd.Series, mu: pd.Series, sim_paths: int = 0) -> list[dict]:
    """One record per finished window with the price each strategy would have paid.
    days: open/close per day; sig: overnight signal for day -> next (known that evening);
    mu: trend (60-day EWMA of daily log change) known at each day."""
    deadline = settings()["plan"]["deadline_day"]
    recs = []
    for y, m, w in _windows(days, max(sig.index[0], mu.index[0]), deadline):
        o, c = w["open"], w["close"]
        n = len(w)
        rec = {"year": y, "month": m, "day1": float(o.iloc[0]),
               "low": float(min(o.min(), c.min())), "prices": {}}
        P = rec["prices"]
        P["1st_morning"] = float(o.iloc[0])
        P["1st_evening"] = float(c.iloc[0])
        P["1st_cheaper_of_morning_evening(hindsight)"] = float(min(o.iloc[0], c.iloc[0]))
        # buy the first dip: first day whose morning rate is below the previous day's close
        prev_close = days["close"].shift(1).loc[w.index]
        dip = [i for i in range(n) if o.iloc[i] < prev_close.iloc[i]]
        P["first_dip"] = float(o.iloc[dip[0]] if dip else o.iloc[-1])
        # weekday effect: first window day that is a given weekday
        for wd, name in enumerate(["mon", "tue", "wed", "thu", "fri", "sat"]):
            hit = [i for i in range(n) if w.index[i].weekday() == wd]
            P[f"first_{name}"] = float(o.iloc[hit[0]] if hit else o.iloc[0])
        # signal rules (decided in the evening, pay at the day's close if not waiting)
        for t in THRESHOLDS:
            P[f"sig_{t}"] = _sig_rule(c, sig, t, None)
            P[f"sig_trenddown_{t}"] = _sig_rule(c, sig, t, mu)
        if sim_paths:
            P["sim+signal"] = _sim_signal(days["close"], c, sig, sim_paths)
        recs.append(rec)
    return recs


def _sig_rule(c: pd.Series, sig: pd.Series, t: float, mu: pd.Series | None) -> float:
    for i, d in enumerate(c.index):
        if i == len(c) - 1:
            return float(c.iloc[i])
        x = sig.get(d, np.nan)
        trend_down = mu is None or mu.get(d, 0.0) < 0
        if not (trend_down and not np.isnan(x) and x <= -t):
            return float(c.iloc[i])
    return float(c.iloc[-1])


def _sim_signal(close: pd.Series, c: pd.Series, sig: pd.Series, paths: int) -> float:
    margin = 0.003
    for i, d in enumerate(c.index):
        n_left = len(c) - 1 - i
        if n_left == 0:
            return float(c.iloc[i])
        x = sig.get(d, np.nan)
        res = decide(close, d.date(), margin, n_left=n_left, paths=paths,
                     first_step=None if np.isnan(x) else float(x))
        if res["action"] == "PAY":
            return float(c.iloc[i])
    return float(c.iloc[-1])


def evaluate(recs: list[dict]) -> dict:
    amount = settings()["plan"]["monthly_amount"]
    train = [r for r in recs if r["year"] <= TRAIN_END]
    test = [r for r in recs if r["year"] > TRAIN_END]
    names = list(recs[0]["prices"])
    out = {}
    for name in names:
        out[name] = {"train": score(train, lambda r: r["prices"][name], amount),
                     "test": score(test, lambda r: r["prices"][name], amount)}
    # knobs: pick threshold on train only
    for fam in ("sig_", "sig_trenddown_"):
        cands = [n for n in names if n.startswith(fam) and n[len(fam):].replace(".", "").isdigit()]
        best = max(cands, key=lambda n: out[n]["train"]["avg_extra_grams_per_month"])
        out[f"{fam}TUNED({best[len(fam):]})"] = out[best]
    return out
