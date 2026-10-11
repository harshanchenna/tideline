"""Offline tests: engine diff and guards, digest merging, the MCP incremental merge, lane signals,
the brief's fallbacks, and the site's escaping. No network; every source is a stub."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tideline import __main__ as cli
from tideline import brief, engine, site
from tideline.engine import Lane, LaneResult
from tideline.lanes import mcp, models, teams


def lane(rows=None, exc=None, **kw) -> Lane:
    def fetch():
        if exc:
            raise exc
        return [dict(r) for r in rows]
    return Lane(name="t", title="Test", blurb="b", fetch=fetch, signals=lambda r: [], **kw)


class TempRoot(unittest.TestCase):
    """Point every on-disk path at a temp dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [mock.patch.object(engine, "SNAP", root / "snapshots"),
                        mock.patch.object(engine, "EVENTS", root / "events"),
                        mock.patch.object(cli, "DIGESTS", root / "digests"),
                        mock.patch.object(site, "DIGESTS", root / "digests")]
        for p in self.patches:
            p.start()
        self.root = root

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


class DiffTest(unittest.TestCase):
    def test_added_removed_changed(self):
        l = lane([], volatile={"downloads"})
        base = {"a": {"id": "a", "v": 1, "downloads": 5}, "b": {"id": "b", "v": 1}}
        today = {"a": {"id": "a", "v": 2, "downloads": 9}, "c": {"id": "c", "v": 1}}
        ev = engine.diff(l, base, today, "2026-10-08")
        ops = sorted((e["op"], e["id"], e.get("field")) for e in ev)
        self.assertEqual(ops, [("added", "c", None), ("changed", "a", "v"), ("removed", "b", None)])

    def test_rolling_window_lane_does_not_report_removals(self):
        l = lane([], track_removed=False)
        ev = engine.diff(l, {"a": {"id": "a"}}, {"b": {"id": "b"}}, "2026-10-08")
        self.assertEqual([e["op"] for e in ev], ["added"])


class RunLaneTest(TempRoot):
    def test_bootstrap_writes_snapshot_and_no_events(self):
        with mock.patch.object(engine, "load_base", return_value=None):
            r = engine.run_lane(lane([{"id": 1}, {"id": 2}]), "2026-10-08")
        self.assertTrue(r.ok)
        self.assertEqual(r.events, [])
        self.assertEqual(set(engine.load_current("t")), {"1", "2"})

    def test_shrink_guard_keeps_previous_snapshot(self):
        base = {str(i): {"id": str(i)} for i in range(10)}
        engine.write_snapshot("t", base)
        with mock.patch.object(engine, "load_base", return_value=base):
            r = engine.run_lane(lane([{"id": "1"}]), "2026-10-08")
        self.assertFalse(r.ok)
        self.assertIn("failed fetch", r.error)
        self.assertEqual(len(engine.load_current("t")), 10)

    def test_fetch_exception_is_contained(self):
        with mock.patch.object(engine, "load_base", return_value=None):
            r = engine.run_lane(lane(exc=ValueError("boom")), "2026-10-08")
        self.assertFalse(r.ok)
        self.assertIn("boom", r.error)

    def test_health_keeps_lanes_not_run(self):
        ok = LaneResult(lane([]), {"a": {}}, None, [], True, None, "2026-10-08")
        engine.write_health("2026-10-08", [ok])
        other = Lane(name="u", title="U", blurb="", fetch=list, signals=lambda r: [])
        h = engine.write_health("2026-10-08", [LaneResult(other, {}, None, [], False, "x", "2026-10-08")])
        self.assertEqual(set(h), {"t", "u"})


