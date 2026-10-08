"""Teams lane: what AI companies are staffing up for, read from their public job boards.

Aggregate only. Individual postings are read, tagged, and reduced to company-level counts and
"team forming" notes at fetch time; no posting, pay range, or apply link is ever stored or
published. The question this lane answers is "what is this company building", not "who is hiring".
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from tideline.engine import ROOT, Lane, LaneResult
from tideline.lanes import _ats
from tideline.lanes.common import sig

CONFIG = ROOT / "config" / "teams.json"
WINDOW = 14
TECH = ("Engineering", "Research", "Data", "Hardware", "Security")


def _company(c: dict, today: str) -> dict | None:
    posts = _ats.fetch_board(c)
    if posts is None:
        return None
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=WINDOW)).isoformat()
    for p in posts:
        p["themes"] = json.loads(p["themes"])
    # Dedupe one role listed in several locations.
    uniq = {(p["title"].strip().lower()): p for p in posts}.values()
    recent = [p for p in uniq if (p["posted_at"] or "") >= cutoff]
    older = [p for p in uniq if (p["posted_at"] or "") < cutoff]
    n, nr = len(uniq), len(recent)
    has_baseline = len(older) >= max(5, 0.2 * n)
    notes = []
    if has_baseline:
        rt, ot = Counter(t for p in recent for t in p["themes"]), Counter(t for p in older for t in p["themes"])
        for theme, k in rt.items():
            ex = [p["title"] for p in recent if theme in p["themes"]][:3]
            if k >= 3 and ot[theme] == 0:
                notes.append({"kind": "new_team", "score": 8 + 2 * k, "examples": ex,
                              "title": f"{c['name']} is building a {theme} team: {k} roles, none before"})
            elif k >= 5 and older:
                rs, os_ = k / nr, ot[theme] / len(older)
                if rs >= 2 * os_ and rs >= 0.15:
                    notes.append({"kind": "theme_push", "score": k * (rs / max(os_, 0.02)) ** 0.5, "examples": ex,
                                  "title": f"{c['name']} is leaning into {theme}: {rs:.0%} of new roles vs {os_:.0%} before"})
        if nr >= 10 and nr / n >= 0.25:
            fn = Counter(p["function"] for p in recent).most_common(2)
            notes.append({"kind": "surge", "score": nr * nr / n, "examples": [],
                          "title": f"{c['name']} opened {nr} roles in {WINDOW} days ({nr / n:.0%} of its open book), "
                                   f"mostly {' and '.join(f for f, _ in fn)}"})
        leads = [p["title"] for p in recent if p["leadership"] == 2]
        if leads:
            notes.append({"kind": "leadership", "score": 2 + 3 * len(leads), "examples": leads[:4],
                          "title": f"{c['name']} is hiring to lead new work: {leads[0]}" + (" and more" if len(leads) > 1 else "")})
    return {"id": c["name"], "sector": c["sector"], "open": n, "recent": nr,
            "tech_share": round(sum(p["function"] in TECH for p in uniq) / n, 3) if n else 0,
            "top_themes": [t for t, _ in Counter(t for p in uniq for t in p["themes"]).most_common(5)],
            "notes": notes, "url": None}


def fetch() -> list[dict]:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    cfg = json.loads(CONFIG.read_text())["companies"]
    with ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(lambda c: _company(c, today), cfg))
    if sum(r is not None for r in rows) < 0.8 * len(cfg):
        raise RuntimeError("fewer than 80% of job boards fetched")
    return [r for r in rows if r and r["open"]]


def signals(r: LaneResult) -> list[dict]:
    out = []
    for e in r.today.values():
        for n in e.get("notes") or []:
            detail = ("Example roles: " + "; ".join(n["examples"])) if n["examples"] else ""
            out.append(sig(n["kind"], n["title"], detail, e["id"].lower(), [], n["score"] * 0.4))  # supporting context: weighted below the other lanes
    if r.base:
        for k, e in r.today.items():
            prev = (r.base.get(k) or {}).get("open")
            if prev and prev - e["open"] >= max(10, 0.15 * prev):
                out.append(sig("pullback", f"{k} closed {prev - e['open']} roles since yesterday ({(prev - e['open']) / prev:.0%} of its open book)",
                               "A filled batch, a freeze, or a reorg.", k.lower(), [], 4))
    return out


LANE = Lane(name="teams", title="Teams", blurb="What AI companies are staffing up for: new teams, pushes, leadership roles. Aggregates only.",
            fetch=fetch, signals=signals, volatile={"open", "recent", "tech_share", "top_themes", "notes"},
            label=lambda e: e["id"])
