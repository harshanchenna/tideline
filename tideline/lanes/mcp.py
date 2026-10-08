"""MCP lane: every server in the official Model Context Protocol registry (latest version of each).

Registry names are namespaced. `io.github.<user>/...` is GitHub-authenticated; a reverse-DNS
namespace like `com.stripe/...` means the publisher proved control of that domain, which is the
closest thing the ecosystem has to "official". Those are the launches worth surfacing.
"""
from __future__ import annotations

import datetime as dt
import urllib.parse
from collections import Counter

from tideline.engine import Lane, LaneResult, load_current
from tideline.http import get_json
from tideline.lanes.common import by, days_ago, item, plural, sig

API = "https://registry.modelcontextprotocol.io/v0/servers"
MAX_PAGES = 1000
FULL_CRAWL_WEEKDAY = 6  # Sunday: re-read everything to pick up anything incremental runs missed

# Domain-verified publishers we score up: big platforms and AI companies.
NOTABLE = {"com.anthropic", "com.openai", "com.google", "com.microsoft", "com.github", "com.stripe", "com.cloudflare",
           "com.vercel", "com.atlassian", "com.notion", "com.linear", "com.figma", "com.supabase", "com.mongodb",
           "com.datadoghq", "com.sentry", "io.sentry", "com.paypal", "com.shopify", "com.salesforce", "com.slack",
           "com.zapier", "com.hubspot", "com.twilio", "com.elastic", "com.snowflake", "com.databricks", "com.amazon",
           "com.aws", "com.huggingface", "co.huggingface", "ai.mistral", "com.perplexity", "com.cursor", "com.docker",
           "com.gitlab", "com.box", "com.dropbox", "com.asana", "com.monday", "com.canva", "com.netlify", "com.neon",
           "tech.neon", "com.pinecone", "io.pinecone", "com.redis", "com.postman", "com.jetbrains", "com.ibm",
           "com.oracle", "com.adobe", "com.plaid", "com.square", "com.squareup", "com.intercom", "com.wix", "com.webflow"}


def namespace(name: str) -> str:
    return name.split("/")[0]


def verified(ns: str) -> bool:
    return not ns.startswith("io.github.")


