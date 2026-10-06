"""Find Senate roll calls on a topic and how one senator voted on each.

Votes come from senate.gov; topic tags come from congress.gov (CRS policy area +
legislative subjects), with a title-keyword fallback for votes with no bill
(nominations, amendments). Every match says why it matched.
"""

import functools
import hashlib
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import votes
from spinner import Spinner

MENU = "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{c}_{s}.xml"
SUBJECTS = (
    "https://api.congress.gov/v3/bill/{c}/{t}/{n}/subjects"
    "?format=json&limit=250&api_key={k}"
)
CACHE = Path("data/cache")

TOPICS = {
    "healthcare": {
        "policy_area": "Health",
        # Exact CRS subject terms. Substrings are too loose ("Animal and plant health").
        "subjects": {
            "Medicaid",
            "Medicare",
            "Health care costs and insurance",
            "Health care coverage and access",
            "Prescription drugs",
            "Health programs administration and funding",
            "Department of Health and Human Services",
        },
        "keywords": ["health", "medicare", "medicaid", "affordable care act"],
    },
}

# Votes whose menu "issue" is the pending bill, not the thing voted on.
NOT_THE_BILL = ("executive session", "confirmation", "nomination")

BILL = re.compile(r"^(HR|S|HJRES|SJRES|HCONRES|SCONRES|HRES|SRES)(\d+)$")


def get(url, cache=False):
    """Fetch a URL. cache=True only for things that never change (recorded votes)."""
    path = CACHE / hashlib.sha256(url.encode()).hexdigest()[:16]
    if cache and path.exists():
        return path.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "voter-info/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
    if cache:
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return body


def menu(congress, session):
    root = ET.fromstring(get(MENU.format(c=congress, s=session)))
    for v in root.iter("vote"):
        yield {
            "number": int(v.findtext("vote_number")),
            "issue": " ".join((v.findtext("issue") or "").split()),
            "title": " ".join((v.findtext("title") or "").split()),
        }


def bill_id(issue):
    m = BILL.match(issue.replace(".", "").replace(" ", "").upper())
    return (m.group(1).lower(), m.group(2)) if m else None


@functools.cache
def subjects(congress, btype, number, api_key):
    url = SUBJECTS.format(c=congress, t=btype, n=number, k=api_key)
    data = json.loads(get(url)).get("subjects", {})
    area = (data.get("policyArea") or {}).get("name", "")
    items = data.get("legislativeSubjects") or []
    if isinstance(items, dict):
        items = items.get("item", [])
    return area, [s["name"] for s in items]


def match(vote, topic, congress, api_key):
    """Return (tier, reason) or None.

    core:    the bill is a health bill, or the vote itself names the topic
    related: a bill about something else that carries health provisions
    """
    t = TOPICS[topic]
    title = vote["title"].lower()
    bill = None
    if not any(w in title for w in NOT_THE_BILL):
        bill = bill_id(vote["issue"])

    area, subs = subjects(congress, *bill, api_key) if bill else ("", [])
    if area == t["policy_area"]:
        return "core", f"policy area: {area}"
    for k in t["keywords"]:
        if k in title:
            return "core", f"title keyword: {k}"
    hits = [s for s in subs if s in t["subjects"]]
    if hits:
        return "related", f"subjects: {', '.join(hits)} (policy area: {area})"
    return None


def topic_votes(congress, session, lis_id, topic, api_key, on_scan=None):
    all_votes = list(menu(congress, session))
    for i, v in enumerate(all_votes, 1):
        if on_scan:
            on_scan(i, len(all_votes))
        m = match(v, topic, congress, api_key)
        if not m:
            continue
        url = votes.URL.format(c=congress, s=session, n=v["number"])
        try:
            vote = votes.parse(get(url, cache=True), lis_id, url)
        except LookupError:
            continue  # senator not in this roll call
        yield v, vote, *m


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(
            "usage: python topic_votes.py CONGRESS SESSION LIS_ID TOPIC\n"
            "  e.g. python topic_votes.py 119 1 S414 healthcare"
        )
    c, s, lis_id, topic = sys.argv[1:]
    key = os.environ["CONGRESS_API_KEY"]
    with Spinner(f"Loading {c}-{s} vote list") as sp:

        def scan(i, total):
            sp.text = f"Scanning {c}-{s}  vote {i}/{total}"

        for v, vote, tier, reason in topic_votes(
            int(c), int(s), lis_id, topic, key, on_scan=scan
        ):
            sp.write(f"{tier:<8}#{v['number']}  {vote.cast:<10} {vote.title}")
            sp.write(f"        {vote.date} | {vote.result} | {reason}")
