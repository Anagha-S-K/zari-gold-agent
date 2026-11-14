"""Zari's quant model: trend + resampled noise, simulated futures, optimal-stopping PAY/WAIT,
and a backtest against simple strategies.

Model of a daily log change r_t:
    r_t = mu + phi * (r_{t-1} - mu) + e_t
  mu   = 60-day EWMA of recent changes (trend)
  phi  = measured short-term bounce-back / follow-through (AR(1)), clipped to +-0.5
  e_t  = resampled from ~3 years of real residuals, rescaled to current volatility

Decision on a window day with n trading days left before the deadline:
  simulate futures, work backwards (Longstaff-Schwartz) to get the expected rate if you wait
  and keep following the same rule; PAY unless waiting is expected to save more than `margin`.
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from zari.config import settings

MIN_POOL = 250


@dataclass
class Params:
    mu: float
    phi: float
    vol: float
    eps: np.ndarray       # residual pool, unit variance (rescaled to vol at use)


def log_returns(s: pd.Series) -> pd.Series:
    return np.log(s).diff().dropna()


def fit_params(r: pd.Series, cfg: dict | None = None) -> Params:
    """Fit the model on returns r (already cut to the data known so far)."""
    cfg = cfg or settings()["model"]
    pool = r.iloc[-cfg["pool_days"]:].to_numpy()
    if len(pool) < MIN_POOL:
        raise ValueError(f"need {MIN_POOL}+ days of history, have {len(pool)}")
    mu = float(r.ewm(span=cfg["drift_span"]).mean().iloc[-1])
    vol = float(np.sqrt((r ** 2).ewm(span=cfg["vol_span"]).mean().iloc[-1]))
    x, y = pool[:-1] - pool.mean(), pool[1:] - pool.mean()
    phi = float(np.clip((x @ y) / (x @ x), -0.5, 0.5)) if x @ x > 0 else 0.0
    eps = y - phi * x
    eps = (eps - eps.mean()) / (eps.std() or 1.0)
    return Params(mu, phi, max(vol, 1e-5), eps)


def simulate(p: Params, n: int, r_last: float, paths: int, rng: np.random.Generator,
             first_step: float | None = None) -> np.ndarray:
    """Simulated log changes, shape (paths, n). `first_step` pins tomorrow's change
    (e.g. from the overnight market signal), with a little noise."""
    rets = np.empty((paths, n))
    prev = np.full(paths, r_last)
    shocks = rng.choice(p.eps, size=(paths, n)) * p.vol
    for j in range(n):
        rets[:, j] = p.mu + p.phi * (prev - p.mu) + shocks[:, j]
        if j == 0 and first_step is not None:
            rets[:, 0] = first_step + shocks[:, 0] * 0.25
        prev = rets[:, j]
    return rets


def wait_value(rets: np.ndarray, margin: float) -> tuple[float, float]:
    """Expected rate (relative to today = 1.0) if you wait and follow the rule,
    and the probability that a cheaper day exists before the deadline."""
    paths, n = rets.shape
    if n == 0:
        return 1.0, 0.0
    price = np.exp(np.cumsum(rets, axis=1))          # relative to today
    cost = price[:, -1].copy()                          # last day: must pay
    for j in range(n - 2, -1, -1):                      # backward induction
        state = rets[:, j]
        X = np.column_stack([np.ones(paths), state, state ** 2])
        beta, *_ = np.linalg.lstsq(X, cost / price[:, j], rcond=None)
        pay = X @ beta >= 1.0 - margin                  # expected future cost / today's price
        cost = np.where(pay, price[:, j], cost)
    p_cheaper = float((price.min(axis=1) < 1.0).mean())
    return float(cost.mean()), p_cheaper


def remaining_window_days(day: dt.date, deadline_day: int) -> int:
    """Non-Sunday days after `day` up to the deadline (MJDTA posts no Sunday rate)."""
    if day.day >= deadline_day:
        return 0
    return sum(1 for d in range(day.day + 1, deadline_day + 1)
               if dt.date(day.year, day.month, d).weekday() != 6)


def decide(s: pd.Series, day: dt.date, margin: float, n_left: int | None = None,
           paths: int | None = None, seed: int = 7, first_step: float | None = None) -> dict:
    """PAY / WAIT for `day`, using only data up to and including `day`."""
    cfg = settings()
    deadline = cfg["plan"]["deadline_day"]
    known = s[s.index <= pd.Timestamp(day)]
    if known.empty or known.index[-1].date() != day:
        raise ValueError(f"no rate stored for {day}")
    rate = float(known.iloc[-1])
    month = known[(known.index.year == day.year) & (known.index.month == day.month)]
    window = month[month.index.day <= deadline]
    out = {"date": day.isoformat(), "rate": rate,
           "window_low_so_far": float(window.min()) if len(window) else None}
    if day.day > deadline:
        out.update(action="OUTSIDE_WINDOW", reason=f"payment window is the 1st-{deadline}th")
        return out
    n = remaining_window_days(day, deadline) if n_left is None else n_left
    if n == 0:
        out.update(action="PAY", reason="deadline day - pay today", expected_wait_rate=rate, p_cheaper=0.0)
        return out
    r = log_returns(known)
    p = fit_params(r)
    rets = simulate(p, n, float(r.iloc[-1]), paths or cfg["model"]["paths"],
                    np.random.default_rng(seed), first_step)
    ev, p_cheaper = wait_value(rets, margin)
    action = "PAY" if ev >= 1.0 - margin else "WAIT"
    out.update(action=action, expected_wait_rate=round(rate * ev, 2), p_cheaper=round(p_cheaper, 3),
               days_left=n, trend_pct_per_day=round(p.mu * 100, 3), vol_pct_per_day=round(p.vol * 100, 2),
               bounce=round(p.phi, 3),
               reason=("waiting is not expected to save enough" if action == "PAY"
                       else f"waiting is expected to save {100 * (1 - ev):.2f}%"))
    return out


# ---------------------------------------------------------------- backtest

MARGINS = [0.0, 0.001, 0.002, 0.003, 0.005, 0.0075, 0.01]


def complete_windows(s: pd.Series, deadline: int):
    """Yield (year, month, window series) for finished payment windows."""
    last = s.index[-1].date()
    for (y, m), g in s.groupby([s.index.year, s.index.month]):
        w = g[g.index.day <= deadline]
        end = dt.date(y, m, min(deadline, calendar.monthrange(y, m)[1]))
        if len(w) >= 3 and last >= end:
            yield int(y), int(m), w


def fixed_day_price(rec: dict, d: int) -> float:
    days = sorted(rec["by_day"])
    pick = next((k for k in days if k >= d), days[-1])
    return rec["by_day"][pick]


def score(recs: list[dict], price_fn, amount: float) -> dict:
    if not recs:
        return {}
    p = np.array([price_fn(r) for r in recs])
    d1 = np.array([r["day1"] for r in recs])
    low = np.array([r["low"] for r in recs])
    g = amount / p - amount / d1
    return {"months": len(recs),
            "avg_saving_pct_vs_day1": round(float(((d1 - p) / d1).mean() * 100), 3),
            "avg_extra_grams_per_month": round(float(g.mean()), 4),
            "extra_grams_over_11_months": round(float(g.mean() * 11), 4),
            "beat_day1_pct": round(float((p < d1).mean() * 100), 1),
            "worse_than_day1_pct": round(float((p > d1).mean() * 100), 1),
            "hit_cheapest_pct": round(float((p <= low).mean() * 100), 1),
            "within_0_5pct_of_cheapest_pct": round(float((p <= low * 1.005).mean() * 100), 1),
            "avg_gap_to_cheapest_pct": round(float(((p - low) / low).mean() * 100), 3)}


def backtest(s: pd.Series, paths: int = 1500, margins=MARGINS, train_end_year: int = 2022,
             log=print) -> dict:
    """Replay every finished window using only data known at the time."""
    cfg = settings()
    deadline = cfg["plan"]["deadline_day"]
    amount = cfg["plan"]["monthly_amount"]
    r_all = log_returns(s)
    rows = []
    for y, m, w in complete_windows(s, deadline):
        if s.index.get_loc(w.index[0]) < MIN_POOL + 1:
            continue
        rec = {"year": y, "month": m, "day1": float(w.iloc[0]), "low": float(w.min()),
               "high": float(w.max()), "by_day": {int(d.day): float(v) for d, v in w.items()}}
        paid: dict[float, float | None] = {mg: None for mg in margins}
        for i, (d, price) in enumerate(w.items()):
            n_left = len(w) - 1 - i
            if n_left == 0:                                   # deadline: pay
                for mg in margins:
                    paid[mg] = paid[mg] or float(price)
                break
            r = r_all[r_all.index <= d]
            rets = simulate(fit_params(r), n_left, float(r.iloc[-1]), paths,
                            np.random.default_rng(y * 10000 + m * 100 + i))
            for mg in margins:
                if paid[mg] is None:
                    ev, _ = wait_value(rets, mg)
                    if ev >= 1.0 - mg:
                        paid[mg] = float(price)
            if all(v is not None for v in paid.values()):
                break
        rec["rule"] = {str(mg): v for mg, v in paid.items()}
        rows.append(rec)
        log(f"{y}-{m:02d}: 1st {rec['day1']:.0f}  low {rec['low']:.0f}  high {rec['high']:.0f}")

    train = [r for r in rows if r["year"] <= train_end_year]
    test = [r for r in rows if r["year"] > train_end_year]
    sc = lambda recs, fn: score(recs, fn, amount)  # noqa: E731
    best_margin = max(margins, key=lambda mg: sc(train, lambda r: r["rule"][str(mg)])
                      .get("avg_extra_grams_per_month", -1e9))
    best_day = max(range(1, deadline + 1),
                   key=lambda d: sc(train, lambda r: fixed_day_price(r, d)).get("avg_extra_grams_per_month", -1e9))
    strategies = {
        "pay_on_1st": lambda r: r["day1"],
        f"fixed_day_{best_day}": lambda r: fixed_day_price(r, best_day),
        "zari_rule": lambda r: r["rule"][str(best_margin)],
        "perfect_hindsight": lambda r: r["low"],
    }
    result = {
        "generated": dt.date.today().isoformat(),
        "amount": amount, "deadline_day": deadline, "paths": paths,
        "train_years": f"{train[0]['year']}-{train[-1]['year']}" if train else "-",
        "test_years": f"{test[0]['year']}-{test[-1]['year']}" if test else "-",
        "tuned_margin": best_margin, "tuned_fixed_day": best_day,
        "train": {k: sc(train, f) for k, f in strategies.items()},
        "test": {k: sc(test, f) for k, f in strategies.items()},
        "test_by_margin": {str(mg): sc(test, lambda r, mg=mg: r["rule"][str(mg)]) for mg in margins},
        "months": rows,
    }
    rule = result["test"]["zari_rule"].get("avg_extra_grams_per_month", 0.0)
    fixed = result["test"][f"fixed_day_{best_day}"].get("avg_extra_grams_per_month", 0.0)
    if rule > 0 and rule >= fixed:
        result["verdict"] = "Zari's rule beat paying on the 1st and the best fixed day in the unseen test years."
    elif fixed > 0 and fixed > rule:
        result["verdict"] = f"Paying on a fixed day ({best_day}) beat Zari's rule in the test years - simpler wins."
    else:
        result["verdict"] = "Nothing beat simply paying on the 1st in the test years."
    return result
