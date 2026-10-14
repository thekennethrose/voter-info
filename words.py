"""Build "In Their Words": members' own words from the Congressional Record, and
news coverage of them, linked where an article quotes the Record word for word.

usage: python words.py [--no-media] [MEMBER_ID ...]
  default: the pilot members below

Writes, under docs/data/words/:
  ID.json            floor: every speech, statement and extension of remarks
                     media: articles naming the member, with any quotes matched
  text/ID/GRANULE.json   the member's words in one section of the Record, for
                     sections an article quoted (the rest link to GovInfo)
  outlets.json       ratings for every outlet that appears, with attribution

Media accumulates: GDELT only searches the last three months, so every article it
has returned is kept in data/media/ID.json and rechecked each run. --no-media skips
the GDELT search and rechecks what's already there.
"""

import http.client
import json
import os
import re
import shutil
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import sources
import speeches
from spinner import Spinner

OUT = Path("docs/data/words")
# Every article GDELT has returned for each member, kept between runs (committed,
# not published): GDELT only searches back three months.
FOUND = Path("data/media")
START = "2025-01-03"  # the 119th Congress
PILOT = ["S413", "S416", "S313", "S303", "S365",  # Padilla, Britt, Sanders, Thune, T. Scott
         "R000575", "O000172", "J000289", "J000294", "F000481"]  # fmt: skip
MIN_WORDS = 50  # shorter turns are procedural: unanimous consent requests, yielding
# Sections that are procedure, not anyone's statement.
PROCEDURAL = {
    "PRAYER", "PLEDGE", "CALLTOORDER", "ADJOURNMENT", "FRONTMATTER", "HJOURNAL",
    "HDESIGNATION", "SORDERFOR", "SAUTHORITY", "SPRIVILEGES", "SEXECCAL",
    "SMEASUREDCAL", "SCONSENTAGREE", "SLEGISLATIVE", "SAMENDMENTTEXT",
    "SAMENDMENTTEXTIND", "SAMENDMENTSSUB", "SINTROBILLS", "SCOSPONSORS",
    "SSUBMISSION", "SNOMINATIONS", "SMSGEXEC", "MSGPRESIDENT", "EXECUTIVECOMM",
    "SPETANDMEM", "SEXECREPORT", "SMBUSINESS", "SCLOTURE", "CASTATEMENT",
    "HPUBBILLS", "HADDSPONSORS",
}  # fmt: skip
NAME_SHARE = 0.5  # matches more than half capitalized are names, not words
BOILERPLATE_MEMBERS = 3  # a 12-word run said by this many members is stock phrasing
MEDIA_DAYS = 30  # pilot; each run adds to what earlier runs found
# Media Cloud's own geographic collections (its curators choose the outlets):
# "United States - National" and "United States - State & Local".
MEDIACLOUD_COLLECTIONS = [34412234, 38379429]
QUOTE_DAYS = 120  # an article can quote a speech from up to this long before it
ABBREV = {
    "AL": "Ala.", "AZ": "Ariz.", "AR": "Ark.", "CA": "Calif.", "CO": "Colo.",
    "CT": "Conn.", "DE": "Del.", "FL": "Fla.", "GA": "Ga.", "IL": "Ill.",
    "IN": "Ind.", "KS": "Kan.", "KY": "Ky.", "LA": "La.", "MD": "Md.",
    "MA": "Mass.", "MI": "Mich.", "MN": "Minn.", "MS": "Miss.", "MO": "Mo.",
    "MT": "Mont.", "NE": "Neb.", "NV": "Nev.", "NH": "N.H.", "NJ": "N.J.",
    "NM": "N.M.", "NY": "N.Y.", "NC": "N.C.", "ND": "N.D.", "OK": "Okla.",
    "OR": "Ore.", "PA": "Pa.", "RI": "R.I.", "SC": "S.C.", "SD": "S.D.",
    "TN": "Tenn.", "VT": "Vt.", "VA": "Va.", "WA": "Wash.", "WV": "W.Va.",
    "WI": "Wis.", "WY": "Wyo.",
}  # fmt: skip
TITLES = {
    "senate": r"Sen\.|Senator|Senate",
    "house": r"Rep\.|Representative|Congressman|Congresswoman|Congressmember|House|Speaker",
}


