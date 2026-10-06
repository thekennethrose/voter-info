"""Look up a senator's vote on a Senate roll call, straight from senate.gov. No LLM."""

import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

URL = (
    "https://www.senate.gov/legislative/LIS/roll_call_votes/"
    "vote{c}{s}/vote_{c}_{s}_{n:05d}.xml"
)


@dataclass
class Vote:
    senator: str
    cast: str
    title: str
    document: str
    result: str
    yeas: int
    nays: int
    requirement: str
    date: str
    url: str

    def __str__(self):
        return (
            f"{self.senator} voted {self.cast}: {self.title}\n"
            f"  {self.document}\n"
            f"  Result: {self.result}, {self.yeas}-{self.nays} "
            f"({self.requirement} required)\n"
            f"  {self.date}\n"
            f"  Source: {self.url}"
        )


def fetch(congress, session, number):
    url = URL.format(c=congress, s=session, n=number)
    req = urllib.request.Request(url, headers={"User-Agent": "voter-info/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return url, r.read()


def parse(xml, lis_id, url=""):
    root = ET.fromstring(xml)
    member = next(
        (m for m in root.iter("member") if m.findtext("lis_member_id") == lis_id),
        None,
    )
    if member is None:
        raise LookupError(f"{lis_id} not found in this roll call")

    def t(path):
        return (root.findtext(path) or "").strip()

    return Vote(
        senator=member.findtext("member_full"),
        cast=member.findtext("vote_cast"),
        title=t("vote_title"),
        document=t("vote_document_text"),
        result=t("vote_result"),
        yeas=int(t("count/yeas") or 0),
        nays=int(t("count/nays") or 0),
        requirement=t("majority_requirement"),
        date=" ".join(t("vote_date").split()),
        url=url,
    )


def senator_vote(congress, session, number, lis_id):
    url, xml = fetch(congress, session, number)
    return parse(xml, lis_id, url)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(
            "usage: python votes.py CONGRESS SESSION VOTE_NUMBER LIS_ID\n"
            "  e.g. python votes.py 119 1 644 S414"
        )
    c, s, n, lis_id = sys.argv[1:]
    print(senator_vote(int(c), int(s), int(n), lis_id))
