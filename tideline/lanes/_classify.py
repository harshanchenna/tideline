"""Deterministic tagging of a posting: function, seniority, and strategic themes.

Rules read only the title and the department/team, never the description body, because
descriptions mention "AI" or "security" boilerplate on nearly every posting and drown the signal.
Order matters in FUNCTIONS: the first match wins.
"""
from __future__ import annotations

import re

FUNCTIONS: list[tuple[str, str]] = [
    ("Research", r"\bresearch (scientist|engineer|lead|manager)|\bresearcher\b|\bscientist\b|member of technical staff.*research"),
    ("Security", r"\bsecurity\b|\bsecops\b|\bdetection\b|\bred team|\bpentest|\bincident response"),
    ("Hardware", r"\bhardware\b|\basic\b|\bsilicon\b|\bfpga\b|\bmechanical\b|\belectrical\b|\bfirmware\b|\bthermal\b|\bpcb\b|\brf engineer|\bpackaging engineer|\bmanufacturing engineer|\btechnician\b|\bmachinist\b|\bassembl|\bavionics\b|\bpropulsion\b|\bstructures engineer"),
    ("Data", r"\bdata (scientist|engineer|analyst|platform)|\banalytics engineer|\bbusiness intelligence\b|\bbi (engineer|analyst)"),
    ("Sales", r"\b(solutions?|sales|field|pre-?sales) (engineer|architect)|\b(field|executive|technical) architect\b|\bapplied ai architect"),
    ("Design", r"\bdesigner\b"),
    ("Marketing", r"\bcopywriter\b|\bdeveloper (advocate|relations|marketing)|\bdevrel\b"),
    ("Engineering", r"\bengineer|\bengineering\b|\b(software|web|mobile|ios|android|front[- ]?end|back[- ]?end|full[- ]?stack|salesforce|blockchain|game|platform) developer\b|\bsre\b|\bmember of technical staff\b|\barchitect\b|\bprogrammer\b|\bdevops\b"),
    ("Product", r"\bproduct (manager|lead|owner|management)|\bhead of product|\bvp,? product|\bprogram manager|\btpm\b"),
    ("Design", r"\bdesign(er)?\b|\bux\b|\bresearch(er)?, user|\bcreative director|\bbrand\b"),
    ("Sales", r"\baccount (executive|manager|director)|\bsales\b|\bbusiness development\b|\bbdr\b|\bsdr\b|\bpartnerships?\b|\bgo[- ]to[- ]market\b|\bgtm\b|\bsolutions? (engineer|architect|consultant)|\bpre-?sales|\bforward deployed|\bdeployment strategist|\bcommercial\b"),
    ("Marketing", r"\bmarketing\b|\bcommunications?\b|\bcontent\b|\bgrowth\b|\bcommunity\b|\bpr\b|\bevents?\b|\bsocial media\b|\bcopywriter"),
    ("Customer", r"\bcustomer\b|\bsupport\b|\bsuccess\b|\bonboarding\b|\bimplementation\b|\bclient\b|\btechnical account"),
    ("People", r"\brecruit|\btalent\b|\bpeople\b|\bhr\b|\bhuman resources|\bsourcer\b|\bworkplace\b|\blearning (and|&) development"),
    ("Finance", r"\bfinance\b|\baccount(ant|ing)\b|\btax\b|\btreasury\b|\bfp&a\b|\bcontroller\b|\bpayroll\b|\bprocurement\b|\binvestor relations"),
    ("Legal", r"\blegal\b|\bcounsel\b|\bcompliance\b|\bpolicy\b|\bregulatory\b|\bprivacy\b|\bparalegal\b|\bgovernment affairs|\bpublic policy"),
    ("Operations", r"\boperations?\b|\bops\b|\bsupply chain\b|\blogistics\b|\bfacilities\b|\bchief of staff\b|\bstrategy\b|\bbusiness analyst\b|\bexecutive assistant|\badministrative\b|\bdata center\b"),
]

SENIORITY: list[tuple[str, str]] = [
    ("Exec", r"\bchief\b|\bc[etofmr]o\b|\bpresident\b|\bvp\b|\bvice president|\bgeneral manager\b|\bgm\b|\bhead of\b"),
    ("Director", r"\bdirector\b"),
    ("Manager", r"\b(engineering|software|research|people|team|design|data science|security|it|hardware|site|operations) manager\b|\bmanager,? (of )?(engineering|software|research|machine learning|ml|data|design|security|infrastructure|platform|people)\b|\bmanaging director\b|\bsupervisor\b|\bforeman\b"),
    ("Staff+", r"\bstaff\b|\bprincipal\b|\bdistinguished\b|\bfellow\b"),
    ("Senior", r"\bsenior\b|\bsr\.?\b|\biii\b|\bl5\b|\blead\b"),
    ("Intern", r"\bintern(ship)?\b|\bco-?op\b|\bresidency\b|\bfellowship\b|\bapprentice"),
    ("Junior", r"\bjunior\b|\bjr\.?\b|\bnew grad|\bentry[- ]level|\bassociate\b|\bearly career|\buniversity grad"),
]