def mediacloud_query(m):
    """The member's name with their title just before it, or their state within
    three words: the same test names_member applies, run by Media Cloud's search
    so namesakes aren't downloaded at all."""
    n = common_name(m)
    titles = {"senate": ["Sen.", "Senator"],
              "house": ["Rep.", "Representative", "Congressman", "Congresswoman"]}  # fmt: skip
    states = {sources_state_name(m["state"]), ABBREV.get(m["state"])} - {None, ""}
    return " OR ".join(
        [f'"{t} {n}"' for t in titles[m["chamber"]]]
        + [f'"{n} {s}"~3' for s in sorted(states)]
    )


def common_name(m):
    """The name news uses: a quoted nickname if there is one, else the first given
    name without middle initials ("Mike D." -> "Mike")."""
    nick = re.search(r'"([^"]+)"', m["first"])
    first = nick.group(1) if nick else m["first"].split()[0]
    return f"{first} {m['last']}"


def names_member(m, text):
    """The article names this member: full name, with their chamber title or their
    state within a few words of it. Keeps out namesakes (a coach called Tim Scott)
    and other members who share a name."""
    states = [sources_state_name(m["state"]), m["state"], ABBREV.get(m["state"], "")]
    context = TITLES[m["chamber"]] + "|" + "|".join(re.escape(s) for s in states if s)
    pat = re.compile(
        rf"(?:{context})[^.]{{0,60}}?{re.escape(common_name(m))}"
        rf"|{re.escape(common_name(m))}[^.]{{0,40}}?(?:{context})"
    )
    return bool(pat.search(text))


def sources_state_name(code):
    return next((k for k, v in sources.STATE_CODES.items() if v == code), "")


def domain(url_or_host):
    host = re.sub(r"^https?://", "", url_or_host).split("/")[0].lower()
    parts = host.removeprefix("www.").split(".")
    return ".".join(parts[-2:])


