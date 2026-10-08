"""Fetch and normalize public ATS boards (Greenhouse, Lever, Ashby). Shared by the teams lane.

Only fetching and normalizing lives here; the teams lane
reduces postings to company-level aggregates before anything is stored.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re

from tideline.http import get_json
from tideline.lanes import _classify as classify

US_HINTS = re.compile(
    r"\b(united states|usa|u\.s\.|us remote|remote[- ]us|san francisco|new york|nyc|seattle|"
    r"palo alto|mountain view|menlo park|redwood city|san jose|sunnyvale|santa clara|"
    r"boston|austin|chicago|denver|los angeles|washington,? d\.?c|atlanta|miami|"
    r"[A-Za-z .]+,\s?(CA|NY|WA|TX|MA|CO|IL|VA|GA|FL|OR|NJ|PA|NC|UT|AZ|MI|MN|OH|MD|DC))\b", re.I)
US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
    "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
    "nevada", "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming", "district of columbia",
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky", "la",
    "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}
COUNTRY_ALIASES = {"united states": "US", "usa": "US", "us": "US", "united kingdom": "UK", "gb": "UK", "uk": "UK"}
MONEY = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK])")


ISO2 = {"us": "US", "gb": "UK", "uk": "UK", "ca": "Canada", "de": "Germany", "fr": "France", "ie": "Ireland",
        "in": "India", "jp": "Japan", "sg": "Singapore", "au": "Australia", "nl": "Netherlands", "es": "Spain",
        "ch": "Switzerland", "se": "Sweden", "pl": "Poland", "br": "Brazil", "mx": "Mexico", "il": "Israel",
        "kr": "South Korea", "it": "Italy", "pt": "Portugal", "dk": "Denmark", "nz": "New Zealand",
        "ae": "United Arab Emirates", "ind": "India", "co": "Colombia", "ar": "Argentina", "cz": "Czechia", "ro": "Romania"}
CITIES = {"london": "UK", "england": "UK", "scotland": "UK", "wales": "UK", "toronto": "Canada", "vancouver": "Canada",
          "montreal": "Canada", "sydney": "Australia", "melbourne": "Australia", "sao paulo": "Brazil",
          "são paulo": "Brazil", "bangalore": "India", "bengaluru": "India", "hyderabad": "India", "pune": "India",
          "tokyo": "Japan", "paris": "France", "berlin": "Germany", "munich": "Germany", "dublin": "Ireland",
          "amsterdam": "Netherlands", "tel aviv": "Israel", "seoul": "South Korea", "zurich": "Switzerland",
          "stockholm": "Sweden", "madrid": "Spain", "barcelona": "Spain", "warsaw": "Poland", "mexico city": "Mexico"}
REGIONS = {"sea", "hybrid", "on-site", "onsite", "amer", "americas", "emea", "apac", "asia", "europe", "latam", "na", "north america", "global",
           "remote", "anywhere", "worldwide", "remote international", "international"}


def norm_country(tok: str | None) -> str | None:
    """'TX 78705' -> US, 'Remote - Ireland' -> Ireland, 'CH' -> Switzerland, 'EMEA' -> None."""
    if not tok:
        return None
    s = re.sub(r"\d+", "", tok.split(";")[0].split("|")[0])
    s = re.sub(r"^\s*remote\s*[-–:]?\s*|\(remote\)", "", s, flags=re.I).strip(" ,.-")
    low = s.lower()
    if not low or low in REGIONS:
        return None
    if low in US_STATES and low != "georgia":
        return "US"
    if low in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[low]
    if low in CITIES:
        return CITIES[low]
    if low in ISO2:
        return ISO2[low]
    if len(low) == 2:
        return ISO2.get(low, s.upper())
    return s.title() if s.islower() or s.isupper() else s


def _country(raw: str | None, location: str) -> str:
    c = norm_country(raw)
    if c:
        return c
    if US_HINTS.search(location or ""):
        return "US"
    first = (location or "").split(";")[0]
    if "," in first:
        c = norm_country(first.split(",")[-1])
        if c:
            return c
    return norm_country(first) or "Unknown"


def _money(s: str) -> int:
    s = s.replace(",", "").replace(" ", "")
    return int(float(s[:-1]) * 1000) if s[-1] in "kK" else int(s)


def comp_from_text(text: str) -> tuple[int | None, int | None]:
    """First plausible USD annual range in posting text, e.g. '$222,800 — $290,000'."""
    vals = [_money(m) for m in MONEY.findall(text or "")]
    vals = [v for v in vals if 30_000 <= v <= 3_000_000]
    if len(vals) >= 2 and vals[1] >= vals[0]:
        return vals[0], vals[1]
    return None, None


def _strip(htmltext: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(htmltext or "")))


def _date(s) -> str | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return dt.datetime.fromtimestamp(s / 1000, dt.timezone.utc).date().isoformat()
    return str(s)[:10]


def fetch_board(c: dict) -> list[dict] | None:
    ats, tok = c["ats"], c["token"]
    out = []
    if ats == "greenhouse":
        d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs?content=true", timeout=90)
        if not isinstance(d, dict) or "jobs" not in d:
            return None
        for j in d["jobs"]:
            offices = j.get("offices") or []
            office_loc = offices[0].get("location") if offices else None
            lo, hi = comp_from_text(_strip(j.get("content", "")))
            meta = {m.get("name"): m.get("value") for m in j.get("metadata") or [] if isinstance(m, dict)}
            loc = (j.get("location") or {}).get("name", "")
            out.append(dict(job_id=str(j["id"]), title=j.get("title", ""),
                            department=", ".join(x["name"] for x in j.get("departments") or []),
                            location=loc,
                            country=_country(office_loc.split(",")[-1] if office_loc else None, loc),
                            remote=int("remote" in (loc + str(meta.get("Location Type", ""))).lower()),
                            posted_at=_date(j.get("first_published") or j.get("updated_at")),
                            url=j.get("absolute_url"), comp_min=lo, comp_max=hi,
                            comp_currency="USD" if lo else None))
    elif ats == "ashby":
        d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{tok}?includeCompensation=true", timeout=90)
        if not isinstance(d, dict) or "jobs" not in d:
            return None
        for j in d["jobs"]:
            if j.get("isListed") is False:
                continue
            lo = hi = cur = None
            for comp in (j.get("compensation") or {}).get("summaryComponents") or []:
                if comp.get("compensationType") == "Salary" and comp.get("interval") == "1 YEAR":
                    lo, hi, cur = comp.get("minValue"), comp.get("maxValue"), comp.get("currencyCode")
            addr = ((j.get("address") or {}).get("postalAddress") or {})
            loc = j.get("location") or ""
            out.append(dict(job_id=j["id"], title=j.get("title", ""),
                            department=" / ".join(x for x in (j.get("department"), j.get("team")) if x),
                            location=loc, country=_country(addr.get("addressCountry"), loc),
                            remote=int(bool(j.get("isRemote")) or j.get("workplaceType") == "Remote"
                                       or "remote" in loc.lower()),
                            posted_at=_date(j.get("publishedAt")), url=j.get("jobUrl"),
                            comp_min=int(lo) if lo else None, comp_max=int(hi) if hi else None,
                            comp_currency=cur))
    elif ats == "lever":
        d = get_json(f"https://api.lever.co/v0/postings/{tok}?mode=json", timeout=90)
        if not isinstance(d, list):
            return None
        for j in d:
            cat = j.get("categories") or {}
            sr = j.get("salaryRange") or {}
            if sr.get("min") and str(sr.get("interval", "")).startswith("per-year"):
                lo, hi, cur = sr.get("min"), sr.get("max"), sr.get("currency")
            else:
                lo, hi = comp_from_text(j.get("additionalPlain", "") + " " + j.get("descriptionPlain", ""))
                cur = "USD" if lo else None
            loc = cat.get("location") or ""
            out.append(dict(job_id=j["id"], title=j.get("text", ""),
                            department=" / ".join(x for x in (cat.get("department"), cat.get("team")) if x),
                            location=loc, country=_country(j.get("country"), loc),
                            remote=int(j.get("workplaceType") == "remote" or "remote" in loc.lower()),
                            posted_at=_date(j.get("createdAt")), url=j.get("hostedUrl"),
                            comp_min=lo, comp_max=hi, comp_currency=cur))
    else:
        return None  # unknown ATS: treat as a failed fetch, never as "every role closed"
    for p in out:
        p["function"] = classify.function_of(p["title"], p["department"])
        p["seniority"] = classify.seniority_of(p["title"])
        p["themes"] = json.dumps(classify.themes_of(p["title"], p["department"]))
        p["leadership"] = int(classify.is_leadership(p["title"]))
    return out


