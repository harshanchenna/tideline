"""Render the static site from digests/, events/ and snapshots/. Pure stdlib; output goes to site/.

Pages: today's front page (lane tiles, brief, ranked signals), one page per lane (entity-count
trend, recent signals, the raw change log), one page per day, an archive, an about page, an RSS
feed and JSON exports. Every chart is one series in one hue (the validated blue ramp), with a
hover tooltip and a table alongside, so nothing rests on color alone.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import shutil
from pathlib import Path

from tideline import engine
from tideline.lanes import LANES

OUT = engine.ROOT / "site"
DIGESTS = engine.ROOT / "digests"
SITE_URL = "https://harshanchenna.github.io/tideline"
REPO_URL = "https://github.com/harshanchenna/tideline"
TITLE = "Tideline"
TAGLINE = "A git log of the AI industry. Public sources, diffed daily."
LANE_BY_NAME = {l.name: l for l in LANES}
RECENT_DAYS = 14
EVENT_ROWS = 300

KIND_LABEL = {"new_models": "New models", "price_change": "Price change", "context_change": "Context window",
              "delisted": "Delisted", "mcp_volume": "MCP volume", "official_server": "Official server",
              "server_release": "Server release", "verified_publishers": "Publishers", "mcp_retired": "Retired", "release": "Release",
              "momentum": "Momentum", "incident": "Incident", "reliability_week": "Reliability",
              "new_team": "New team", "theme_push": "Leaning in", "surge": "Staffing surge",
              "leadership": "Leadership roles", "pullback": "Pullback"}

CSS = """
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f3f2ef;--rule:#e2e1dc;--text-primary:#0b0b0b;
--text-secondary:#52514e;--text-muted:#77766f;--accent:#2a78d6;--accent-weak:#9ec5f4;--accent-strong:#1c5cab;--link:#1c5cab;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface-1:#1a1a19;
--surface-2:#232321;--rule:#383835;--text-primary:#fff;--text-secondary:#c3c2b7;--text-muted:#9a998f;--accent:#3987e5;
--accent-weak:#256abf;--accent-strong:#86b6ef;--link:#86b6ef}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#232321;--rule:#383835;--text-primary:#fff;
--text-secondary:#c3c2b7;--text-muted:#9a998f;--accent:#3987e5;--accent-weak:#256abf;--accent-strong:#86b6ef;--link:#86b6ef}
*{box-sizing:border-box}body{margin:0}
.viz-root{background:var(--surface-1);color:var(--text-primary);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100vh}
main{max-width:1080px;margin:0 auto;padding:28px 20px 64px}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
header.top{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;border-bottom:1px solid var(--rule);padding-bottom:14px}
header.top h1{margin:0;font:600 24px/1.2 var(--mono);letter-spacing:-.02em}header.top h1 a{color:var(--text-primary)}
header.top h1 .caret{color:var(--accent)}
header.top p{margin:2px 0 0;color:var(--text-secondary)}nav a{margin-left:16px;color:var(--text-secondary)}
h2{font-size:18px;margin:36px 0 4px}h2+.sub{color:var(--text-secondary);margin:0 0 14px;font-size:14px}
.date{font-family:var(--mono);color:var(--text-muted);font-size:13px;margin-top:18px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:14px 0 6px}
.tile{background:var(--surface-2);border-radius:10px;padding:14px 16px;display:block;color:inherit}
.tile:hover{text-decoration:none;outline:1px solid var(--accent-weak)}
.tile .label{color:var(--text-secondary);font-size:13px}
.tile .value{font:600 26px/1.3 var(--mono)}.tile .note{color:var(--text-muted);font-size:12px}
.tile .note.bad{color:var(--text-primary)}
.brief{background:var(--surface-2);border-left:3px solid var(--accent);border-radius:6px;padding:14px 18px;margin:18px 0}
.brief p{margin:.4em 0}
.signals{display:grid;gap:10px}
.sig{border:1px solid var(--rule);border-radius:10px;padding:12px 16px}
.sig .meta{font-size:12px;color:var(--text-secondary);display:flex;gap:10px;flex-wrap:wrap;font-family:var(--mono)}
.sig .kind{font-weight:600;color:var(--accent-strong)}
.sig h3{font-size:16px;margin:4px 0}.sig p{margin:2px 0 6px;color:var(--text-secondary);font-size:14px}
.sig details summary{cursor:pointer;color:var(--text-muted);font-size:13px}
.sig ul{margin:6px 0 0;padding-left:18px;font-size:13px}.sig li{margin:2px 0}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 12px}
.filters input,.filters select{font:inherit;padding:6px 10px;border:1px solid var(--rule);border-radius:8px;background:var(--surface-1);color:var(--text-primary)}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:6px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
th{color:var(--text-secondary);font-weight:500;font-size:13px;white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.op{font-family:var(--mono);font-size:12px;white-space:nowrap}td.mono{font-family:var(--mono);font-size:12px}
.muted{color:var(--text-muted)}.small{font-size:13px}
svg.chart path{fill:none;stroke:var(--accent);stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round}
svg.chart rect.hit{fill:transparent}svg.chart circle{fill:var(--accent-strong);stroke:var(--surface-1);stroke-width:2;opacity:0}
svg.chart g:hover circle{opacity:1}
svg.chart line{stroke:var(--rule)}svg.chart text{fill:var(--text-muted);font:11px var(--mono)}
#tip{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);font-size:12px;padding:5px 8px;border-radius:6px;display:none;z-index:9;max-width:300px}
footer{margin-top:48px;border-top:1px solid var(--rule);padding-top:14px;color:var(--text-muted);font-size:13px}
.days{display:grid;gap:4px}.days a{font-family:var(--mono)}
"""

JS = """
const tip=document.getElementById('tip');
document.addEventListener('mousemove',e=>{const t=e.target.closest('[data-tip]');
 if(!t){tip.style.display='none';return}tip.textContent=t.dataset.tip;tip.style.display='block';
 tip.style.left=Math.min(e.clientX+12,innerWidth-310)+'px';tip.style.top=(e.clientY+14)+'px'});
const q=document.getElementById('q'),ln=document.getElementById('lane');
function filt(){const s=(q?.value||'').toLowerCase(),l=ln?.value||'';
 document.querySelectorAll('[data-filter]').forEach(el=>{const ok=(!s||el.dataset.filter.toLowerCase().includes(s))&&(!l||el.dataset.lane===l);
 el.style.display=ok?'':'none'})}
q?.addEventListener('input',filt);ln?.addEventListener('change',filt);
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def safe_url(u) -> str:
    """Only http(s) links reach an href; third-party data can't smuggle a javascript: URL in."""
    u = str(u or "").strip()
    return esc(u) if re.match(r"https?://", u, re.I) else ""


def link(label, url) -> str:
    u = safe_url(url)
    return f'<a href="{u}" rel="nofollow noopener">{esc(label)}</a>' if u else esc(label)


def compact(n: int) -> str:
    return f"{n / 1000:.1f}K" if n >= 10_000 else f"{n:,}"


def page(title: str, body: str, depth: int = 0, desc: str = TAGLINE) -> str:
    up = "../" * depth
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ctext y='13' font-size='15' font-family='monospace' fill='%232a78d6'%3E~%3C/text%3E%3C/svg%3E">
<link rel="alternate" type="application/rss+xml" title="{TITLE}" href="{up}feed.xml">
<style>{CSS}</style></head><body class="viz-root"><main>
<header class="top"><div><h1><a href="{up}index.html"><span class="caret">~</span>tideline</a></h1><p>{esc(TAGLINE)}</p></div>
<nav><a href="{up}index.html">Today</a>{''.join(f'<a href="{up}lane/{l.name}.html">{esc(l.title)}</a>' for l in LANES)}
<a href="{up}archive.html">Archive</a><a href="{up}about.html">About</a><a href="{up}feed.xml">RSS</a></nav></header>
{body}
<footer>Rebuilt daily from public APIs. Every change is a commit: the history is the dataset.
<a href="{up}about.html">Method and caveats</a> · <a href="{REPO_URL}">Source (MIT)</a> ·
<a href="{up}data/latest.json">JSON</a></footer>
</main><div id="tip" role="tooltip"></div><script>{JS}</script></body></html>"""


def chart(series: list[tuple[str, int]], unit: str, w: int = 1000, h: int = 120, slots: int = 90) -> str:
    """Daily counts as one 2px line in fixed slots (newest at the right). A line, not bars,
    because the y-range is zoomed to the data: min and max are labeled so the scale is explicit.
    Each day has a full-height hover target with a tooltip; the table view carries exact values."""
    if len(series) < 2:
        return '<p class="muted small">The trend starts once there are two days of data.</p>'
    series = series[-slots:]
    vals = [v for _, v in series]
    lo, hi = min(vals), max(vals)
    pad, top, bot = (hi - lo) * 0.08 or 1, 16, h - 16
    y = lambda v: bot - (v - lo + pad) / (hi - lo + 2 * pad) * (bot - top)
    step = w / (slots - 1)
    x0 = (slots - len(series)) * step
    pts = [(x0 + i * step, y(v)) for i, v in enumerate(vals)]
    path = "M" + " L".join(f"{x:.1f},{yy:.1f}" for x, yy in pts)
    hits = "".join(f'<g data-tip="{esc(d)}: {v:,} {esc(unit)}"><rect class="hit" x="{x - step / 2:.1f}" y="0" '
                   f'width="{step:.1f}" height="{h}"/><circle cx="{x:.1f}" cy="{yy:.1f}" r="4"/></g>'
                   for (d, v), (x, yy) in zip(series, pts))
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" width="100%" role="img" '
            f'aria-label="{esc(unit)} per day, {esc(series[0][0])} to {esc(series[-1][0])}, from {lo:,} to {hi:,}">'
            f'<line x1="0" x2="{w}" y1="{y(hi):.1f}" y2="{y(hi):.1f}"/><line x1="0" x2="{w}" y1="{y(lo):.1f}" y2="{y(lo):.1f}"/>'
            f'<text x="0" y="{y(hi) - 4:.1f}">{hi:,}</text><text x="0" y="{y(lo) + 13:.1f}">{lo:,}</text>'
            f'<path d="{path}"/>{hits}</svg>')


def counts_table(series: list[tuple[str, int]], unit: str) -> str:
    rows = "".join(f'<tr><td class="mono">{esc(d)}</td><td class="num">{v:,}</td></tr>' for d, v in reversed(series[-60:]))
    return (f'<details><summary class="small muted">Table view</summary><table><thead><tr><th>Date</th>'
            f'<th class="num">{esc(unit)}</th></tr></thead><tbody>{rows}</tbody></table></details>')


def sig_card(s: dict, depth: int, show_date: bool = False) -> str:
    lane = LANE_BY_NAME.get(s["lane"])
    items = s.get("items") or []
    lis = "".join(f"<li>{link(i['label'], i.get('url'))}"
                  + (f' <span class="muted">· {esc(i["note"])}</span>' if i.get("note") else "") + "</li>"
                  for i in items[:25])
    more = f'<li class="muted">and {len(items) - 25} more</li>' if len(items) > 25 else ""
    det = f"<details><summary>{len(items)} item{'s' if len(items) != 1 else ''}</summary><ul>{lis}{more}</ul></details>" if items else ""
    up = "../" * depth
    meta = [f'<span class="kind">{esc(KIND_LABEL.get(s["kind"], s["kind"].replace("_", " ").capitalize()))}</span>',
            f'<a href="{up}lane/{esc(s["lane"])}.html">{esc(lane.title if lane else s["lane"])}</a>']
    if show_date:
        meta.append(f'<a href="{up}day/{esc(s["date"])}.html">{esc(s["date"])}</a>')
    filt = " ".join(str(x or "") for x in (s["title"], s.get("detail"), s.get("org"), s["kind"]))
    return (f'<article class="sig" data-filter="{esc(filt)}" data-lane="{esc(s["lane"])}"><div class="meta">{"".join(meta)}</div>'
            f'<h3>{esc(s["title"])}</h3>' + (f'<p>{esc(s["detail"])}</p>' if s.get("detail") else "") + det + "</article>")


def load_digests() -> list[dict]:
    out = []
    for p in sorted(DIGESTS.glob("*/*/*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def load_events(date: str) -> list[dict]:
    y, m, d = date.split("-")
    p = engine.EVENTS / y / m / f"{d}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def brief_html(paras) -> str:
    if not paras:
        return ""
    return ('<section class="brief">' + "".join(f"<p>{esc(p)}</p>" for p in paras)
            + '<p class="small muted">Written by Claude from the day\'s signals. Check the items before relying on it.</p></section>')


def health_note(h: dict | None, today: str) -> tuple[str, str]:
    if not h:
        return "not run yet", "bad"
    if not h.get("ok"):
        return f"fetch failed; showing {h.get('last_ok') or 'no'} data", "bad"
    if h.get("bootstrap"):
        return "first snapshot (no diff yet)", ""
    if h.get("date") != today:
        return f"last run {h.get('date')}", "bad"
    return f"{h.get('events', 0):,} changes today", ""


def front(latest: dict | None, hist: dict, recent: list[dict]) -> str:
    if not latest:
        return page(TITLE, '<p class="muted">No runs yet.</p>')
    date = latest["date"]
    tiles = []
    for l in LANES:
        series = hist.get(l.name, [])
        note, cls = health_note(latest["health"].get(l.name), date)
        val = compact(series[-1][1]) if series else "—"
        tiles.append(f'<a class="tile" href="lane/{l.name}.html"><div class="label">{esc(l.title)}</div>'
                     f'<div class="value">{val}</div><div class="note {cls}">{esc(note)}</div></a>')
    sigs = latest["signals"]
    opts = "".join(f'<option value="{l.name}">{esc(l.title)}</option>' for l in LANES)
    week = [s for d in recent[-7:-1] for s in d["signals"][:5]]
    body = (f'<div class="date">{esc(date)} · {len(sigs)} signals</div><div class="tiles">{"".join(tiles)}</div>'
            + brief_html(latest.get("brief"))
            + f'<h2>Today</h2><p class="sub">Ranked across every source. Each card links to the underlying items.</p>'
            f'<div class="filters"><input id="q" placeholder="Filter: company, model, kind" aria-label="Filter signals">'
            f'<select id="lane" aria-label="Lane"><option value="">All lanes</option>{opts}</select></div>'
            f'<div class="signals">{"".join(sig_card(s, 0) for s in sigs[:60]) or "<p class=muted>Quiet day.</p>"}</div>')
    if week:
        body += (f'<h2>Earlier this week</h2><p class="sub">The top few from each of the previous days.</p>'
                 f'<div class="signals">{"".join(sig_card(s, 0, True) for s in week)}</div>')
    return page(f"{TITLE}: {date}", body)


def lane_page(l, hist: dict, recent: list[dict]) -> str:
    series = hist.get(l.name, [])[-90:]
    unit = "entities"
    sigs = [s for d in reversed(recent) for s in d["signals"] if s["lane"] == l.name][:80]
    rows, total = [], 0
    for d in reversed(recent[-7:]):
        evs = [e for e in load_events(d["date"]) if e["lane"] == l.name]
        total += len(evs)
        for e in evs:
            if len(rows) >= EVENT_ROWS:
                break
            ent = e.get("entity") or {}
            try:
                label, url = l.label(ent), l.url(ent)
            except (KeyError, TypeError):
                label, url = e["id"], None
            what = f'{esc(e["field"])}: {esc(e.get("old"))} → {esc(e.get("new"))}' if e["op"] == "changed" else ""
            rows.append(f'<tr><td class="mono">{esc(e["date"])}</td><td class="op">{esc(e["op"])}</td>'
                        f'<td>{link(label, url)}</td><td class="small">{what}</td></tr>')
    log = (f'<table><thead><tr><th>Date</th><th>Op</th><th>Entity</th><th>Change</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
           + (f'<p class="small muted">Showing {len(rows):,} of {total:,}. The full log is in '
              f'<a href="{REPO_URL}/tree/main/events">events/</a>.</p>' if total > len(rows) else "")) if rows else '<p class="muted">No changes recorded in the last week.</p>'
    body = (f'<h2>{esc(l.title)}</h2><p class="sub">{esc(l.blurb)}</p>'
            f'<h2>Tracked {unit}</h2><p class="sub">Count per day, last {len(series)} day{"s" if len(series) != 1 else ""}.</p>{chart(series, unit)}{counts_table(series, unit)}'
            f'<h2>Signals</h2><p class="sub">Last {RECENT_DAYS} days, newest first.</p>'
            f'<div class="signals">{"".join(sig_card(s, 1, True) for s in sigs) or "<p class=muted>None yet.</p>"}</div>'
            f'<h2>Change log</h2><p class="sub">Raw diffs from the last 7 days: added, removed, changed.</p>{log}')
    return page(f"{l.title} · {TITLE}", body, depth=1, desc=l.blurb)


def day_page(d: dict) -> str:
    body = (f'<div class="date">{esc(d["date"])} · {len(d["signals"])} signals</div>' + brief_html(d.get("brief"))
            + f'<div class="signals">{"".join(sig_card(s, 1) for s in d["signals"]) or "<p class=muted>Quiet day.</p>"}</div>')
    return page(f"{d['date']} · {TITLE}", body, depth=1)


def archive(digests: list[dict]) -> str:
    rows = "".join(f'<div><a href="day/{esc(d["date"])}.html">{esc(d["date"])}</a> <span class="muted small">· '
                   f'{len(d["signals"])} signals' + (f' · {esc(d["signals"][0]["title"])}' if d["signals"] else "") + "</span></div>"
                   for d in reversed(digests))
    return page(f"Archive · {TITLE}", f'<h2>Archive</h2><p class="sub">Every day Tideline has run.</p><div class="days">{rows}</div>')


ABOUT = """<h2>What this is</h2>
<p>Tideline watches public sources that describe the AI industry and keeps a daily snapshot of each in git.
Every morning it diffs today against yesterday, turns the changes into ranked signals, and publishes this site.
Nothing here is scraped from behind a login, and the full history is in the repository: <code>git log</code> is the dataset.</p>
<h2>Lanes</h2>
<table><thead><tr><th>Lane</th><th>Source</th><th>What it surfaces</th></tr></thead><tbody>
<tr><td>Models</td><td>OpenRouter's public model catalog</td><td>New models, delistings, price and context-window changes.</td></tr>
<tr><td>MCP</td><td>The official Model Context Protocol registry</td><td>New servers, launches from domain-verified publishers, releases, retirements.</td></tr>
<tr><td>Packages</td><td>PyPI, npm, and their public download stats</td><td>Releases of the SDKs and frameworks AI is built on, and unusual download momentum.</td></tr>
<tr><td>Reliability</td><td>Providers' public status pages</td><td>Incidents, how long they lasted, and each provider's week.</td></tr>
<tr><td>Teams</td><td>AI companies' public job boards (Greenhouse, Lever, Ashby)</td><td>What companies are staffing up for: new teams, pushes into a theme, leadership roles. Aggregates only.</td></tr>
</tbody></table>
<h2>How it works</h2>
<p>Each lane is a function that returns today's entities. The engine stores them as sorted JSON lines in
<code>snapshots/</code>, compares them with the snapshot from the last commit before today (UTC), and writes
what was added, removed or changed to <code>events/</code>. Lanes then read their own diff and emit signals with a score.
The day's signals, lane health and the optional brief go to <code>digests/</code>, which this site is built from.</p>
<h2>Caveats</h2>
<ul>
<li>A source that fails, or returns less than half of what it did yesterday, is treated as an outage: its previous snapshot is kept, so an outage never shows up as mass removals.</li>
<li>A lane's first run records a baseline and emits no diff. Its first signals use the source's own dates where it has them.</li>
<li>The MCP registry is read incrementally (servers updated in the last two days) with a full re-read every Sunday.</li>
<li>Job postings are evidence of direction, not proof. The Teams lane stores no individual posting, pay range or link, only company-level counts and notes.</li>
<li>The daily brief is written by Claude with no tools and no access beyond the day's signals; it can be wrong. The items are the source of truth.</li>
</ul>"""


def feed(digests: list[dict]) -> str:
    items = []
    for d in reversed(digests[-RECENT_DAYS:]):
        for s in d["signals"][:10]:
            guid = f"{SITE_URL}/day/{d['date']}.html#{hashlib.sha1((s['lane'] + s['kind'] + s['title']).encode()).hexdigest()[:12]}"
            pub = dt.datetime.fromisoformat(d["date"]).replace(tzinfo=dt.timezone.utc).strftime("%a, %d %b %Y 06:00:00 +0000")
            items.append(f"<item><title>{esc(s['title'])}</title><link>{SITE_URL}/day/{esc(d['date'])}.html</link>"
                         f"<guid isPermaLink=\"false\">{esc(guid)}</guid><pubDate>{pub}</pubDate>"
                         f"<description>{esc(s.get('detail') or '')}</description></item>")
    return (f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>{TITLE}</title>'
            f"<link>{SITE_URL}/</link><description>{esc(TAGLINE)}</description>{''.join(items)}</channel></rss>")


def render(out: Path | None = None) -> Path:
    out = out or OUT
    if out.exists():
        shutil.rmtree(out)
    for sub in ("lane", "day", "data"):
        (out / sub).mkdir(parents=True)
    digests = load_digests()
    hist = engine.counts_history()
    recent = digests[-RECENT_DAYS:]
    latest = digests[-1] if digests else None
    (out / "index.html").write_text(front(latest, hist, recent))
    for l in LANES:
        (out / "lane" / f"{l.name}.html").write_text(lane_page(l, hist, recent))
    for d in digests:
        (out / "day" / f"{d['date']}.html").write_text(day_page(d))
    (out / "archive.html").write_text(archive(digests))
    (out / "about.html").write_text(page(f"About · {TITLE}", ABOUT))
    (out / "feed.xml").write_text(feed(digests))
    (out / "data" / "latest.json").write_text(json.dumps(latest or {}, indent=1, ensure_ascii=False))
    (out / ".nojekyll").write_text("")
    return out
