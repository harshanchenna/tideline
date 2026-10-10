"""Helpers shared by lanes."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict


def days_ago(date: str, n: int) -> str:
    return (dt.date.fromisoformat(date) - dt.timedelta(days=n)).isoformat()


def by(items, key):
    out = defaultdict(list)
    for i in items:
        out[key(i)].append(i)
    return out


def sig(kind: str, title: str, detail: str = "", org: str | None = None, items=None, score: float = 1.0) -> dict:
    return {"kind": kind, "title": title, "detail": detail, "org": org, "items": items or [], "score": round(score, 2)}


def item(label: str, url: str | None = None, note: str = "") -> dict:
    return {"label": label, "url": url, "note": note}


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else many or word + 's'}"