class Builder:
    def __init__(self, api_key, members, progress):
        self.api_key = api_key
        self.members = members  # site id -> member, plus "bioguide"
        self.by_bioguide = {m["bioguide"]: m["id"] for m in members.values()}
        self.progress = progress
        self.floor = {i: {} for i in members}  # id -> granule -> speech
        self.tokens = {}  # (id, granule) -> normalized words
        self.caps = {}  # (id, granule) -> whether each word is capitalized

    def record(self):
        issues = sources.crec_issues(START, date.today().isoformat(), self.api_key)
        mods = [sources.CREC_MODS.format(pkg=p) for p, _ in issues]
        sources.prefetch(
            mods, sources.is_mods,
            on_done=lambda i, n: self.progress(f"Record: index {i}/{n} days"),
        )  # fmt: skip
        wanted = []
        for (pkg, _), url in zip(issues, mods):
            for g in sources.crec_granules(sources.cached(url, sources.is_mods)):
                if g["subclass"] in PROCEDURAL:
                    continue
                for bio, name in g["speakers"]:
                    if bio in self.by_bioguide:
                        wanted.append((pkg, g, self.by_bioguide[bio], name))
        pages = [
            sources.CREC_HTML.format(pkg=p, granule=g["granule"]) for p, g, *_ in wanted
        ]
        sources.prefetch(
            sorted(set(pages)), sources.is_crec_html,
            on_done=lambda i, n: self.progress(f"Record: text {i}/{n} sections"),
        )  # fmt: skip
        for (pkg, g, mid, name), url in zip(wanted, pages):
            ts = speeches.turns(
                sources.cached(url, sources.is_crec_html), name, len(g["speakers"]) == 1
            )
            paras = [p for t in ts for p in t["paras"]]
            n = sum(len(p.split()) for p in paras)
            if n < MIN_WORDS:
                continue
            if g["class"] == "EXTENSIONS":
                kind = "extension"
            elif all(t["submitted"] for t in ts):
                kind = "submitted"
            elif g["subclass"] in ("SSTATEMENTS", "SSTATEMENTSIND"):
                kind = "introduced"
            else:
                kind = g["class"].lower()  # "senate" or "house" proceedings
            self.floor[mid][g["granule"]] = {
                "g": g["granule"], "date": g["date"], "title": g["title"],
                "kind": kind, "words": n, "turns": [t["paras"] for t in ts],
            }  # fmt: skip
            self.tokens[(mid, g["granule"])] = speeches.words(" ".join(paras))
            self.caps[(mid, g["granule"])] = speeches.capitalized(" ".join(paras))

    def dedupe(self):
        """GovInfo publishes some text twice: "Statements on Introduced Bills" as one
        section and again as a section per bill. Drop a section when every paragraph
        of it appears in the member's other sections that day."""
        for mid, items in self.floor.items():
            # Larger sections first, so the per-bill copy is the one kept.
            for g, s in sorted(items.items(), key=lambda kv: -kv[1]["words"]):
                others = {p for o in items.values() if o is not s and o["date"] == s["date"]
                          and o["words"] <= s["words"] for t in o["turns"] for p in t}  # fmt: skip
                if all(p in others for t in s["turns"] for p in t):
                    del items[g]
                    del self.tokens[(mid, g)]
                    del self.caps[(mid, g)]

    def index(self):
        """Every 12-word run in the members' speeches, minus stock phrasing: runs
        that several different members used."""
        said_by = {}
        for (mid, _), toks in self.tokens.items():
            for g in speeches.grams(toks):
                said_by.setdefault(g, set()).add(mid)
        common = frozenset(
            g for g, who in said_by.items() if len(who) >= BOILERPLATE_MEMBERS
        )
        idx = speeches.Index(common)
        for key, toks in self.tokens.items():
            idx.add(key, toks)
        return idx

    def is_name(self, key, span):
        """A matching run that is mostly capitalized words is a name, usually a
        bill's ("the Lindsey O. Graham Sanctioning Russia and Iran Act of 2026"),
        not anyone's words."""
        caps = self.caps[key][span[0] : span[1]]
        return sum(caps) > len(caps) * NAME_SHARE

    def search_gdelt(self, mid, found):
        """Add articles GDELT has indexed in the last MEDIA_DAYS days that name the member to
        `found` (url -> article), the list earlier runs built up. One query for the
        whole period; periods that hit GDELT's 250-article cap are split in half
        until they don't (or are two days long). Returns the periods still capped."""
        m = self.members[mid]
        end = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        todo, capped = [(end - timedelta(days=MEDIA_DAYS), end)], []
        while todo:
            lo, hi = todo.pop()
            self.progress(f"Media: {common_name(m)}, {lo:%b %d}–{hi:%b %d}")
            arts = sources.gdelt(
                f'"{common_name(m)}" sourcecountry:US',
                lo.strftime("%Y%m%d%H%M%S"), hi.strftime("%Y%m%d%H%M%S"),
            )  # fmt: skip
            if len(arts) >= sources.GDELT_MAX:
                if hi - lo > timedelta(days=2):
                    mid_t = lo + (hi - lo) / 2
                    todo += [(lo, mid_t), (mid_t, hi)]
                else:
                    capped.append(lo.date().isoformat())
            for a in arts:
                seen = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ")
                add(found, "gdelt", a["url"], seen.date().isoformat(), a["domain"],
                    untokenize(a["title"]))  # fmt: skip
        return sorted(capped)

    def search_mediacloud(self, mid, found, api_key):
        """Add stories in Media Cloud's U.S. collections from the last MEDIA_DAYS
        days that name the member. Media Cloud chooses which outlets its collections hold."""
        m = self.members[mid]
        end = date.today()
        self.progress(f"Media Cloud: {common_name(m)}")
        stories = sources.mediacloud_stories(
            mediacloud_query(m), (end - timedelta(days=MEDIA_DAYS)).isoformat(),
            end.isoformat(), MEDIACLOUD_COLLECTIONS, api_key,
        )  # fmt: skip
        for st in stories:
            add(found, "mediacloud", st["url"], str(st["publish_date"])[:10],
                st.get("media_url") or st["url"], st.get("title") or "")  # fmt: skip

    def check(self, mid, found, idx):
        """Read each article: keep it if it names the member, and find any run of
        12+ words it shares with the member's speeches from the 120 days before.
        Redone every run, since the Record is posted a day or more after the news."""
        m = self.members[mid]
        arts = sorted(found.values(), key=lambda a: (a["date"], a["url"]), reverse=True)
        kept = []
        with ThreadPoolExecutor(16) as pool:
            pages = pool.map(sources.article, [a["url"] for a in arts])
            for i, (a, page) in enumerate(zip(arts, pages), 1):
                self.progress(f"Media: {common_name(m)}, reading {i}/{len(arts)}")
                out = {k: a[k] for k in ("date", "domain", "url", "via")}
                if page is None:
                    # Can't read it: keep it only if the headline names them.
                    if m["last"].lower() not in a["title"].lower():
                        continue
                    kept.append(out | {"title": a["title"], "checked": False})
                    continue
                text = page["text"]
                if not names_member(m, text):
                    continue
                out["title"] = page["title"] or a["title"]
                out["checked"] = True
                if page.get("partial"):
                    out["partial"] = True
                lo = (
                    date.fromisoformat(a["date"]) - timedelta(days=QUOTE_DAYS)
                ).isoformat()
                keys = {
                    (mid, g)
                    for g, s in self.floor[mid].items()
                    if lo <= s["date"] <= a["date"]
                }
                quotes = {
                    key: kept_spans
                    for key, spans in idx.quotes(text, keys).items()
                    if (kept_spans := [sp for sp in spans if not self.is_name(key, sp)])
                }
                if quotes:
                    out["quotes"] = [
                        {"g": g, "spans": sp} for (_, g), sp in sorted(quotes.items())
                    ]
                kept.append(out)
        return kept


