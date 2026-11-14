"""Alerts, ntfy payloads, phone replies and AI output checks (no network)."""
import datetime as dt

import pandas as pd
import pytest

from zari import ai, inbox, news, notify, run


def test_paid_message_parsing():
    assert inbox.parse("paid") == {"cmd": "paid", "amount": 10000.0, "rate": None}
    assert inbox.parse("Paid 10,000 13340") == {"cmd": "paid", "amount": 10000.0, "rate": 13340.0}
    assert inbox.parse("paid ₹10000 @ 13340") == {"cmd": "paid", "amount": 10000.0, "rate": 13340.0}
    assert inbox.parse("UNDO") == {"cmd": "undo"}
    assert inbox.parse("hello") is None


def test_ntfy_payload_has_paid_button_posting_to_reply_topic():
    p = notify.payload(notify.Alert("✅ PAY TODAY", "body", 4, ["moneybag"], pay_buttons=True), "alertX", "replyY")
    assert p["topic"] == "alertX" and p["title"] == "Zari: ✅ PAY TODAY" and p["priority"] == 4
    paid = p["actions"][0]
    assert paid["action"] == "http" and paid["url"].endswith("/replyY") and paid["body"] == "paid 10000"
    assert p["actions"][1]["url"] == "https://www.grtjewels.com/"


def test_pay_alert_escalates_towards_deadline():
    early = run.pay_alert(dt.date(2026, 12, 1), 13150, 13140, [])
    guard = run.pay_alert(dt.date(2026, 12, 8), 13150, 13140, [])
    last = run.pay_alert(dt.date(2026, 12, 10), 13150, 13140, [])
    assert early.title == "✅ PAY TODAY" and early.priority == 4 and early.pay_buttons
    assert "2 days to deadline" in guard.title and guard.priority == 5
    assert "LAST DAY" in last.title and last.priority == 5
    assert "0.7605 g" in early.body


def test_recap_compares_with_best_and_worst_day():
    win = pd.Series([13385.0, 13100.0, 13500.0], index=pd.to_datetime(["2026-12-01", "2026-12-02", "2026-12-03"]))
    row = pd.Series({"rate": 13385.0, "date": "2026-12-01", "grams": 0.7471})
    a = run.recap_alert(dt.date(2026, 12, 11), row, win, {"grams": 1.8, "instalments": 2, "of": 11,
                                                          "avg_rate": 13385}, [])
    assert "low ₹13,100" in a.body and "high ₹13,500" in a.body and "vs cheapest day: -0.0163 g" in a.body


def test_events_include_first_friday_jobs_report():
    assert news.first_friday(2026, 12) == dt.date(2026, 12, 4)
    assert any("jobs report" in e for e in news.events_in_window(2026, 12))


def test_rss_parsing():
    xml = ("<rss><channel><item><title>Gold slips as dollar firms</title><pubDate>Fri, 06 Nov 2026 08:00:00 GMT"
           "</pubDate><source>Reuters</source></item></channel></rss>")
    assert news.parse_rss(xml, 5) == [{"title": "Gold slips as dollar firms", "date": "Fri, 06 Nov 2026",
                                       "source": "Reuters"}]


def test_ai_output_is_validated():
    v = ai.validate('Sure! {"bias":"down","confidence":"medium","reasons":["a","b","c","d"],"note":"Fed hawkish"}')
    assert v["bias"] == "down" and len(v["reasons"]) == 3
    with pytest.raises(ValueError):
        ai.validate('{"bias":"crash","confidence":"high"}')


def test_cron_maps_to_slots():
    assert run.slot_for_utc("04:45") == "morning"
    assert run.slot_for_utc("11:00") == "afternoon"
    assert run.slot_for_utc("02:30") == run.slot_for_utc("15:30") == "signal"
