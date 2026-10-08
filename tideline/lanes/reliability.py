"""Reliability lane: incidents from AI providers' public status pages (Atlassian Statuspage feeds)."""
from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

from tideline.engine import ROOT, Lane, LaneResult
from tideline.http import get_json
from tideline.lanes.common import days_ago, item, plural, sig

CONFIG = ROOT / "config" / "status.json"
IMPACT_RANK = {"none": 0, "minor": 1, "major": 2, "critical": 3, "maintenance": 0}


def _ts(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _provider(item_: tuple[str, str]) -> list[dict] | None:
    name, base = item_
    d = get_json(base.rstrip("/") + "/api/v2/incidents.json")
    if not d or "incidents" not in d:
        return None
    out = []
    for i in d["incidents"]:
        start, end = _ts(i.get("started_at") or i.get("created_at")), _ts(i.get("resolved_at"))
        out.append({"id": f"{name}:{i['id']}", "provider": name, "name": (i.get("name") or "")[:160],
                    "impact": i.get("impact"), "status": i.get("status"),
                    "started": start.isoformat()[:16] if start else None,
                    "resolved": end.isoformat()[:16] if end else None,
                    "minutes": int((end - start).total_seconds() // 60) if start and end else None,
                    "components": sorted({c.get("name") for c in i.get("components") or [] if c.get("name")})[:6],
                    "url": i.get("shortlink") or base})
    return out


def fetch() -> list[dict]:
    cfg = {k: v for k, v in json.loads(CONFIG.read_text()).items() if not k.startswith("_")}
    with ThreadPoolExecutor(max_workers=6) as pool:
        res = list(pool.map(_provider, cfg.items()))
    if sum(r is not None for r in res) < len(cfg) / 2:
        raise RuntimeError("most status pages unreachable")
    return [i for r in res if r for i in r]


def summary(today: dict[str, dict], date: str, days: int = 30) -> list[dict]:
    """Per provider over the window: incidents, major+ incidents, and total incident minutes."""
    since = days_ago(date, days)
    rows = {}
    for e in today.values():
        if (e.get("started") or "") < since or e.get("impact") == "maintenance":
            continue
        r = rows.setdefault(e["provider"], {"provider": e["provider"], "incidents": 0, "major": 0, "minutes": 0})
        r["incidents"] += 1
        r["major"] += IMPACT_RANK.get(e.get("impact"), 0) >= 2
        r["minutes"] += e.get("minutes") or 0
    return sorted(rows.values(), key=lambda r: (-r["major"], -r["minutes"]))


def signals(r: LaneResult) -> list[dict]:
    out = []
    since = days_ago(r.date, 1)
    fresh = [e for e in r.today.values() if (e.get("started") or "") >= since and e.get("impact") != "maintenance"]
    for e in fresh:
        rank = IMPACT_RANK.get(e.get("impact"), 0)
        dur = f"{e['minutes']} min" if e.get("minutes") is not None else "ongoing"
        out.append(sig("incident", f"{e['provider']}: {e['name']} ({e.get('impact') or 'no'} impact, {dur})",
                       ", ".join(e.get("components") or []), e["provider"].lower(), [item(e["name"], e["url"])],
                       2 + 3 * rank + min((e.get("minutes") or 0) / 60, 4)))
    worst = summary(r.today, r.date, 7)
    if worst and worst[0]["incidents"] >= 3:
        w = worst[0]
        out.append(sig("reliability_week", f"{w['provider']} had {plural(w['incidents'], 'incident')} in 7 days "
                       f"({w['major']} major, {w['minutes'] // 60}h {w['minutes'] % 60}m total)", "", w["provider"].lower(), [], 4 + w["major"]))
    return out


LANE = Lane(name="reliability", title="Reliability", blurb="Incidents on AI providers' public status pages: what broke, for how long.",
            fetch=fetch, signals=signals, track_removed=False, label=lambda e: f"{e['provider']}: {e['name']}")