class DigestTest(TempRoot):
    def test_rerun_keeps_signals_of_failed_and_unselected_lanes(self):
        a, b, c = (Lane(name=n, title=n, blurb="", fetch=list, signals=lambda r: []) for n in "abc")
        s = lambda ln, t: {"lane": ln, "kind": "k", "title": t, "score": 1}
        d = "2026-10-08"
        first = [LaneResult(x, {}, None, [], True, None, d) for x in (a, b, c)]
        cli.write_digest(d, first, {}, [s("a", "a1"), s("b", "b1"), s("c", "c1")], ["brief"])
        rerun = [LaneResult(a, {}, None, [], True, None, d), LaneResult(b, {}, None, [], False, "x", d)]
        out = cli.write_digest(d, rerun, {}, [s("a", "a2")], None)
        self.assertEqual(sorted(x["title"] for x in out["signals"]), ["a2", "b1", "c1"])
        self.assertEqual(out["brief"], ["brief"])


class McpIncrementalTest(unittest.TestCase):
    def test_merges_updates_onto_base_and_drops_deleted(self):
        base = {"a/x": {"id": "a/x", "status": "active"}, "b/y": {"id": "b/y", "status": "active"}}
        upd = [{"id": "b/y", "status": "deleted"}, {"id": "c/z", "status": "active"}]
        with mock.patch.object(mcp, "load_current", return_value=base), \
             mock.patch.object(mcp, "_crawl", return_value=upd) as crawl, \
             mock.patch.object(mcp, "FULL_CRAWL_WEEKDAY", 9):
            out = mcp.fetch()
        self.assertEqual(sorted(e["id"] for e in out), ["a/x", "c/z"])
        self.assertIn("updated_since=", crawl.call_args[0][0])

    def test_full_crawl_without_base(self):
        with mock.patch.object(mcp, "load_current", return_value=None), \
             mock.patch.object(mcp, "_crawl", return_value=[{"id": "a", "status": "active"}]) as crawl:
            self.assertEqual(len(mcp.fetch()), 1)
        crawl.assert_called_once_with("")


class ModelSignalsTest(unittest.TestCase):
    def test_price_cut_is_a_signal_and_small_moves_are_not(self):
        e = {"id": "o/m", "name": "M", "org": "openai", "context": 128000, "price_in": 1.0, "price_out": 4.0,
             "url": "https://openrouter.ai/o/m"}
        ev = [{"op": "changed", "field": "price_in", "old": 2.0, "new": 1.0, "entity": e},
              {"op": "changed", "field": "price_out", "old": 4.1, "new": 4.0, "entity": e}]
        r = LaneResult(models.LANE, {"o/m": e}, {"o/m": e}, ev, True, None, "2026-10-08")
        sigs = models.signals(r)
        self.assertEqual([s["kind"] for s in sigs], ["price_change"])
        self.assertIn("cut 50%", sigs[0]["title"])

    def test_price_moves_merge_per_model_drop_sub_cent_and_cap(self):
        def ent(i):
            return {"id": f"o/m{i}", "name": f"M{i}", "org": "o", "context": 1000, "price_in": 1.0, "price_out": 1.0,
                    "url": "https://openrouter.ai/o/m"}
        ev = [{"op": "changed", "field": "price_in", "old": 2.0, "new": 1.0, "entity": ent(0)},
              {"op": "changed", "field": "price_out", "old": 4.0, "new": 2.0, "entity": ent(0)},
              {"op": "changed", "field": "price_in", "old": 0.01, "new": 0.02, "entity": ent(1)}]  # +100% but $0.01
        ev += [{"op": "changed", "field": "price_in", "old": 1.0, "new": 1.5, "entity": ent(i)} for i in range(2, 12)]
        sigs = models.signals(LaneResult(models.LANE, {}, {}, ev, True, None, "2026-10-09"))
        self.assertEqual(len(sigs), models.MAX_PRICE_SIGNALS + 1)
        self.assertIn("input cut 50%", sigs[0]["title"])
        self.assertIn("output cut 50%", sigs[0]["title"])
        self.assertNotIn("M1:", " ".join(s["title"] for s in sigs))
        self.assertEqual(sigs[-1]["title"], "3 more models changed price")


