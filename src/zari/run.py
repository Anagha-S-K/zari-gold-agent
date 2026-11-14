"""Scheduled runs. Policy (decided 6 Nov 2026 from backtests): pay on the first window day
after the morning rate; everything else is a heads-up.

slots:
  morning   ~10:15 IST  fetch + save rates, pay/deadline alerts, big drop, recap, digest, milestones
  afternoon ~16:30 IST  window days only: alert if the rate was revised DOWN since morning
  signal    ~08:00 / ~21:00 IST  estimate the next rate from gold x USD/INR"""
from __future__ import annotations

import csv
import datetime as dt

import numpy as np
import pandas as pd

from zari import notify, state
from zari.config import DATA, IST, now_ist, settings
from zari.notify import Alert

SIGNAL_LOG = DATA / "signal_log.csv"
BIG_MOVE = 0.015          # 1.5% daily fall -> heads-up
SIGNAL_ALERT = 0.01       # overnight estimate move worth a heads-up


# ------------------------------------------------------------------ helpers

def plan() -> dict:
    return settings()["plan"]


def paid_in_month(payments: pd.DataFrame, day: dt.date) -> pd.Series | None:
    m = payments[payments["date"].astype(str).str.startswith(day.strftime("%Y-%m"))]
    return m.iloc[-1] if len(m) else None


def window_series(close: pd.Series, day: dt.date) -> pd.Series:
    m = close[(close.index.year == day.year) & (close.index.month == day.month)]
    return m[m.index.day <= plan()["deadline_day"]]


def grams(rate: float) -> float:
    return plan()["monthly_amount"] / rate


def pct(a: float, b: float) -> float:
    return (a / b - 1) * 100


def milestones(day: dt.date) -> list[str]:
    out = []
    for m in settings().get("milestones") or []:
        if dt.date.fromisoformat(str(m["date"])) == day:
            out.append(m["text"])
    return out


# ------------------------------------------------------------------ alert builders (pure)

def pay_alert(day: dt.date, rate: float, prev: float | None, extra: list[str]) -> Alert:
    deadline = plan()["deadline_day"]
    left = deadline - day.day
    change = f" ({pct(rate, prev):+.1f}% vs last)" if prev else ""
    head = f"₹{rate:,.0f}/g today{change} → {grams(rate):.4f} g for ₹{plan()['monthly_amount']:,}"
    if day.day >= deadline:
        title, prio, tags = "🚨 LAST DAY - pay today", 5, ["rotating_light"]
        why = f"Deadline is today (the {deadline}th). Missing it ends the no-wastage benefit."
    elif day.day >= 8:
        title, prio, tags = f"⚠️ PAY TODAY - {left} day{'s' if left != 1 else ''} to deadline", 5, ["warning"]
        why = "Not logged as paid yet. Please pay today to stay safe."
    else:
        title, prio, tags = "✅ PAY TODAY", 4, ["moneybag"]
        why = "Zari's policy: pay early - in 12 years of data, waiting cost more on average."
    return Alert(title, "\n".join([head, why, *extra]), prio, tags, pay_buttons=True)


def revised_down_alert(rate: float, morning: float) -> Alert:
    return Alert("rate revised DOWN", f"GRT now ₹{rate:,.0f}/g (morning ₹{morning:,.0f}, {pct(rate, morning):+.1f}%) "
                 f"→ {grams(rate):.4f} g. If you haven't paid, now is cheaper.", 4, ["chart_with_downwards_trend"],
                 pay_buttons=True)


def big_drop_alert(rate: float, prev: float, low30: bool) -> Alert:
    bits = [f"₹{rate:,.0f}/g ({pct(rate, prev):+.1f}% vs last)"]
    if low30:
        bits.append("Lowest in 30 days.")
    bits.append("Info only - your next payment window opens on the 1st.")
    return Alert("📉 gold dropped", " ".join(bits), 3, ["chart_with_downwards_trend"])


def recap_alert(day: dt.date, pay_row, win: pd.Series, totals: dict, extra: list[str]) -> Alert:
    paid_rate = float(pay_row["rate"])
    lo, hi = float(win.min()), float(win.max())
    g_paid, g_best = grams(paid_rate), grams(lo)
    body = [f"Paid ₹{paid_rate:,.0f}/g on {pay_row['date']} → {float(pay_row['grams']):.4f} g",
            f"Window {win.index[0]:%d}–{win.index[-1]:%d %b}: low ₹{lo:,.0f}, high ₹{hi:,.0f}",
            f"vs cheapest day: {g_paid - g_best:+.4f} g · vs dearest: {g_paid - grams(hi):+.4f} g",
            f"Total: {totals['grams']:.4f} g · {totals['instalments']}/{totals['of']} paid · avg ₹{totals['avg_rate']:,.0f}/g",
            *extra]
    return Alert(f"{day:%B} recap", "\n".join(body), 2, ["bar_chart"])


