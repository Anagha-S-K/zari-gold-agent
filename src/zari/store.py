"""CSV storage in data/: rates.csv (every reading) and payments.csv (your instalments)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from zari.config import DATA, settings
from zari.models import RateRecord

RATES = DATA / "rates.csv"
PAYMENTS = DATA / "payments.csv"
RATE_COLS = ["date", "time", "source", "rate_22k", "note"]
PAY_COLS = ["n", "date", "amount", "rate", "grams", "note"]



def load_rates(path: Path = RATES) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=RATE_COLS)
    df = pd.read_csv(path, dtype={"time": str, "note": str}, keep_default_na=False)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def save_rates(records: list[RateRecord], path: Path = RATES) -> int:
    """Upsert readings keyed by (date, time, source). Returns rows added."""
    path.parent.mkdir(parents=True, exist_ok=True)
    old = load_rates(path)
    new = pd.DataFrame([{"date": r.date, "time": r.time, "source": r.source,
                         "rate_22k": r.rate_22k, "note": r.note} for r in records], columns=RATE_COLS)
    both = pd.concat([old, new], ignore_index=True)
    both = both.drop_duplicates(subset=["date", "time", "source"], keep="last")
    both = both.sort_values(["date", "source", "time"]).reset_index(drop=True)
    both.to_csv(path, index=False)
    return len(both) - len(old)


def daily_close(df: pd.DataFrame | None = None) -> pd.Series:
    """One 22K rate per day: MJDTA's last reading that day (board rate GRT follows).
    Days with only a GRT reading fall back to GRT."""
    df = load_rates() if df is None else df
    if df.empty:
        return pd.Series(dtype=float)
    c = settings()["checks"]
    df = df[(df.rate_22k >= c["min_rate"]) & (df.rate_22k <= c["max_rate"])]   # drops zeros / typos
    df = df.sort_values(["date", "time"])
    mj = df[df.source == "mjdta"].groupby("date")["rate_22k"].last()
    gr = df[df.source == "grt"].groupby("date")["rate_22k"].last()
    s = mj.combine_first(gr).sort_index()
    s.index = pd.to_datetime(s.index)
    return drop_spikes(s.astype(float))


def drop_spikes(s: pd.Series, limit: float = 0.06) -> pd.Series:
    """Remove one-day glitches: a day more than `limit` away from BOTH neighbours in the
    same direction (e.g. 11 Mar 2016: 2797 -> 2515 -> 2702). Real moves that stick stay."""
    if len(s) < 3:
        return s
    prev, nxt = s.shift(1), s.shift(-1)
    up = (s > prev * (1 + limit)) & (s > nxt * (1 + limit))
    down = (s < prev * (1 - limit)) & (s < nxt * (1 - limit))
    return s[~(up | down)]


def load_payments(path: Path = PAYMENTS) -> pd.DataFrame:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=PAY_COLS).to_csv(path, index=False)
    return pd.read_csv(path, keep_default_na=False)


def add_payment(date: dt.date, amount: float, rate: float, note: str = "", path: Path = PAYMENTS) -> dict:
    df = load_payments(path)
    row = {"n": int(df["n"].max()) + 1 if len(df) else 1, "date": date.isoformat(), "amount": amount,
           "rate": rate, "grams": round(amount / rate, 4), "note": note}
    pd.concat([df, pd.DataFrame([row])], ignore_index=True).to_csv(path, index=False)
    return row


def totals(df: pd.DataFrame | None = None) -> dict:
    df = load_payments() if df is None else df
    plan = settings()["plan"]
    paid = float(df["amount"].sum())
    grams = float(df["grams"].sum())
    return {"instalments": len(df), "of": plan["instalments"], "paid": paid, "grams": round(grams, 4),
            "avg_rate": round(paid / grams, 2) if grams else None}
