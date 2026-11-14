"""Send Zari alerts to your phone with ntfy (free, no account).

Topics come from environment variables (GitHub secrets):
  NTFY_ALERT_TOPIC - Zari -> you   (subscribe to this in the ntfy app)
  NTFY_REPLY_TOPIC - you -> Zari   (the "I paid" button posts here)
A topic name works like a password: long and random, never committed to the repo."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

import requests

from zari.config import settings

SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")


@dataclass
class Alert:
    title: str
    body: str
    priority: int = 3                 # 1 min .. 5 urgent
    tags: list[str] = field(default_factory=list)
    pay_buttons: bool = False         # add "I paid" + "Open GRT"


def topics() -> tuple[str | None, str | None]:
    return os.environ.get("NTFY_ALERT_TOPIC"), os.environ.get("NTFY_REPLY_TOPIC")


def payload(alert: Alert, alert_topic: str, reply_topic: str | None) -> dict:
    amount = settings()["plan"]["monthly_amount"]
    p = {"topic": alert_topic, "title": f"Zari: {alert.title}", "message": alert.body,
         "priority": alert.priority, "tags": alert.tags}
    if alert.pay_buttons:
        actions = [{"action": "view", "label": "Open GRT", "url": "https://www.grtjewels.com/"}]
        if reply_topic:
            actions.insert(0, {"action": "http", "label": f"I paid ₹{amount:,} today",
                               "url": f"{SERVER}/{reply_topic}", "method": "POST",
                               "body": f"paid {amount}", "clear": True})
        p["actions"] = actions
    return p


def send(alert: Alert, dry_run: bool = False) -> bool:
    alert_topic, reply_topic = topics()
    if dry_run or not alert_topic:
        why = "dry run" if dry_run else "NTFY_ALERT_TOPIC not set"
        sys.stdout.buffer.write(f"\n[{why} - not sent] Zari: {alert.title} (priority {alert.priority})\n"
                                f"{alert.body}\n".encode("utf-8"))
        if alert.pay_buttons:
            sys.stdout.buffer.write("  [buttons: I paid | Open GRT]\n".encode("utf-8"))
        return False
    r = requests.post(SERVER, data=json.dumps(payload(alert, alert_topic, reply_topic)).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, timeout=30)
    r.raise_for_status()
    return True