# Strategic themes: what a company is building or where it is pushing. Kept specific on purpose;
# a theme that fires on half the postings carries no information.
THEMES: dict[str, str] = {
    "agents": r"\bagent(s|ic)?\b|\bcomputer use\b|\bbrowser use",
    "coding": r"\bcod(e|ing) (model|agent|assistant)|\bcodegen\b|\bdeveloper tools?\b|\bdevtools?\b|\bdeveloper experience\b|\bide\b",
    "inference": r"\binference\b|\bserving\b|\bmodel performance\b|\bkernels?\b|\bcuda\b|\btriton\b|\bcompilers?\b",
    "pretraining": r"\bpre-?train(ing)?\b|\bfoundation model|\bscaling\b|\btraining infra",
    "post-training/RL": r"\bpost-?train(ing)?\b|\brl(hf)?\b|\breinforcement learning\b|\breward model|\bfine-?tun",
    "AI safety/alignment": r"\bai safety\b|\bsafeguards\b|\balignment\b|\binterpretab|\bmodel welfare|\bresponsible (ai|scaling)|\bfrontier red team|\bmodel (behavior|policy)",
    "evals": r"\bevals?\b|\bevaluations?\b|\bbenchmark",
    "trust & safety": r"\btrust (and|&) safety|\bcontent moderation|\bintegrity\b",
    "multimodal/voice": r"\bvoice\b|\baudio\b|\bspeech\b|\bvideo\b|\bimage\b|\bvision\b|\bmultimodal\b|\b3d\b|\bworld model",
    "robotics": r"\brobot(ic|ics|s)?\b|\bhumanoid\b|\bmanipulation\b|\blocomotion\b|\bembodied\b|\bautonom(y|ous)\b|\bperception\b|\bslam\b",
    "chips/silicon": r"\bsilicon\b|\basic\b|\bchip\b|\bsoc\b|\bverification engineer|\bphysical design\b|\brtl\b|\bfpga\b|\bpackaging\b|\bphotonic",
    "datacenter/energy": r"\bdata ?cent(er|re)s?\b|\bpower (systems|engineer|electronics|distribution|infrastructure|procurement|generation)|\benergy\b|\bcooling\b|\bsite selection\b|\bconstruction\b|\bcapacity planning\b|\bnuclear\b|\bfusion (energy|power|reactor)|\bgpu (cluster|fleet)",
    "public sector/defense": r"\bpublic sector\b|\bfederal\b|\bgovernment\b|\bdefen[cs]e\b|\bnational security\b|\bdod\b|\bclearance\b|\bts/sci\b|\bintelligence community|\bmilitary\b|\bmission\b.*\b(army|navy|air force)",
    "healthcare/bio": r"\bhealth(care)?\b|\bclinical\b|\bmedical\b|\bbio(logy|tech|informatics)?\b|\bpharma|\bdrug\b|\bprotein\b|\bgenom|\bpatient",
    "financial services": r"\bfinancial services\b|\bbanking\b|\binsurance\b|\bcapital markets\b|\btrading\b|\bwealth\b|\bfsi\b",
    "crypto/stablecoins": r"\bcrypto\b|\bstablecoin|\bblockchain\b|\bweb3\b|\bdefi\b|\bonchain\b|\btokeniz",
    "payments": r"\bpayments?\b|\bcards?\b|\bissuing\b|\bacquiring\b|\bfraud\b|\brisk\b",
    "ads/monetization": r"\bads\b|\badvertis|\bmonetization\b|\bad tech\b|\bsponsored",
    "consumer": r"\bconsumer\b|\bsubscriptions?\b|\bcreator\b",
    "enterprise GTM": r"\benterprise\b|\bstrategic accounts?\b|\bmid-?market\b|\bchannel\b|\balliances\b",
    "forward-deployed": r"\bforward[- ]deployed\b|\bdeployment strateg|\bapplied ai engineer|\bai deployment|\bsolutions? engineer",
    "education": r"(?<!customer )(?<!partner )(?<!product )\beducation\b(?! services)|\bedu\b|\blearning sciences|\bteacher|\bk-?12\b|\bhigher ed",
    "hardware devices": r"\bconsumer hardware\b|\bdevice\b|\bwearable|\boptics\b|\bdisplay\b|\bbattery\b|\bindustrial design\b",
}

# Strong: the person who will run a new thing. Directors are common enough to count only in bulk.
LEADERSHIP = re.compile(r"\bhead of\b|\bvp\b|\bvice president\b|\bchief\b|\bgeneral manager\b|\bfounding\b", re.I)
DIRECTOR = re.compile(r"\bdirector\b", re.I)

_FUNCTIONS = [(name, re.compile(rx, re.I)) for name, rx in FUNCTIONS]
_SENIORITY = [(name, re.compile(rx, re.I)) for name, rx in SENIORITY]
_THEMES = {name: re.compile(rx, re.I) for name, rx in THEMES.items()}


def function_of(title: str, dept: str) -> str:
    for name, rx in _FUNCTIONS:
        if rx.search(title):
            return name
    for name, rx in _FUNCTIONS:
        if rx.search(dept):
            return name
    return "Other"


def seniority_of(title: str) -> str:
    for name, rx in _SENIORITY:
        if rx.search(title):
            return name
    return "Mid"


def themes_of(title: str, dept: str) -> list[str]:
    text = f"{title} | {dept}"
    return sorted(name for name, rx in _THEMES.items() if rx.search(text))


# Quota-carrying field sales titles say "vice president" but signal sales capacity, not a new org.
FIELD_SALES = re.compile(r"\b(regional|area|district|group) vice president\b|\b[ra]vp\b|\bvp,? (sales|enterprise|commercial|mid-?market)", re.I)


def is_leadership(title: str) -> int:
    """2 = head/VP/chief/GM/founding, 1 = director or field-sales VP, 0 = neither."""
    if FIELD_SALES.search(title):
        return 1
    return 2 if LEADERSHIP.search(title) else 1 if DIRECTOR.search(title) else 0
