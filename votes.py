"""Parse official roll call records: senate.gov and clerk.house.gov XML. No LLM."""

import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

SENATE_URL = (
    "https://www.senate.gov/legislative/LIS/roll_call_votes/"
    "vote{c}{s}/vote_{c}_{s}_{n:05d}.xml"
)
HOUSE_URL = "https://clerk.house.gov/evs/{year}/roll{n:03d}.xml"


def session_year(congress, session):
    return 1789 + 2 * (congress - 1) + session - 1


def parse_senate(xml):
    """Return (vote details, every senator's vote) for one Senate roll call."""
    root = ET.fromstring(xml)

    def t(path):
        return " ".join((root.findtext(path) or "").split())

    purpose = t("amendment/amendment_purpose")
    tie = t("tie_breaker/by_whom")
    meta = {
        "title": t("vote_title"),
        "document": t("vote_document_text"),
        "purpose": "" if purpose.startswith("No Statement of Purpose") else purpose,
        "tie_breaker": f"{tie} voted {t('tie_breaker/tie_breaker_vote')}"
        if tie
        else "",
        "result": t("vote_result"),
        "yeas": int(t("count/yeas") or 0),
        "nays": int(t("count/nays") or 0),
        "requirement": t("majority_requirement"),
        "date": datetime.strptime(t("vote_date"), "%B %d, %Y, %I:%M %p").isoformat(),
    }
    members = [
        {
            "id": m.findtext("lis_member_id"),
            "first": m.findtext("first_name"),
            "last": m.findtext("last_name"),
            "party": m.findtext("party"),
            "state": m.findtext("state"),
            "cast": m.findtext("vote_cast"),
        }
        for m in root.iter("member")
    ]
    return meta, members


def parse_house(xml):
    """Return (vote details, every representative's vote) for one House roll call."""
    root = ET.fromstring(xml)
    md = root.find("vote-metadata")
    if md is None:
        raise ValueError("not a House roll call")

    def t(path):
        return " ".join((md.findtext(path) or "").split())

    def total(tag):
        return int(t(f"vote-totals/totals-by-vote/{tag}") or 0)

    clock = md.find("action-time")
    hm = clock.get("time-etz") if clock is not None else "00:00"
    vote_type = t("vote-type")
    meta = {
        "legis": t("legis-num"),
        "title": t("vote-question"),
        "purpose": t("vote-desc"),
        "result": t("vote-result"),
        "yeas": total("yea-total"),
        "nays": total("nay-total"),
        "requirement": "2/3" if "2/3" in vote_type else "1/2",
        "date": datetime.strptime(
            f"{t('action-date')} {hm}", "%d-%b-%Y %H:%M"
        ).isoformat(),
    }
    members = []
    for rv in root.iter("recorded-vote"):
        leg = rv.find("legislator")
        members.append(
            {
                "id": leg.get("name-id"),
                "first": "",
                "last": leg.get("unaccented-name") or (leg.text or "").strip(),
                "party": leg.get("party"),
                "state": leg.get("state"),
                "cast": (rv.findtext("vote") or "").strip(),
            }
        )
    return meta, members


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(
            "usage: python votes.py senate|house CONGRESS-SESSION ROLL MEMBER_ID\n"
            "  e.g. python votes.py senate 119-1 644 S414\n"
            "       python votes.py house 119-1 190 O000172"
        )
    chamber, cs, n, member_id = sys.argv[1:]
    c, s = (int(x) for x in cs.split("-"))
    if chamber == "senate":
        url = SENATE_URL.format(c=c, s=s, n=int(n))
    else:
        url = HOUSE_URL.format(year=session_year(c, s), n=int(n))
    req = urllib.request.Request(url, headers={"User-Agent": "voter-info/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        xml = r.read()
    meta, members = (parse_senate if chamber == "senate" else parse_house)(xml)
    m = next((m for m in members if m["id"] == member_id), None)
    if m is None:
        sys.exit(f"{member_id} isn't in this roll call")
    print(f"{m['first']} {m['last']} ({m['party']}-{m['state']}) voted {m['cast']}")
    print(f"  {meta['title']}  {meta.get('document') or meta.get('purpose', '')}")
    print(f"  {meta['result']}, {meta['yeas']}-{meta['nays']}, {meta['date']}")
    print(f"  Source: {url}")
