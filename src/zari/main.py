"""Zari command line: python -m zari <command>."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from zari.config import DATA, settings, today_ist


def _out(text: str = "") -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))  # ₹ safe on Windows consoles


def cmd_fetch(a) -> int:
    from zari.fetch import fetch_all
    from zari.store import save_rates
    rep = fetch_all()
    _out(f"Zari rate check - {rep.date}")
    for name, rec in rep.records.items():
        when = f"{rec.date} {rec.time}".strip() if rec.date else "date not shown"
        _out(f"  {name:12s} ₹{rec.rate_22k:>10,.2f}   ({when}{', ' + rec.note if rec.note else ''})")
    for name, err in rep.errors.items():
        _out(f"  {name:12s} FAILED: {err}")
    for w in rep.warnings:
        _out(f"  ! {w}")
    if rep.pay_rate:
        _out(f"  Pay rate today: ₹{rep.pay_rate:,.0f}/g from {rep.pay_source} "
             f"= {settings()['plan']['monthly_amount'] / rep.pay_rate:.4f} g for ₹{settings()['plan']['monthly_amount']:,}")
    if not a.dry_run:
        added = save_rates([r for n, r in rep.records.items() if n in ("grt", "mjdta", "goodreturns", "ibja")])
        _out(f"  Saved ({added} new rows) to data/rates.csv")
    return 0 if rep.pay_rate else 1


def cmd_backfill(a) -> int:
    from zari.backfill import backfill
    stats = backfill(start=a.start)
    _out(f"Done: {stats['months']} months fetched, {stats['skipped']} already stored, "
         f"{stats['rows']} new rows, {len(stats['errors'])} errors")
    for e in stats["errors"]:
        _out(f"  ! {e}")
    return 0 if not stats["errors"] else 1


def cmd_backtest(a) -> int:
    from zari.analysis import backtest
    from zari.store import daily_close
    s = daily_close()
    res = backtest(s, paths=a.paths, log=(lambda *_: None) if a.quiet else (lambda m: _out(m)))
    (DATA / "backtest.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    _out(f"\nBacktest: train {res['train_years']}, test {res['test_years']} (unseen)")
    _out(f"Tuned on train: margin {res['tuned_margin']}, best fixed day {res['tuned_fixed_day']}")
    for part in ("train", "test"):
        _out(f"\n{part.upper()}  (₹{res['amount']:,}/month)")
        _out(f"  {'strategy':22s}{'save% vs 1st':>13}{'g/month':>10}{'g/11 mo':>9}{'beat 1st':>9}"
             f"{'worse':>7}{'cheapest':>9}{'<=0.5%':>8}")
        for name, m in res[part].items():
            if not m:
                continue
            _out(f"  {name:22s}{m['avg_saving_pct_vs_day1']:>12.2f}%{m['avg_extra_grams_per_month']:>10.4f}"
                 f"{m['extra_grams_over_11_months']:>9.4f}{m['beat_day1_pct']:>8.0f}%{m['worse_than_day1_pct']:>6.0f}%"
                 f"{m['hit_cheapest_pct']:>8.0f}%{m['within_0_5pct_of_cheapest_pct']:>7.0f}%")
    _out(f"\nVerdict: {res['verdict']}")
    _out("Saved to data/backtest.json")
    return 0


def cmd_signaltest(a) -> int:
    import pandas as pd
    from zari import market, signal
    days = signal.rate_days()
    daily = market.gold_inr_series()
    market.save_daily(daily)
    hourly = market.gold_inr_series("2y", "1h")["gold_inr"]
    pd_ = signal.pairs_daily(days, daily["gold_inr"])
    ph = signal.pairs_hourly(days, hourly)
    res = {"accuracy": {"daily_close_proxy_2016+": signal.accuracy(pd_, "x_close"),
                        "hourly_9pm_2y": signal.accuracy(ph, "x_9pm"),
                        "hourly_8am_2y": signal.accuracy(ph, "x_8am")},
           "rule_daily_proxy": signal.backtest_signal(days, pd_["x_close"]),
           "rule_hourly_9pm": signal.backtest_signal(days, ph["x_9pm"], train_end_year=0),
           "rule_hourly_8am": signal.backtest_signal(days, ph["x_8am"], train_end_year=0)}
    (DATA / "signaltest.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    _out("How well does gold x USD/INR predict the next GRT/MJDTA rate?")
    _out(f"  {'estimate made at':26s}{'days':>6}{'direction':>10}{'big moves':>10}{'error':>8}{'no-change err':>14}")
    for k, v in res["accuracy"].items():
        _out(f"  {k:26s}{v['days']:>6}{v['direction_right_pct']:>9.0f}%{v['big_moves_caught_pct']:>9.0f}%"
             f"{v['median_error_pct']:>7.2f}%{v['median_error_if_no_change_pct']:>13.2f}%")
    _out("\nPay rule 'wait if tomorrow is estimated lower', vs paying on the 1st:")
    for k in ("rule_daily_proxy", "rule_hourly_9pm", "rule_hourly_8am"):
        r = res[k]
        unseen = bool(r["train"].get("zari_signal_rule"))       # tuned on separate years?
        m = r["test"]["zari_signal_rule"]
        label = f"unseen {r['test_years']}" if unseen else f"{r['test_years']}, tuned on same data"
        _out(f"  {k:18s} ({label}, {m['months']} months): save {m['avg_saving_pct_vs_day1']:+.2f}%, "
             f"{m['extra_grams_over_11_months']:+.4f} g over 11 months, beat 1st {m['beat_day1_pct']:.0f}%, "
             f"worse {m['worse_than_day1_pct']:.0f}%")
    _out("Saved to data/signaltest.json")
    return 0


def cmd_research(a) -> int:
    import numpy as np
    import pandas as pd
    from zari import market, research, signal
    days = signal.rate_days()
    if not market.MARKET.exists():
        market.save_daily(market.gold_inr_series())
    gi = pd.read_csv(market.MARKET, index_col=0, parse_dates=True)["gold_inr"]
    sig = signal.pairs_daily(days, gi)["x_close"]
    mu = np.log(days["close"]).diff().ewm(span=60).mean()
    ev = research.evaluate(research.strategies(days, sig, mu, sim_paths=a.paths))
    (DATA / "research.json").write_text(json.dumps(ev, indent=2), encoding="utf-8")
    _out(f"Tuned on {research.TRAIN_END} and earlier; judged on later years (unseen). Baseline = 1st, morning rate.")
    _out(f"  {'strategy':44s}{'train save%':>12}{'test save%':>11}{'test g/11mo':>12}{'beat':>6}{'worse':>6}")
    for k, v in ev.items():
        if k.startswith(("sig_0", "sig_trenddown_0")):
            continue
        tr, te = v["train"], v["test"]
        _out(f"  {k:44s}{tr['avg_saving_pct_vs_day1']:>11.2f}%{te['avg_saving_pct_vs_day1']:>10.2f}%"
             f"{te['extra_grams_over_11_months']:>12.4f}{te['beat_day1_pct']:>5.0f}%{te['worse_than_day1_pct']:>5.0f}%")
    _out("Saved to data/research.json")
    return 0


def cmd_run(a) -> int:
    from zari import run
    slot = a.slot
    if a.cron:                                    # e.g. "45 4 * * *" from GitHub's schedule
        mins, hours = a.cron.split()[:2]
        slot = run.slot_for_utc(f"{int(hours):02d}:{int(mins):02d}")
    today = dt.date.fromisoformat(a.date) if a.date else None
    if slot == "morning":
        alerts = run.morning(a.dry_run, use_ai=not a.no_ai, today=today)
    elif slot == "afternoon":
        alerts = run.afternoon(a.dry_run, today=today)
    else:
        alerts = run.signal(a.dry_run)
    _out(f"{slot}: {len(alerts)} alert(s)")
    return 0


def cmd_inbox(a) -> int:
    from zari import inbox
    return inbox.run(a.dry_run)


def cmd_notify_test(a) -> int:
    from zari import notify
    sent = notify.send(notify.Alert("test ✅", "If you can read this, Zari can reach your phone. "
                                    "Tap 'I paid' only for a real payment (send 'undo' if you tap it by mistake).",
                                    3, ["wave"], pay_buttons=a.buttons))
    _out("sent" if sent else "not sent (topic not set)")
    return 0


def _margin() -> float:
    m = settings()["model"].get("margin")
    if m is None:
        bt = DATA / "backtest.json"
        m = json.loads(bt.read_text(encoding="utf-8"))["tuned_margin"] if bt.exists() else 0.002
    return float(m)


def cmd_decide(a) -> int:
    from zari.analysis import decide
    from zari.store import daily_close
    day = dt.date.fromisoformat(a.date) if a.date else today_ist()
    res = decide(daily_close(), day, _margin())
    for k, v in res.items():
        _out(f"  {k:22s} {v}")
    return 0


def cmd_status(a) -> int:
    from zari.store import load_payments, totals
    df = load_payments()
    _out("Zari - payments")
    for _, r in df.iterrows():
        _out(f"  #{r['n']:<3} {r['date']}  ₹{float(r['amount']):,.0f} @ ₹{float(r['rate']):,.0f} = {float(r['grams']):.4f} g")
    t = totals(df)
    _out(f"  Total: {t['instalments']}/{t['of']} paid, ₹{t['paid']:,.0f}, {t['grams']:.4f} g, avg ₹{t['avg_rate']:,.0f}/g")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="zari", description="Zari - best day to pay your GRT gold instalment")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="today's 22K rate from every source")
    f.add_argument("--dry-run", action="store_true", help="show only, don't save")
    f.set_defaults(fn=cmd_fetch)
    b = sub.add_parser("backfill", help="load MJDTA history into data/rates.csv")
    b.add_argument("--start", help="YYYY-MM (default from config)")
    b.set_defaults(fn=cmd_backfill)
    t = sub.add_parser("backtest", help="replay past windows and compare strategies")
    t.add_argument("--paths", type=int, default=1500)
    t.add_argument("--quiet", action="store_true")
    t.set_defaults(fn=cmd_backtest)
    g = sub.add_parser("signaltest", help="test the overnight gold x USD/INR signal on history")
    g.set_defaults(fn=cmd_signaltest)
    rs = sub.add_parser("research", help="compare extra pay-day strategies on history")
    rs.add_argument("--paths", type=int, default=800, help="simulated futures for the sim+signal variant (0 = skip)")
    rs.set_defaults(fn=cmd_research)
    r = sub.add_parser("run", help="scheduled run: morning | afternoon | signal")
    r.add_argument("--slot", choices=["morning", "afternoon", "signal"], default="morning")
    r.add_argument("--cron", help="GitHub schedule string; picks the slot automatically")
    r.add_argument("--dry-run", action="store_true", help="print alerts, don't send or save")
    r.add_argument("--no-ai", action="store_true", help="skip news + AI")
    r.add_argument("--date", help="pretend today is YYYY-MM-DD (morning/afternoon testing)")
    r.set_defaults(fn=cmd_run)
    ib = sub.add_parser("inbox", help="read 'paid' replies from your phone and log them")
    ib.add_argument("--dry-run", action="store_true")
    ib.set_defaults(fn=cmd_inbox)
    nt = sub.add_parser("notify-test", help="send a test alert to your phone")
    nt.add_argument("--buttons", action="store_true", help="include the I paid / Open GRT buttons")
    nt.set_defaults(fn=cmd_notify_test)
    d = sub.add_parser("decide", help="what Zari says for a day (default today)")
    d.add_argument("--date", help="YYYY-MM-DD")
    d.set_defaults(fn=cmd_decide)
    s = sub.add_parser("status", help="payments and totals")
    s.set_defaults(fn=cmd_status)
    a = p.parse_args(argv)
    return a.fn(a)
