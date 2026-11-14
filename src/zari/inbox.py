"""Read your replies from the ntfy reply topic and log payments.

Understood messages (case doesn't matter):
  paid                 -> your monthly amount at that day's GRT rate
  paid 10000           -> same, amount given
  paid 10000 13340     -> rate taken from your receipt (most exact)
  undo                 -> remove the last payment logged from the phone
ntfy.sh keeps messages ~12 hours, so this runs every 2 hours."""
from __future__ import annotations

import datetime as dt
import json
import re

import pandas as pd

from zari import notify, state
from zari.config import IST, settings
from zari.store import PAYMENTS, add_payment, load_payments, load_rates, totals
from zari.web import get

_PAID = re.compile(r"^\s*paid\b\s*(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)?\s*(?:@|at)?\s*(?:₹|rs\.?)?\s*([\d,]+(?:\.\d+)?)?\s*$", re.I)


def parse(text: str) -> dict | None:
    t = text.strip()
    if re.fullmatch(r"\s*undo\s*", t, re.I):
        return {"cmd": "undo"}
    m = _PAID.match(t)
    if not m:
        return None
    num = lambda g: float(g.replace(",", "")) if g else None  # noqa: E731
    amount, rate = num(m.group(1)), num(m.group(2))
    return {"cmd": "paid", "amount": amount or float(settings()["plan"]["monthly_amount"]), "rate": rate}


def rate_for(day: dt.date) -> tuple[float | None, str]:
    """Best known GRT pay rate for a day: GRT reading, else MJDTA board rate."""
    df = load_rates()
    df = df[df.date == day].sort_values("time")
    for src in ("grt", "mjdta"):
        rows = df[df.source == src]
        if len(rows):
            return float(rows.rate_22k.iloc[-1]), src
    return None, ""


def poll(topic: str, since: str) -> list[dict]:
    from zari.notify import SERVER
    text = get(f"{SERVER}/{topic}/json", params={"poll": 1, "since": since})
    msgs = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [m for m in msgs if m.get("event") == "message"]


def handle(msg: dict, dry_run: bool = False) -> notify.Alert | None:
    cmd = parse(msg.get("message", ""))
    when = dt.datetime.fromtimestamp(msg["time"], IST)
    if cmd is None:
        return notify.Alert("didn't understand", f'"{msg.get("message", "")[:60]}" - send e.g. "paid 10000" '
                            'or "paid 10000 13340" (amount, rate from receipt), or "undo".', 3, ["question"])
    payments = load_payments()
    if cmd["cmd"] == "undo":
        phone = payments[payments["note"].astype(str).str.contains("phone")]
        if phone.empty:
            return notify.Alert("nothing to undo", "No phone-logged payment found.", 3, ["information_source"])
        last = phone.iloc[-1]
        if not dry_run:
            payments[payments["n"] != last["n"]].to_csv(PAYMENTS, index=False)
        return notify.Alert("payment removed", f"Removed #{last['n']} ({last['date']}, ₹{float(last['amount']):,.0f}).",
                            3, ["wastebasket"])
    month = when.strftime("%Y-%m")
    already = payments[payments["date"].astype(str).str.startswith(month)]
    if len(already):
        a = already.iloc[-1]
        return notify.Alert("already logged", f"#{a['n']} on {a['date']} is already logged for {month}. "
                            'Send "undo" first if it was wrong.', 3, ["information_source"])
    rate, src = (cmd["rate"], "receipt") if cmd["rate"] else rate_for(when.date())
    if not rate:
        return notify.Alert("need the rate", f"No GRT rate stored for {when.date()}. "
                            f'Send "paid {cmd["amount"]:.0f} <rate from receipt>".', 4, ["warning"])
    if dry_run:
        grams = round(cmd["amount"] / rate, 4)
        return notify.Alert("would log payment", f"₹{cmd['amount']:,.0f} @ ₹{rate:,.0f} = {grams:.4f} g", 3, [])
    row = add_payment(when.date(), cmd["amount"], rate, note=f"phone ({src})")
    t = totals()
    return notify.Alert(f"logged #{row['n']} ✅",
                        f"₹{row['amount']:,.0f} @ ₹{rate:,.0f}/g ({src}) = {row['grams']:.4f} g\n"
                        f"Total: {t['grams']:.4f} g · {t['instalments']}/{t['of']} paid · avg ₹{t['avg_rate']:,.0f}/g",
                        3, ["white_check_mark"])


def run(dry_run: bool = False) -> int:
    _, reply = notify.topics()
    if not reply:
        print("NTFY_REPLY_TOPIC not set - nothing to read")
        return 0
    s = state.load()
    since = s.get("inbox_since", "12h")
    msgs = poll(reply, since)
    for m in msgs:
        alert = handle(m, dry_run)
        if alert:
            notify.send(alert, dry_run)
    if msgs and not dry_run:
        s["inbox_since"] = msgs[-1]["id"]
        state.save(s)
    print(f"inbox: {len(msgs)} message(s)")
    return 0