def digest_alert(day: dt.date, close: pd.Series, totals: dict, extra: list[str]) -> Alert:
    last = close[close.index <= pd.Timestamp(day)]
    wk = last[last.index > pd.Timestamp(day - dt.timedelta(days=7))]
    m30 = last[last.index > pd.Timestamp(day - dt.timedelta(days=30))]
    nxt = (day.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    body = [f"Now ₹{last.iloc[-1]:,.0f}/g · week {pct(last.iloc[-1], wk.iloc[0]):+.1f}% · "
            f"30-day range ₹{m30.min():,.0f}–₹{m30.max():,.0f}",
            f"Next window opens {nxt:%a %d %b}. Total so far {totals['grams']:.4f} g "
            f"({totals['instalments']}/{totals['of']}).", *extra]
    return Alert("weekly digest", "\n".join(body), 2, ["calendar"])


def signal_alert(target: str, est: float, base: float, window_note: str) -> Alert:
    move = pct(est, base)
    arrow = "lower" if move < 0 else "higher"
    return Alert(f"{target} likely ₹{est:,.0f} ({move:+.1f}%)",
                 f"Gold × USD/INR moved {move:+.1f}% since the last rate was set, so the next GRT rate is "
                 f"likely {arrow}: about ₹{est:,.0f}/g (was ₹{base:,.0f}). Right ~87% of the time at 8 AM, "
                 f"~65% at 9 PM. {window_note}".strip(), 3, ["crystal_ball"])


# ------------------------------------------------------------------ runs

def _ai_lines(day: dt.date, rate: float | None, close: pd.Series, extra_facts: dict) -> list[str]:
    from zari import ai, news
    heads, errs = news.headlines()
    facts = {"date": day.isoformat(), "rate_22k_inr_per_g": rate,
             "last_20_rates": {d.strftime("%d %b"): float(v) for d, v in close.iloc[-20:].items()},
             "headlines": [h["title"] for h in heads], **extra_facts}
    view, ai_errs = ai.analyse(facts, day, rate)
    if not view:
        return [f"(AI skipped: {'; '.join((errs + ai_errs))[:120]})"] if (errs or ai_errs) else []
    lines = [f"AI view ({view['confidence']}): next 5 days {view['bias']}. {view['note']}"]
    return lines


def morning(dry_run: bool = False, use_ai: bool = True, today: dt.date | None = None) -> list[Alert]:
    from zari import ai, news
    from zari.fetch import fetch_all
    from zari.store import daily_close, load_payments, save_rates, totals
    today = today or now_ist().date()
    s = state.load()
    rep = fetch_all(today)
    if not dry_run:
        save_rates(list(rep.records.values()))
    close = daily_close()
    alerts: list[Alert] = []
    health = [f"⚠️ {n} failed: {e}" for n, e in rep.errors.items()] + [f"⚠️ {w}" for w in rep.warnings]
    last_run = s.get("last_morning")
    if last_run and (today - dt.date.fromisoformat(last_run)).days > 1:
        health.append(f"⚠️ No morning run since {last_run} - some days were missed.")

    rate = rep.pay_rate
    prior = close[close.index < pd.Timestamp(today)]
    prev = float(prior.iloc[-1]) if len(prior) else None
    _score_signal(today, rep.records.get("mjdta").rate_22k if "mjdta" in rep.records else rate, s, dry_run)

    if rate is None:
        alerts.append(Alert("⚠️ no rate today", "\n".join(health) or "All sources failed.", 4, ["warning"]))
    else:
        payments = load_payments()
        paid = paid_in_month(payments, today)
        deadline = plan()["deadline_day"]
        events = news.events_in_window(today.year, today.month)
        if today.day <= deadline and paid is None:
            extra = [f"📅 {e}" for e in events]
            if use_ai:
                extra += _ai_lines(today, rate, close, {"events_in_window": events, "policy": "pay early in window",
                                                        "days_to_deadline": deadline - today.day})
            alerts.append(pay_alert(today, rate, prev, extra + health))
            s.setdefault("morning_rate", {})[today.isoformat()] = rate
        else:
            m30 = close[(close.index > pd.Timestamp(today - dt.timedelta(days=30))) & (close.index < pd.Timestamp(today))]
            low30 = len(m30) > 10 and rate < float(m30.min())
            if prev and (pct(rate, prev) <= -BIG_MOVE * 100 or low30) and state.once(s, f"drop:{today}"):
                alerts.append(big_drop_alert(rate, prev, low30))
            if today.day == deadline + 1 and paid is not None and state.once(s, f"recap:{today:%Y-%m}"):
                card = ai.scorecard(close)
                extra = [f"AI 5-day calls right {card['right_pct']}% ({card['calls']} calls)"] if card else []
                alerts.append(recap_alert(today, paid, window_series(close, today), totals(payments), extra))
            if today.weekday() == 6 and state.once(s, f"digest:{today}"):
                extra = _ai_lines(today, rate, close, {"note": "weekly digest"}) if use_ai else []
                alerts.append(digest_alert(today, close, totals(payments), extra + health))
            for text in milestones(today):
                alerts.append(Alert("📌 plan reminder", text, 4, ["pushpin"]))
            if health and not alerts:
                alerts.append(Alert("source problem", "\n".join(health), 2, ["warning"]))
    for a in alerts:
        notify.send(a, dry_run)
    if not dry_run:
        s["last_morning"] = today.isoformat()
        state.save(s)
    return alerts


def afternoon(dry_run: bool = False, today: dt.date | None = None) -> list[Alert]:
    from zari.fetch import fetch_all
    from zari.store import load_payments, save_rates
    today = today or now_ist().date()
    if today.day > plan()["deadline_day"] or paid_in_month(load_payments(), today) is not None:
        return []
    s = state.load()
    rep = fetch_all(today)
    if not dry_run:
        save_rates(list(rep.records.values()))
    morning_rate = (s.get("morning_rate") or {}).get(today.isoformat())
    alerts = []
    if rep.pay_rate and morning_rate and rep.pay_rate < morning_rate:
        alerts.append(revised_down_alert(rep.pay_rate, morning_rate))
    for a in alerts:
        notify.send(a, dry_run)
    return alerts


def _base_rate(before: dt.datetime) -> tuple[float, dt.datetime] | None:
    """Latest stored rate set before `before`, and when it was set (IST)."""
    from zari.store import load_rates
    df = load_rates()
    df = df[df.source.isin(["mjdta", "grt"]) & (df.date <= before.date())].copy()
    if df.empty:
        return None
    mj = df[(df.source == "mjdta") & (df.time.str.len() > 0)]
    mj = mj.assign(ts=[dt.datetime.combine(d, dt.time.fromisoformat(t), IST) for d, t in zip(mj.date, mj.time)])
    mj = mj[mj.ts <= before].sort_values("ts")
    if mj.empty:
        return None
    last = mj.iloc[-1]
    grt = df[(df.source == "grt") & (df.date == last.date)]
    rate = float(grt.rate_22k.iloc[-1]) if len(grt) else float(last.rate_22k)
    return rate, last.ts


def signal(dry_run: bool = False, now: dt.datetime | None = None) -> list[Alert]:
    from zari import market
    from zari.store import load_payments
    now = now or now_ist()
    base = _base_rate(now)
    if not base:
        return []
    rate, set_at = base
    df = market.gold_inr_series("5d", "5m")
    est, x = market.estimate_next(rate, market.gold_inr_at(set_at, df), float(df["gold_inr"].iloc[-1]))
    target_day = now.date() if now.hour < 9 else now.date() + dt.timedelta(days=1)
    if target_day.weekday() == 6:                     # no Sunday rate: next is Monday
        target_day += dt.timedelta(days=1)
    s = state.load()
    s["estimate"] = {"for": target_day.isoformat(), "est": round(est, 2), "base": rate, "x": x,
                     "made": now.isoformat(timespec="minutes")}
    deadline = plan()["deadline_day"]
    in_window = target_day.day <= deadline and paid_in_month(load_payments(), target_day) is None
    label = "today" if target_day == now.date() else f"{target_day:%a %d %b}"
    import sys
    sys.stdout.buffer.write(f"estimate for {label}: ₹{est:,.0f} ({(est / rate - 1) * 100:+.2f}% vs ₹{rate:,.0f} "
                            f"set {set_at:%d %b %H:%M})\n".encode("utf-8"))
    alerts = []
    if in_window or abs(x) >= SIGNAL_ALERT:
        note = ""
        if in_window and x <= -SIGNAL_ALERT:
            note = "A sharp fall is likely - if you haven't paid, waiting for that rate may be worth it."
        elif in_window and target_day.day == 1:
            note = "Your payment window opens then."
        if state.once(s, f"signal:{target_day}:{now.hour < 12}"):
            alerts.append(signal_alert(f"{label}:", est, rate, note))
    for a in alerts:
        notify.send(a, dry_run)
    if not dry_run:
        state.save(s)
    return alerts


def _score_signal(today: dt.date, actual: float | None, s: dict, dry_run: bool) -> None:
    """Record yesterday's estimate for today vs the actual morning rate."""
    e = s.get("estimate")
    if dry_run or not e or not actual or e.get("for") != today.isoformat():
        return
    new = not SIGNAL_LOG.exists()
    with open(SIGNAL_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "made", "base", "estimate", "actual", "direction_right"])
        w.writerow([today, e["made"], e["base"], e["est"], actual,
                    int(np.sign(e["est"] - e["base"]) == np.sign(actual - e["base"]))])
    s["estimate"] = None


def slot_for_utc(hhmm: str) -> str:
    """Map a GitHub cron time (UTC) to a slot."""
    return {"02:30": "signal", "04:45": "morning", "11:00": "afternoon", "15:30": "signal"}.get(hhmm, "morning")
