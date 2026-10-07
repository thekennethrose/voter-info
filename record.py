"""Build the site's data from Senate and House roll calls on bills and nominations.

usage: python record.py [CONGRESS-SESSION ...]
  default: every session since January 2021 (117-1 through 119-2)

Always build every session you publish: each run replaces the data folder.
Writes, under docs/data/:
  votes/s119.json, votes/h119.json   bills (with CRS policy area), nominations
                                     and vote details, per chamber and Congress
  casts/ID.json                      one member's vote on every roll call
                                     (senators by LIS id, representatives by
                                     bioguide id): {"119-1-644": "Yea"}
  members.json                       everyone in those sessions, for search
"""

import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import sources
import votes
from spinner import Spinner

OUT = Path("docs/data")
SESSIONS = ["117-1", "117-2", "118-1", "118-2", "119-1", "119-2"]


class Builder:
    def __init__(self, api_key, progress):
        self.api_key = api_key
        self.progress = progress
        self.files = {}  # "s119" / "h119" -> votes file contents
        self.casts = {}  # member id -> {roll call: cast}
        self.members = {}  # member id -> latest info

    def file(self, chamber, congress):
        return self.files.setdefault(
            f"{chamber[0]}{congress}",
            {"chamber": chamber, "congress": congress, "bills": {},
             "nominations": {}, "votes": {}},
        )  # fmt: skip

    def bill(self, data, congress, kind):
        key = "".join(kind).upper()
        if key not in data["bills"]:
            data["bills"][key] = {
                "label": sources.bill_label(*kind),
                **sources.bill_info(congress, *kind, self.api_key),
            }
        return key

    def add(self, chamber, data, rc, ref, meta, roll):
        keep = ("title", "purpose", "tie_breaker", "result", "yeas", "nays",
                "requirement")  # fmt: skip
        data["votes"][rc] = {
            **ref,
            "date": meta["date"],
            **{k: meta[k] for k in keep if meta.get(k) not in (None, "")},
        }
        for m in roll:
            self.casts.setdefault(m["id"], {})[rc] = m["cast"]
            seen = self.members.get(m["id"])
            if seen is None or meta["date"] >= seen["last_vote"]:
                self.members[m["id"]] = {
                    "id": m["id"], "chamber": chamber, "first": m["first"],
                    "last": m["last"], "party": m["party"], "state": m["state"],
                    "last_vote": meta["date"],
                }  # fmt: skip

    def senate(self, c, s):
        self.progress(f"Senate {c}-{s}: loading vote list")
        data = self.file("senate", c)
        kept = [
            (e, k)
            for e in sources.senate_menu(c, s)
            if (k := sources.classify_senate(e))
        ]
        sources.prefetch(
            [sources.senate_url(c, s, e["number"]) for e, _ in kept],
            valid=sources.is_senate_roll,
            workers=1,  # senate.gov is paced in sources.fetch
            on_done=lambda i, n: self.progress(f"Senate {c}-{s}: downloading {i}/{n}"),
        )
        for i, (entry, (kind, what)) in enumerate(kept, 1):
            self.progress(f"Senate {c}-{s}: vote {i}/{len(kept)}")
            xml = sources.cached(
                sources.senate_url(c, s, entry["number"]), sources.is_senate_roll
            )
            meta, roll = votes.parse_senate(xml)
            if kind == "bill":
                ref = {"bill": self.bill(data, c, what)}
            else:
                data["nominations"].setdefault(
                    what, {"label": what, "title": meta["document"] or meta["title"]}
                )
                ref = {"nomination": what}
            self.add("senate", data, f"{c}-{s}-{entry['number']}", ref, meta, roll)

    def house(self, c, s):
        self.progress(f"House {c}-{s}: counting roll calls")
        data = self.file("house", c)
        numbers = sources.house_roll_numbers(c, s, self.api_key)
        sources.prefetch(
            [sources.house_url(c, s, n) for n in numbers],
            valid=sources.is_house_roll,
            on_done=lambda i, n: self.progress(f"House {c}-{s}: downloading {i}/{n}"),
        )
        for i, n in enumerate(numbers, 1):
            self.progress(f"House {c}-{s}: vote {i}/{len(numbers)}")
            xml = sources.cached(sources.house_url(c, s, n), sources.is_house_roll)
            meta, roll = votes.parse_house(xml)
            kind = sources.classify_house(meta)
            if not kind:
                continue  # quorum calls, journal, adjournment, Speaker election
            ref = {"bill": self.bill(data, c, kind[1])}
            self.add("house", data, f"{c}-{s}-{n}", ref, meta, roll)

    def name_representatives(self, congresses):
        """House XML gives last names only; congress.gov adds full names and districts."""
        for c in sorted(congresses):
            self.progress(f"Members of the {c}th Congress")
            for bio, d in sources.member_details(c, self.api_key).items():
                m = self.members.get(bio)
                if m and m["chamber"] == "house":
                    m.update(first=d["first"], last=d["last"], district=d["district"])


