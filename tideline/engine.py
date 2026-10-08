"""The engine: run each lane, diff today's snapshot against yesterday's, write events, collect signals.

Storage is the git repo itself:
  snapshots/<lane>.jsonl      one sorted JSON object per entity: the state of the world today
  events/YYYY/MM/DD.jsonl     what changed today: added / removed / changed (field, old, new)
  snapshots/_health.json      per-lane status of the latest run
  snapshots/_counts.csv       entity counts per lane per day (cheap trend lines)

"Yesterday" is the snapshot in the last commit made before today (UTC), so a same-day rerun
recomputes the same diff instead of diffing against itself. With no prior commit a lane
bootstraps: it records its snapshot and emits no events, so day one is not "14,000 things
appeared". A lane that fails, or returns far fewer entities than before, keeps its previous
snapshot untouched: an outage never reads as removals.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import subprocess
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "snapshots"
EVENTS = ROOT / "events"
SHRINK_GUARD = 0.5  # a lane returning under half of yesterday's entities is treated as a failed fetch


@dataclass
class Lane:
    name: str                       # short id, used in file names
    title: str                      # display name
    blurb: str                      # one line: what this lane watches
    fetch: Callable[[], list[dict]]  # returns entities, each with a unique "id"
    signals: Callable[["LaneResult"], list[dict]]
    volatile: set[str] = field(default_factory=set)  # stored, but changes are not events (counters, timestamps)
    track_removed: bool = True      # False for rolling windows (e.g. a feed's last 50 incidents)
    label: Callable[[dict], str] = lambda e: e["id"]
    url: Callable[[dict], str | None] = lambda e: e.get("url")


@dataclass
class LaneResult:
    lane: Lane
    today: dict[str, dict]
    base: dict[str, dict] | None   # None = bootstrap (no prior snapshot)
    events: list[dict]
    ok: bool
    error: str | None
    date: str


def utc_today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _parse(text: str) -> dict[str, dict]:
    out = {}
    for line in text.splitlines():
        if line.strip():
            e = json.loads(line)
            out[e["id"]] = e
    return out


def load_base(lane: str, date: str) -> dict[str, dict] | None:
    """The lane's snapshot as of the last commit before `date` 00:00 UTC, else None."""
    ref = (_git("rev-list", "-1", f"--before={date}T00:00:00Z", "HEAD") or "").strip()
    if not ref:
        return None
    text = _git("show", f"{ref}:snapshots/{lane}.jsonl")
    return _parse(text) if text is not None else None


def load_at(lane: str, date: str) -> dict[str, dict] | None:
    """Snapshot as of the last commit before `date` (for week-over-week comparisons)."""
    return load_base(lane, date)


def load_current(lane: str) -> dict[str, dict] | None:
    p = SNAP / f"{lane}.jsonl"
    return _parse(p.read_text()) if p.exists() else None


def write_snapshot(lane: str, entities: dict[str, dict]) -> None:
    SNAP.mkdir(exist_ok=True)
    lines = [json.dumps(entities[k], sort_keys=True, ensure_ascii=False) for k in sorted(entities)]
    (SNAP / f"{lane}.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""))


def diff(lane: Lane, base: dict[str, dict], today: dict[str, dict], date: str) -> list[dict]:
    ev = []
    for k in sorted(today.keys() - base.keys()):
        ev.append({"date": date, "lane": lane.name, "op": "added", "id": k, "entity": today[k]})
    for k in sorted(base.keys() - today.keys()) if lane.track_removed else ():
        ev.append({"date": date, "lane": lane.name, "op": "removed", "id": k, "entity": base[k]})
    for k in sorted(today.keys() & base.keys()):
        a, b = base[k], today[k]
        for f in sorted((a.keys() | b.keys()) - lane.volatile - {"id"}):
            if a.get(f) != b.get(f):
                ev.append({"date": date, "lane": lane.name, "op": "changed", "id": k, "field": f,
                           "old": a.get(f), "new": b.get(f), "entity": b})
    return ev


def run_lane(lane: Lane, date: str) -> LaneResult:
    base = load_base(lane.name, date)
    prev = base if base is not None else load_current(lane.name)
    try:
        rows = lane.fetch()
        today = {}
        for r in rows:
            r["id"] = str(r["id"])
            today[r["id"]] = r
        if not today:
            raise RuntimeError("fetch returned no entities")
        if prev and len(today) < SHRINK_GUARD * len(prev):
            raise RuntimeError(f"fetch returned {len(today)} entities vs {len(prev)} before; treating as a failed fetch")
    except Exception as e:  # noqa: BLE001 - one broken source must not stop the others
        return LaneResult(lane, prev or {}, base, [], False, f"{type(e).__name__}: {e}"[:500], date)
    events = diff(lane, base, today, date) if base is not None else []
    write_snapshot(lane.name, today)
    return LaneResult(lane, today, base, events, True, None, date)


def write_events(date: str, results: list[LaneResult]) -> Path:
    y, m, d = date.split("-")
    path = EVENTS / y / m / f"{d}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = []
    failed = {r.lane.name for r in results if not r.ok}
    if path.exists() and failed:  # a failed rerun keeps that lane's earlier events from today
        keep = [l for l in path.read_text().splitlines() if l and json.loads(l)["lane"] in failed]
    lines = keep + [json.dumps(e, sort_keys=True, ensure_ascii=False) for r in results if r.ok for e in r.events]
    path.write_text("\n".join(lines) + ("\n" if lines else ""))
    return path


def write_health(date: str, results: list[LaneResult]) -> dict:
    SNAP.mkdir(exist_ok=True)
    p = SNAP / "_health.json"
    old = json.loads(p.read_text()) if p.exists() else {}
    health = dict(old)  # lanes not run this time (--lanes) keep their last status
    for r in results:
        prev = old.get(r.lane.name, {})
        health[r.lane.name] = {"ok": r.ok, "error": r.error, "entities": len(r.today), "events": len(r.events),
                               "bootstrap": r.base is None, "date": date,
                               "last_ok": date if r.ok else prev.get("last_ok")}
    p.write_text(json.dumps(health, indent=1, sort_keys=True) + "\n")
    counts = SNAP / "_counts.csv"
    rows = []
    if counts.exists():
        with open(counts) as f:
            rows = [r for r in csv.reader(f)][1:]
    rows = [r for r in rows if r[0] != date]
    rows += [[date, r.lane.name, str(len(r.today))] for r in results if r.ok]
    with open(counts, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "lane", "entities"])
        w.writerows(sorted(rows))
    return health


MAX_SIGNALS_PER_LANE = 40  # one noisy source must not bury the others


def collect_signals(results: list[LaneResult]) -> list[dict]:
    out = []
    for r in results:
        if not r.ok:
            continue
        try:
            for s in sorted(r.lane.signals(r), key=lambda s: -s.get("score", 0))[:MAX_SIGNALS_PER_LANE]:
                s.setdefault("lane", r.lane.name)
                s.setdefault("date", r.date)
                out.append(s)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    return sorted(out, key=lambda s: -s.get("score", 0))


def counts_history() -> dict[str, list[tuple[str, int]]]:
    p = SNAP / "_counts.csv"
    hist: dict[str, list[tuple[str, int]]] = {}
    if p.exists():
        with open(p) as f:
            for row in list(csv.reader(f))[1:]:
                hist.setdefault(row[1], []).append((row[0], int(row[2])))
    return hist