def url_key(url):
    """One key per article however an index spells its URL: no scheme, www,
    tracking parameters or trailing slash."""
    p = urllib.parse.urlsplit(url.strip())
    q = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(p.query)
        if not k.startswith(("utm_", "fbclid", "gclid"))
    ]
    path = p.path.rstrip("/")
    return f"{p.netloc.lower().removeprefix('www.')}{path}" + (
        f"?{urllib.parse.urlencode(q)}" if q else ""
    )


def add(found, via, url, day, site, title):
    """Record that an index returned this article; the earliest date wins."""
    a = found.setdefault(url_key(url), {"url": url, "date": day, "domain": domain(site),
                                        "title": title, "via": []})  # fmt: skip
    if via not in a["via"]:
        a["via"].append(via)
        a["via"].sort()
    a["date"] = min(a["date"], day)
    a["title"] = a["title"] or title


def untokenize(title):
    """GDELT spaces out punctuation ("Sen . Padilla"); put it back."""
    t = re.sub(r"\s+([.,:;?!)%])", r"\1", title)
    return re.sub(r"([($])\s+", r"\1", t).strip()


def write(b, media, capped, scores):
    shutil.rmtree(OUT / "text", ignore_errors=True)  # only sections quoted now
    (OUT / "text").mkdir(parents=True)
    domains = set()
    for mid, items in b.floor.items():
        arts = media.get(mid, [])
        quoted = {}
        for i, a in enumerate(arts):
            domains.add(a["domain"])
            for q in a.get("quotes", []):
                quoted.setdefault(q["g"], []).append(i)
        floor = []
        for g, s in sorted(
            items.items(), key=lambda kv: (kv[1]["date"], kv[0]), reverse=True
        ):
            floor.append(
                {k: v for k, v in s.items() if k != "turns"}
                | ({"quoted_in": quoted[g]} if g in quoted else {})
            )
            if g in quoted:
                path = OUT / "text" / mid / f"{g}.json"
                path.parent.mkdir(exist_ok=True)
                path.write_text(
                    json.dumps({"turns": s["turns"]}, separators=(",", ":"))
                )
        (OUT / f"{mid}.json").write_text(json.dumps({
            "generated": date.today().isoformat(),
            "record_from": START,
            "media_from": min((a["date"] for a in arts), default=None),
            "capped": capped.get(mid, []),
            "floor": floor,
            "media": arts,
        }, indent=1))  # fmt: skip
    (OUT / "outlets.json").write_text(json.dumps({
        "raters": {k: {f: v for f, v in RATERS[k].items() if f != "load"} for k in scores},
        "domains": {
            d: {k: scores[k][d] for k in scores if d in scores[k]}
            for d in sorted(domains) if any(d in sc for sc in scores.values())
        },
    }, indent=1))  # fmt: skip


