"""Build a senator's voting record on a topic as JSON, grouped by bill.

usage: python record.py LIS_ID TOPIC CONGRESS-SESSION [...]
  e.g. python record.py S414 healthcare 117-1 117-2 118-1 118-2 119-1 119-2
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from spinner import Spinner
from topic_votes import bill_id, topic_votes

OUT = Path("docs/data")


def iso(senate_date):
    return datetime.strptime(senate_date, "%B %d, %Y, %I:%M %p").isoformat()


def group_key(congress, issue):
    # Bill numbers restart every Congress: S. 6 in the 117th is not S. 6 in the 119th.
    bill = bill_id(issue)
    return f"{congress}-{''.join(bill) if bill else issue}"


def build(lis_id, topic, sessions, api_key, progress=None):
    groups, senator, matched = {}, None, 0
    for congress, session in sessions:
        if progress:
            progress(f"Loading {congress}-{session} vote list")

        def scan(i, total):
            if progress:
                progress(
                    f"Scanning {congress}-{session}  vote {i}/{total}  "
                    f"· {matched} matched"
                )

        for v, vote, tier, reason in topic_votes(
            congress, session, lis_id, topic, api_key, on_scan=scan
        ):
            matched += 1
            senator = vote.senator
            g = groups.setdefault(
                group_key(congress, v["issue"]),
                {
                    "issue": v["issue"],
                    "congress": congress,
                    "tier": tier,
                    "reason": reason,
                    "votes": [],
                },
            )
            if tier == "core" and g["tier"] != "core":
                g["tier"], g["reason"] = tier, reason
            g["votes"].append(
                {
                    "date": iso(vote.date),
                    "roll_call": f"{congress}-{session}-{v['number']}",
                    "title": vote.title,
                    "document": vote.document,
                    "cast": vote.cast,
                    "result": vote.result,
                    "yeas": vote.yeas,
                    "nays": vote.nays,
                    "requirement": vote.requirement,
                    "source": vote.url,
                    "tier": tier,
                    "reason": reason,
                }
            )

    items = list(groups.values())
    for g in items:
        g["votes"].sort(key=lambda x: x["date"], reverse=True)
    items.sort(key=lambda g: g["votes"][0]["date"], reverse=True)
    return {
        "senator": senator,
        "lis_id": lis_id,
        "topic": topic,
        "sessions": [f"{c}-{s}" for c, s in sessions],
        "generated": date.today().isoformat(),
        "items": items,
    }


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__.strip())
    lis_id, topic, *raw = sys.argv[1:]
    sessions = [tuple(int(x) for x in s.split("-")) for s in raw]
    with Spinner() as sp:
        record = build(
            lis_id,
            topic,
            sessions,
            os.environ["CONGRESS_API_KEY"],
            progress=lambda text: setattr(sp, "text", text),
        )

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{lis_id}-{topic}.json"
    path.write_text(json.dumps(record, indent=2))
    core = sum(g["tier"] == "core" for g in record["items"])
    votes = sum(len(g["votes"]) for g in record["items"])
    print(
        f"{path}: {len(record['items'])} bills/nominations ({core} core), {votes} votes"
    )
