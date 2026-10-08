"""Models lane: every model on OpenRouter's public catalog, with context window and token prices."""
from __future__ import annotations

import datetime as dt

from tideline.engine import Lane, LaneResult
from tideline.http import get_json
from tideline.lanes.common import by, days_ago, item, plural, sig

ORG_NAMES = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google", "meta-llama": "Meta",
             "mistralai": "Mistral", "x-ai": "xAI", "deepseek": "DeepSeek", "qwen": "Qwen", "cohere": "Cohere",
             "amazon": "Amazon", "microsoft": "Microsoft", "nvidia": "NVIDIA", "moonshotai": "Moonshot AI",
             "z-ai": "Zhipu (Z.ai)", "minimax": "MiniMax", "perplexity": "Perplexity", "ai21": "AI21"}


def per_million(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v * 1_000_000, 4) if v >= 0 else None


def fetch() -> list[dict]:
    d = get_json("https://openrouter.ai/api/v1/models")
    if not d or "data" not in d:
        raise RuntimeError("OpenRouter catalog unavailable")
    out = []
    for m in d["data"]:
        p = m.get("pricing") or {}
        org = m["id"].split("/")[0]
        out.append({
            "id": m["id"], "name": m.get("name", m["id"]), "org": org,
            "created": dt.datetime.fromtimestamp(m["created"], dt.timezone.utc).date().isoformat() if m.get("created") else None,
            "context": m.get("context_length"),
            "max_output": (m.get("top_provider") or {}).get("max_completion_tokens"),
            "modality": (m.get("architecture") or {}).get("modality"),
            "price_in": per_million(p.get("prompt")), "price_out": per_million(p.get("completion")),
            "url": f"https://openrouter.ai/{m['id']}",
        })
    return out


def is_alias(e: dict) -> bool:
    return "latest" in e["id"].lower() or "latest" in (e.get("name") or "").lower()


def org_name(org: str) -> str:
    return ORG_NAMES.get(org, org)


def money(v) -> str:
    return "free" if v == 0 else f"${v:g}" if v is not None else "?"


def ctx(v) -> str:
    return f"{v // 1000}K" if v and v >= 1000 else str(v or "?")


def describe(e: dict) -> str:
    return f"{ctx(e.get('context'))} ctx · {money(e.get('price_in'))}/{money(e.get('price_out'))} per M tokens"


def signals(r: LaneResult) -> list[dict]:
    out = []
    if r.base is None:  # bootstrap: use the catalog's own creation dates
        added = [e for e in r.today.values() if (e.get("created") or "") >= days_ago(r.date, 7)]
        window = "in the last 7 days"
    else:
        added = [e["entity"] for e in r.events if e["op"] == "added"]
        window = "today"
    for org, ms in sorted(by(added, lambda e: e["org"]).items(), key=lambda kv: -len(kv[1])):
        ms = [m for m in ms if not m["id"].endswith(":free")] or ms
        out.append(sig("new_models", f"{org_name(org)} listed {plural(len(ms), 'new model')} {window}",
                       "; ".join(f"{m['name']} ({describe(m)})" for m in ms[:3]), org,
                       [item(m["name"], m["url"], describe(m)) for m in ms], 4 + 2 * len(ms)))
    for ev in r.events:
        if ev["op"] != "changed" or ev["field"] not in ("price_in", "price_out", "context"):
            continue
        e, old, new = ev["entity"], ev["old"], ev["new"]
        if is_alias(e):  # "...-latest" ids get repointed to another model; that's not a price move
            continue
        if ev["field"] == "context":
            if old and new:
                out.append(sig("context_change", f"{e['name']} context window {ctx(old)} → {ctx(new)}", "",
                               e["org"], [item(e["name"], e["url"])], 4 if new > old else 3))
            continue
        if old and new is not None and old > 0:
            pct = (new - old) / old * 100
            if abs(pct) < 5:
                continue
            side = "input" if ev["field"] == "price_in" else "output"
            verb = "cut" if pct < 0 else "raised"
            out.append(sig("price_change", f"{e['name']}: {side} price {verb} {abs(pct):.0f}% ({money(old)} → {money(new)} per M tokens)",
                           "", e["org"], [item(e["name"], e["url"], describe(e))], 5 + min(abs(pct), 90) / 10))
    removed = [e["entity"] for e in r.events if e["op"] == "removed"]
    for org, ms in by(removed, lambda e: e["org"]).items():
        out.append(sig("delisted", f"{plural(len(ms), 'model')} from {org_name(org)} left the catalog",
                       ", ".join(m["name"] for m in ms[:5]), org, [item(m["name"], None) for m in ms], 2 + len(ms)))
    return out


LANE = Lane(name="models", title="Models", blurb="Every model on OpenRouter's public catalog: launches, delistings, prices, context windows.",
            fetch=fetch, signals=signals, label=lambda e: e["name"])