# Outlet ratings, always someone else's and attributed. Only raters in
# PUBLISHED_RATERS are written to the site; the page shows nothing for the rest.
RATERS = {
    "robertson": {
        "name": "Robertson et al. (2018)",
        "measures": "Partisan audience: how much more often registered Republicans than Democrats shared links to the site on Twitter, from -1 (only Democrats) to 1 (only Republicans). Measured 2017; describes who shared the outlet, not its content.",
        "cite": "R. E. Robertson, S. Jiang, K. Joseph, L. Friedland, D. Lazer and C. Wilson, \"Auditing Partisan Audience Bias within Google Search,\" Proc. ACM Hum.-Comput. Interact. 2 (CSCW), 2018.",
        "url": "https://doi.org/10.1145/3274417",
        "load": sources.robertson_scores,
    },
}  # fmt: skip
# Robertson's dataset is released "for research purposes only"; not published until
# the authors agree. AllSides: waiting on their API.
PUBLISHED_RATERS = set()


def load_members(ids):
    directory = json.loads(Path("docs/data/members.json").read_text())["members"]
    lis = sources.senate_bioguide()
    out = {}
    for m in directory:
        if m["id"] in ids:
            out[m["id"]] = {
                **m,
                "bioguide": lis.get(m["id"]) if m["chamber"] == "senate" else m["id"],
            }
    missing = [i for i in ids if i not in out or not out[i]["bioguide"]]
    if missing:
        sys.exit(f"not found (or not a current senator): {', '.join(missing)}")
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ids = args or PILOT
    search = "--no-media" not in sys.argv
    media, capped = {}, {}
    with Spinner() as sp:
        progress = lambda t: setattr(sp, "text", t)  # noqa: E731
        b = Builder(os.environ["CONGRESS_API_KEY"], load_members(ids), progress)
        b.record()
        b.dedupe()
        idx = b.index()
        mc_key = os.environ.get("MEDIACLOUD_API_KEY")
        indexes = {"gdelt": search, "mediacloud": search and bool(mc_key)}
        for mid in ids:
            path = FOUND / f"{mid}.json"
            found = json.loads(path.read_text()) if path.exists() else {}
            capped[mid] = []
            if indexes["gdelt"]:
                try:
                    capped[mid] = b.search_gdelt(mid, found)
                except sources.Throttled as e:
                    # Keep building with the articles earlier runs found.
                    sp.write(f"GDELT is refusing requests ({e}); skipping it for "
                             "the rest of this run.")  # fmt: skip
                    indexes["gdelt"] = False
            if indexes["mediacloud"]:
                try:
                    b.search_mediacloud(mid, found, mc_key)
                except (OSError, ValueError, http.client.HTTPException) as e:
                    sp.write(f"Media Cloud failed ({e}); skipping it for the rest "
                             "of this run.")  # fmt: skip
                    indexes["mediacloud"] = False
            FOUND.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(found, indent=1, sort_keys=True))
            media[mid] = b.check(mid, found, idx)
        scores = {k: RATERS[k]["load"]() for k in PUBLISHED_RATERS}
    write(b, media, capped, scores)
    for mid in ids:
        arts = media[mid]
        print(
            f"{common_name(b.members[mid])}: {len(b.floor[mid])} speeches and "
            f"statements; {len(arts)} articles, "
            f"{sum('quotes' in a for a in arts)} quoting the Record"
        )