class TeamSignalsTest(unittest.TestCase):
    @staticmethod
    def co(name, notes):
        return {"id": name, "notes": notes}

    def test_persisting_notes_are_not_reported_again(self):
        surge = {"kind": "surge", "score": 10, "examples": [], "title": "A opened 20 roles"}
        team = {"kind": "new_team", "theme": "Voice", "score": 10, "examples": [], "title": "A is building a Voice team"}
        base = {"A": self.co("A", [surge])}
        today = {"A": self.co("A", [dict(surge, title="A opened 22 roles"), team])}
        sigs = teams.signals(LaneResult(teams.LANE, today, base, [], True, None, "2026-10-09"))
        self.assertEqual([s["kind"] for s in sigs], ["new_team"])

    def test_notes_from_before_theme_was_stored_still_match(self):
        old = {"kind": "theme_push", "score": 5, "examples": [], "title": "A is leaning into Voice: 40% of new roles vs 5% before"}
        new = dict(old, theme="Voice", title="A is leaning into Voice: 45% of new roles vs 5% before")
        other = dict(new, theme="Robotics", title="A is leaning into Robotics: 30% of new roles vs 2% before")
        sigs = teams.signals(LaneResult(teams.LANE, {"A": self.co("A", [new, other])}, {"A": self.co("A", [old])},
                                        [], True, None, "2026-10-09"))
        self.assertEqual([s["title"][:30] for s in sigs], [other["title"][:30]])

    def test_leadership_rolls_up_new_titles_only(self):
        lead = lambda *t: {"kind": "leadership", "score": 5, "examples": list(t), "title": "x"}
        base = {"A": self.co("A", [lead("Head of  Sales")]), "B": self.co("B", [])}
        today = {"A": self.co("A", [lead("Head of Sales", "VP Research")]), "B": self.co("B", [lead("Head of Legal")]),
                 "C": self.co("C", [lead("Head of Sales")])}
        sigs = teams.signals(LaneResult(teams.LANE, today, base, [], True, None, "2026-10-09"))
        self.assertEqual(len(sigs), 1)
        s = sigs[0]
        self.assertEqual((s["kind"], s["org"]), ("leadership", None))
        self.assertEqual(s["title"], "3 companies posted 3 new leadership roles")
        self.assertNotIn("Head of Sales;", s["detail"].split("C:")[0])  # A's existing role is not repeated
        self.assertIn("A: VP Research", s["detail"])
        self.assertLessEqual(s["score"], 4)


class BriefTest(unittest.TestCase):
    def test_no_claude_binary_means_no_brief(self):
        with mock.patch.object(brief.shutil, "which", return_value=None):
            self.assertIsNone(brief.write("2026-10-08", [{"lane": "m", "kind": "k", "title": "t"}]))

    def test_payload_is_bounded(self):
        sigs = [{"lane": "m", "kind": "k", "title": f"t{i}", "detail": "x" * 1000} for i in range(100)]
        data = json.loads(brief.payload("2026-10-08", sigs))
        self.assertEqual(len(data["signals"]), 25)
        self.assertEqual(len(data["signals"][0]["detail"]), 300)


class SiteTest(TempRoot):
    def test_render_escapes_third_party_text_and_urls(self):
        d = self.root / "digests" / "2026" / "10"
        d.mkdir(parents=True)
        evil = {"lane": "mcp", "kind": "official_server", "title": "<script>alert(1)</script>", "detail": "\"><img>",
                "org": None, "score": 9, "date": "2026-10-08",
                "items": [{"label": "x", "url": "javascript:alert(1)", "note": ""},
                          {"label": "ok", "url": "https://example.com/a?b=1&c=2", "note": ""}]}
        (d / "08.json").write_text(json.dumps({"date": "2026-10-08", "health": {}, "signals": [evil],
                                               "brief": ["<b>hi</b>"]}))
        out = site.render(self.root / "site")
        html = (out / "index.html").read_text()
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("<b>hi</b>", html)
        self.assertIn("https://example.com/a?b=1&amp;c=2", html)
        for p in ("about.html", "archive.html", "feed.xml", "day/2026-10-08.html", "lane/mcp.html", "data/latest.json"):
            self.assertTrue((out / p).exists(), p)

    def test_render_with_no_runs(self):
        out = site.render(self.root / "site")
        self.assertIn("No runs yet", (out / "index.html").read_text())


if __name__ == "__main__":
    unittest.main()
