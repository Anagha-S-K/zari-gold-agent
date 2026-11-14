"""AI analyst: a free LLM reads rates, Zari's numbers, headlines and event dates, and returns
a short, checked JSON view. It explains context; it never changes the pay day or any number.

Providers (both free tiers), tried in order:
  1. GitHub Models  - uses GITHUB_TOKEN (automatic inside GitHub Actions)
  2. Google Gemini  - uses GEMINI_API_KEY if you add it
Every call is logged to data/ai_log.jsonl and scored later against what gold actually did."""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import requests

from zari.config import DATA, settings

LOG = DATA / "ai_log.jsonl"
GITHUB_URL = "https://models.github.ai/inference/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM = ("You are Zari's gold market analyst for an Indian buyer of 22K gold. Use only the facts given. "
          "Answer with ONE JSON object and nothing else: "
          '{"bias":"down|flat|up","confidence":"low|medium|high","reasons":["...","...","..."],'
          '"note":"<=160 characters, plain English, for a phone alert"}. '
          "bias = likely direction of the Indian 22K rate over the next 5 trading days. "
          "Never give investment advice; never suggest paying after the deadline.")


def _cfg() -> dict:
    return settings().get("ai") or {}


def prompt(facts: dict) -> str:
    return "Facts:\n" + json.dumps(facts, ensure_ascii=False, indent=1, default=str)


def validate(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    d = json.loads(m.group(0))
    if d.get("bias") not in ("down", "flat", "up") or d.get("confidence") not in ("low", "medium", "high"):
        raise ValueError(f"bad fields: {d}")
    reasons = [str(r)[:140] for r in (d.get("reasons") or [])][:3]
    return {"bias": d["bias"], "confidence": d["confidence"], "reasons": reasons,
            "note": str(d.get("note", ""))[:160]}


def _github(p: str) -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set")
    r = requests.post(GITHUB_URL, timeout=60, headers={"Authorization": f"Bearer {token}",
                                                       "Content-Type": "application/json"},
                      json={"model": _cfg().get("github_model", "openai/gpt-4.1-mini"), "temperature": 0.2,
                            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": p}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _gemini(p: str) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    url = GEMINI_URL.format(model=_cfg().get("gemini_model", "gemini-2.5-flash"))
    r = requests.post(url, timeout=60, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                      json={"systemInstruction": {"parts": [{"text": SYSTEM}]},
                            "contents": [{"parts": [{"text": p}]}],
                            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}})
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def analyse(facts: dict, today: dt.date, rate: float | None) -> tuple[dict | None, list[str]]:
    """Returns (view or None, errors). Logs successful views."""
    if not _cfg().get("enabled", True):
        return None, ["AI disabled in config"]
    errors = []
    for name, fn in (("github", _github), ("gemini", _gemini)):
        try:
            view = validate(fn(prompt(facts)))
            view["provider"] = name
            DATA.mkdir(exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"date": today.isoformat(), "rate": rate, **view}, ensure_ascii=False) + "\n")
            return view, errors
        except Exception as e:
            errors.append(f"{name}: {e}")
    return None, errors


def scorecard(close, horizon: int = 5) -> dict | None:
    """How often the AI's up/down calls matched the next `horizon` trading days."""
    if not LOG.exists():
        return None
    import pandas as pd
    hits = total = 0
    for line in LOG.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["bias"] == "flat":
            continue
        d = pd.Timestamp(e["date"])
        after = close[close.index > d]
        upto = close[close.index <= d]
        if len(after) < horizon or upto.empty:
            continue
        went_up = after.iloc[horizon - 1] > upto.iloc[-1]
        total += 1
        hits += (e["bias"] == "up") == went_up
    return {"calls": total, "right_pct": round(100 * hits / total, 1)} if total else None