CODES = {"Yea": "Y", "Aye": "A", "Nay": "N", "No": "O", "Present": "P",
         "Not Voting": "V", "Guilty": "G", "Not Guilty": "U"}  # fmt: skip


def pack(casts, chamber, lengths):
    """{"119-1-644": "Yea"} -> {"119-1": "--...Y"}: one character per roll call
    number in the session, "-" where the member has no kept vote. About 10x
    smaller than the plain map, and stable between rebuilds. Rare official
    wordings ("Present, Giving Live Pair") are marked "*" and kept verbatim
    under "other"."""
    rows, other = {}, {}
    for rc, cast in casts.items():
        c, s, n = rc.split("-")
        key = f"{c}-{s}"
        row = rows.setdefault(key, ["-"] * lengths[(chamber, key)])
        row[int(n) - 1] = CODES.get(cast, "*")
        if cast not in CODES:
            other[rc] = cast
    packed = {k: "".join(v).rstrip("-") for k, v in sorted(rows.items())}
    if other:
        packed["other"] = other
    return packed


def write(sessions, b):
    for sub in ("votes", "casts", "congress"):
        shutil.rmtree(OUT / sub, ignore_errors=True)
    (OUT / "votes").mkdir(parents=True)
    (OUT / "casts").mkdir()
    for name, data in b.files.items():
        (OUT / "votes" / f"{name}.json").write_text(json.dumps(data, indent=1))
    lengths = {}  # (chamber, "119-1") -> highest roll call number kept
    for data in b.files.values():
        for rc in data["votes"]:
            c, s, n = rc.split("-")
            key = (data["chamber"], f"{c}-{s}")
            lengths[key] = max(lengths.get(key, 0), int(n))
    for member_id, c in b.casts.items():
        packed = pack(c, b.members[member_id]["chamber"], lengths)
        (OUT / "casts" / f"{member_id}.json").write_text(
            json.dumps(packed, separators=(",", ":"))
        )

    newest = {}
    for m in b.members.values():
        newest[m["chamber"]] = max(newest.get(m["chamber"], ""), m["last_vote"])
    members = []
    for m in b.members.values():
        entry = {k: v for k, v in m.items() if k != "last_vote"}
        entry["current"] = m["last_vote"] == newest[m["chamber"]]
        members.append(entry)
    members.sort(key=lambda m: (m["state"], m["chamber"] != "senate",
                                m.get("district") or 0, m["last"]))  # fmt: skip
    (OUT / "members.json").write_text(
        json.dumps(
            {
                "generated": date.today().isoformat(),
                "sessions": sessions,
                "members": members,
            },
            indent=1,
        )  # fmt: skip
    )


if __name__ == "__main__":
    raw = sys.argv[1:] or SESSIONS
    sessions = [tuple(int(x) for x in s.split("-")) for s in raw]
    with Spinner() as sp:
        b = Builder(os.environ["CONGRESS_API_KEY"], lambda t: setattr(sp, "text", t))
        for c, s in sessions:
            b.senate(c, s)
            b.house(c, s)
        b.name_representatives({c for c, _ in sessions})
    write(raw, b)

    for chamber in ("senate", "house"):
        fs = [d for d in b.files.values() if d["chamber"] == chamber]
        people = sum(m["chamber"] == chamber for m in b.members.values())
        print(
            f"{chamber.title()}: {sum(len(d['votes']) for d in fs):,} roll calls on "
            f"{sum(len(d['bills']) for d in fs):,} bills"
            + (f" and {sum(len(d['nominations']) for d in fs):,} nominations"
               if chamber == "senate" else "")
            + f"; {people} members."
        )  # fmt: skip
