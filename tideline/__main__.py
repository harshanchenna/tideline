"""Command line: `python -m tideline run` does one daily pass; `site` re-renders from what's on disk.

    run [--lanes a,b] [--brief] [--no-site]   fetch every lane, diff, write events + digest, render
    site                                      render site/ from the committed digests and snapshots

A digest (digests/YYYY/MM/DD.json) is the day's readable layer: lane health, ranked signals,
and the optional brief. It is what the site and the feed are built from, so the site can be
rebuilt for any past day without refetching anything.
"""
from __future__ import annotations

import argparse
import json
import sys

from tideline import engine
from tideline.lanes import LANES

DIGESTS = engine.ROOT / "digests"


def digest_path(date: str):
    y, m, d = date.split("-")
    return DIGESTS / y / m / f"{d}.json"


def write_digest(date: str, results: list[engine.LaneResult], health: dict, signals: list[dict],
                 brief: list[str] | None) -> dict:
    path = digest_path(date)
    failed = {r.lane.name for r in results if not r.ok}
    ran = {r.lane.name for r in results}
    old = json.loads(path.read_text()) if path.exists() else {}
    # A same-day rerun replaces signals for lanes that ran and succeeded, and keeps the rest:
    # lanes that failed this time, and lanes not selected with --lanes.
    keep = [s for s in old.get("signals", []) if s["lane"] in failed or s["lane"] not in ran]
    merged = sorted(keep + signals, key=lambda s: -s.get("score", 0))
    digest = {"date": date, "health": {**old.get("health", {}), **health}, "signals": merged,
              "brief": brief if brief is not None else old.get("brief")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(digest, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return digest


def run(lanes: list[str] | None, brief: bool, render: bool) -> int:
    date = engine.utc_today()
    chosen = [l for l in LANES if not lanes or l.name in lanes]
    if lanes and len(chosen) != len(lanes):
        print(f"unknown lane in {lanes}; have {[l.name for l in LANES]}", file=sys.stderr)
        return 2
    results = []
    for lane in chosen:
        r = engine.run_lane(lane, date)
        status = "ok" if r.ok else f"FAILED: {r.error}"
        print(f"{lane.name:12} {len(r.today):>7} entities  {len(r.events):>5} events  {status}", file=sys.stderr)
        results.append(r)
    engine.write_events(date, results)
    health = engine.write_health(date, results)
    signals = engine.collect_signals(results)
    text = None
    if brief:
        from tideline import brief as briefmod
        text = briefmod.write(date, signals)
    write_digest(date, results, health, signals, text)
    print(f"{len(signals)} signals; brief {'written' if text else 'skipped'}", file=sys.stderr)
    if render:
        from tideline import site
        print(f"site -> {site.render()}", file=sys.stderr)
    # Exit non-zero only if every lane failed: one dead source shouldn't block the day's commit.
    return 0 if any(r.ok for r in results) else 1


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="tideline")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--lanes", help="comma-separated lane names (default: all)")
    r.add_argument("--brief", action="store_true", help="write an analyst brief with headless Claude Code")
    r.add_argument("--no-site", action="store_true")
    sub.add_parser("site")
    a = p.parse_args(argv)
    if a.cmd == "run":
        return run(a.lanes.split(",") if a.lanes else None, a.brief, not a.no_site)
    from tideline import site
    print(f"site -> {site.render()}", file=sys.stderr)
    return 0


def cli() -> None:
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
