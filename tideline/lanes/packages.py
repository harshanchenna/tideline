"""Packages lane: releases and download momentum for a curated set of AI SDKs and frameworks."""
from __future__ import annotations

import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tideline.engine import ROOT, Lane, LaneResult
from tideline.http import get_json
from tideline.lanes.common import days_ago, item, sig

CONFIG = ROOT / "config" / "packages.json"


def _pypi(name: str) -> dict | None:
    d = get_json(f"https://pypi.org/pypi/{name}/json")
    if not d:
        return None
    ver = d["info"]["version"]
    files = d.get("releases", {}).get(ver) or []
    released = min((f["upload_time_iso_8601"] for f in files), default=None)
    dl = get_json(f"https://pypistats.org/api/packages/{name.lower()}/recent")
    return {"id": f"pypi:{name}", "eco": "pypi", "name": name, "version": ver,
            "released": released[:10] if released else None,
            "downloads_week": ((dl or {}).get("data") or {}).get("last_week"),
            "url": f"https://pypi.org/project/{name}/"}


def _npm(name: str) -> dict | None:
    q = urllib.parse.quote(name, safe="@")
    d = get_json(f"https://registry.npmjs.org/{q.replace('/', '%2F')}")
    if not d or "dist-tags" not in d:
        return None
    ver = d["dist-tags"].get("latest")
    dl = get_json(f"https://api.npmjs.org/downloads/point/last-week/{name}")
    return {"id": f"npm:{name}", "eco": "npm", "name": name, "version": ver,
            "released": (d.get("time") or {}).get(ver, "")[:10] or None,
            "downloads_week": (dl or {}).get("downloads"),
            "url": f"https://www.npmjs.com/package/{name}"}


def fetch() -> list[dict]:
    cfg = json.loads(CONFIG.read_text())
    jobs = [(_pypi, n) for n in cfg["pypi"]] + [(_npm, n) for n in cfg["npm"]]
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda j: j[0](j[1]), jobs))
    return [r for r in rows if r]


def bump(old: str | None, new: str | None) -> str:
    """'major' / 'minor' / 'patch' / 'other' for a version change."""
    def parts(v):
        return [int(x) for x in re.findall(r"\d+", v or "")[:3]] + [0, 0, 0]
    a, b = parts(old)[:3], parts(new)[:3]
    if b[0] != a[0]:
        return "major" if a[0] else "minor"  # 0.x -> 0.y behaves like a minor bump
    if b[1] != a[1]:
        return "minor" if b[0] else "major-ish" if b[1] > a[1] else "minor"
    return "patch" if b[2] != a[2] else "other"


def fmt(n) -> str:
    if n is None:
        return "?"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e3 else str(n)


def signals(r: LaneResult) -> list[dict]:
    out = []
    if r.base is None:
        for e in r.today.values():
            if (e.get("released") or "") >= days_ago(r.date, 3):
                out.append(sig("release", f"{e['name']} {e['version']} released {e['released']} ({e['eco']})",
                               f"{fmt(e.get('downloads_week'))} downloads last week", e["name"].split("/")[0].lstrip("@"),
                               [item(f"{e['name']} {e['version']}", e["url"])], 2 + (e.get("downloads_week") or 0) ** 0.25 / 10))
        return out
    for ev in r.events:
        if ev["op"] == "changed" and ev["field"] == "version":
            e, kind = ev["entity"], bump(ev["old"], ev["new"])
            if kind == "patch":
                continue
            score = {"major": 9, "major-ish": 7, "minor": 5}.get(kind, 3) + (e.get("downloads_week") or 0) ** 0.25 / 10
            out.append(sig("release", f"{e['name']} {ev['old']} → {ev['new']} ({kind} release, {e['eco']})",
                           f"{fmt(e.get('downloads_week'))} downloads last week", e["name"].split("/")[0].lstrip("@"),
                           [item(f"{e['name']} {e['version']}", e["url"])], score))
    week = None
    try:
        from tideline.engine import load_at
        week = load_at("packages", days_ago(r.date, 7))
    except Exception:  # noqa: BLE001
        week = None
    if week:
        for k, e in r.today.items():
            a, b = (week.get(k) or {}).get("downloads_week"), e.get("downloads_week")
            if a and b and a >= 10_000 and b / a >= 1.5:
                out.append(sig("momentum", f"{e['name']} weekly downloads up {b / a - 1:.0%} in a week ({fmt(a)} → {fmt(b)})",
                               "", e["name"].split("/")[0].lstrip("@"), [item(e["name"], e["url"])], 4 + min(b / a, 5)))
    return out


LANE = Lane(name="packages", title="Packages", blurb="Releases and download momentum for the SDKs, frameworks and engines AI is built on.",
            fetch=fetch, signals=signals, volatile={"downloads_week"}, label=lambda e: f"{e['name']} ({e['eco']})")
