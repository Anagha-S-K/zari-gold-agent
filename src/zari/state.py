"""Small persistent memory in data/state.json (what was already sent / read)."""
from __future__ import annotations

import json

from zari.config import DATA

STATE = DATA / "state.json"


def load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def save(s: dict) -> None:
    DATA.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, sort_keys=True), encoding="utf-8")


def once(s: dict, key: str) -> bool:
    """True the first time `key` is seen (and records it); False afterwards."""
    sent = s.setdefault("sent", [])
    if key in sent:
        return False
    sent.append(key)
    del sent[:-300]                     # keep the list short
    return True
