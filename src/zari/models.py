from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass
class RateRecord:
    """One 22K rate reading (INR per gram) from one source."""
    source: str
    date: dt.date | None          # date the source says the rate is for (None = not stated)
    rate_22k: float
    time: str = ""                # e.g. "09:46" or "AM"/"PM" when known
    note: str = ""
