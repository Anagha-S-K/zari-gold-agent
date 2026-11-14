import json
import math

import pandas as pd
import pytest

from zari import market, signal


def test_parse_chart_drops_missing_closes():
    payload = {"chart": {"result": [{"timestamp": [1793923200, 1794009600, 1794096000],
                                     "indicators": {"quote": [{"close": [4000.0, None, 4100.0]}]}}]}}
    s = market.parse_chart(json.dumps(payload))
    assert list(s.values) == [4000.0, 4100.0]


def test_estimate_next_follows_gold_inr_move():
    est, x = market.estimate_next(13000, 380000, 376200)       # gold x INR down 1%
    assert est == pytest.approx(12870, abs=1) and x == pytest.approx(math.log(0.99))


def test_signal_rule_waits_only_when_tomorrow_is_lower():
    idx = pd.to_datetime(["2026-12-01", "2026-12-02", "2026-12-03"])
    w = pd.Series([13000.0, 12800.0, 13100.0], index=idx)
    sig = pd.Series([-0.015, +0.02, 0.0], index=idx)           # 1st: big fall expected; 2nd: rise
    assert signal.signal_rule_price(w, sig, 0.005) == 12800.0   # waited one day, then paid
    assert signal.signal_rule_price(w, sig, 0.02) == 13000.0    # threshold too high: paid on 1st
