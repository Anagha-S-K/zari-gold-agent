"""Paths, settings and IST time helpers."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
IST = ZoneInfo("Asia/Kolkata")


@lru_cache
def settings() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def today_ist() -> dt.date:
    return now_ist().date()