def fetch() -> list[dict]:
    """Full crawl on the first run and weekly; otherwise only servers updated since yesterday.

    The registry holds 40k+ servers (~400 pages, several minutes), so daily runs ask for
    `updated_since` and merge onto the last snapshot. A server marked deleted is dropped.
    """
    base = load_current("mcp")
    now = dt.datetime.now(dt.timezone.utc)
    if not base or now.weekday() == FULL_CRAWL_WEEKDAY:
        return [e for e in _crawl("") if e.get("status") != "deleted"]
    since = (now - dt.timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")
    merged = dict(base)
    for e in _crawl(f"&updated_since={since}"):
        merged[e["id"]] = e
    return [e for e in merged.values() if e.get("status") != "deleted"]


def _crawl(extra: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(MAX_PAGES):
        url = API + "?limit=100&version=latest" + extra + (f"&cursor={urllib.parse.quote(cursor, safe='')}" if cursor else "")
        d = get_json(url, timeout=60)
        if d is None:
            raise RuntimeError(f"registry page failed after {len(out)} servers")
        for s in d.get("servers", []):
            sv = s.get("server", {})
            meta = (s.get("_meta") or {}).get("io.modelcontextprotocol.registry/official", {})
            name = sv.get("name")
            if not name:
                continue
            pkgs = sorted({p.get("registryType") for p in sv.get("packages") or [] if p.get("registryType")})
            remotes = sorted({r.get("type") for r in sv.get("remotes") or [] if r.get("type")})
            repo = (sv.get("repository") or {}).get("url")
            out.append({
                "id": name, "namespace": namespace(name), "title": sv.get("title") or name.split("/")[-1],
                "description": (sv.get("description") or "")[:200], "version": sv.get("version"),
                "packages": pkgs, "remotes": remotes, "status": meta.get("status"),
                "published": (meta.get("publishedAt") or "")[:10] or None,
                "repo": repo,
                "url": repo or f"https://registry.modelcontextprotocol.io/v0/servers?search={urllib.parse.quote(name)}",
            })
        cursor = (d.get("metadata") or {}).get("nextCursor")
        if not cursor:
            break
    return out


def label(e: dict) -> str:
    return f"{e['title']} ({e['id']})"


def kind(e: dict) -> str:
    if e.get("remotes") and not e.get("packages"):
        return "hosted"
    if e.get("packages") and not e.get("remotes"):
        return "local"
    return "both" if e.get("packages") else "none"


def signals(r: LaneResult) -> list[dict]:
    out = []
    if r.base is None:
        added = [e for e in r.today.values() if (e.get("published") or "") >= days_ago(r.date, 7)]
        window = "in the last 7 days"
    else:
        added = [e["entity"] for e in r.events if e["op"] == "added"]
        window = "today"
    if added:
        mix = Counter(kind(e) for e in added)
        out.append(sig("mcp_volume", f"{plural(len(added), 'new MCP server')} {window}",
                       f"{mix['hosted']} hosted, {mix['local']} local packages, {mix['both']} both; "
                       f"{sum(verified(e['namespace']) for e in added)} from domain-verified publishers.",
                       None, [], 3 + min(len(added) / 100, 7)))
    # Only well-known publishers get their own card. Everyone else is rolled into one summary:
    # a domain-verified namespace is cheap, and bulk publishers push dozens of templated servers.
    ver = by([e for e in added if verified(e["namespace"])], lambda e: e["namespace"])
    for ns, es in sorted(((ns, es) for ns, es in ver.items() if ns in NOTABLE), key=lambda kv: -len(kv[1])):
        out.append(sig("official_server", f"{ns} published {plural(len(es), 'MCP server')} {window}: "
                       + ", ".join(e["title"] for e in es[:3]),
                       es[0]["description"], ns.split(".")[-1], [item(label(e), e["url"], e["description"]) for e in es],
                       9 + min(len(es), 6)))
    rest = sorted(((ns, es) for ns, es in ver.items() if ns not in NOTABLE), key=lambda kv: (-len(kv[1]), kv[0]))
    if rest:
        bulk = [ns for ns, es in rest if len(es) >= 10]
        out.append(sig("verified_publishers", f"{plural(len(rest), 'domain-verified publisher')} shipped MCP servers {window}",
                       ", ".join(f"{ns} ({len(es)})" for ns, es in rest[:8])
                       + (f". Bulk publishers (10+ servers): {', '.join(bulk[:5])}." if bulk else ""),
                       None, [item(f"{ns}: {', '.join(e['title'] for e in es[:3])}", es[0]["url"], plural(len(es), "server"))
                              for ns, es in rest], 3 + min(len(rest), 40) / 10))
    if r.base is not None:
        bumps = [e for e in r.events if e["op"] == "changed" and e["field"] == "version"]
        notable_bumps = [e for e in bumps if e["entity"]["namespace"] in NOTABLE]
        for e in notable_bumps:
            out.append(sig("server_release", f"{e['entity']['title']} ({e['entity']['namespace']}) {e['old']} → {e['new']}",
                           "", e["entity"]["namespace"].split(".")[-1], [item(label(e["entity"]), e["entity"]["url"])], 4))
        gone = [e["entity"] for e in r.events if e["op"] == "removed"]
        dep = [e["entity"] for e in r.events if e["op"] == "changed" and e["field"] == "status" and e["new"] != "active"]
        if gone or dep:
            out.append(sig("mcp_retired", f"{plural(len(gone) + len(dep), 'MCP server')} removed or deprecated",
                           ", ".join(e["id"] for e in (gone + dep)[:6]), None,
                           [item(label(e), e.get("url")) for e in gone + dep], 2 + (len(gone) + len(dep)) / 20))
    return out


LANE = Lane(name="mcp", title="MCP", blurb="Every server in the official MCP registry: launches, official publishers, releases, retirements.",
            fetch=fetch, signals=signals, label=label)
