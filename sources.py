"""Fetching from senate.gov, clerk.house.gov and congress.gov, with a disk cache."""

import gzip
import hashlib
import http.client
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from votes import HOUSE_URL, SENATE_URL, session_year

SENATE_MENU = (
    "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{c}_{s}.xml"
)
API = "https://api.congress.gov/v3/{path}?format=json&api_key={k}"
CACHE = Path("data/cache")

BILL = re.compile(r"^(HR|S|HJRES|SJRES|HCONRES|SCONRES|HRES|SRES)(\d+)$")
NOMINATION = re.compile(r"^PN\d+(-\d+)?$")
LABELS = {
    "hr": "H.R.",
    "s": "S.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
    "hres": "H.Res.",
    "sres": "S.Res.",
}
# Senate votes whose "issue" is the bill pending at the time, not the thing voted on.
NOT_THE_BILL = ("executive session", "confirmation", "nomination")
PARTIES = {"Democratic": "D", "Republican": "R", "Independent": "I"}
STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC", "Puerto Rico": "PR", "Guam": "GU",
    "American Samoa": "AS", "Virgin Islands": "VI",
    "Northern Mariana Islands": "MP",
}  # fmt: skip


# senate.gov's firewall answers sustained traffic with temporary 403s, so requests
# there are spaced out and a 403 means "back off", not "forbidden".
MIN_GAP = {"www.senate.gov": 1.0}
BACKOFF = [5, 15, 30, 60, 120]
_last_request = {}
_pace = threading.Lock()


def _wait_turn(host):
    gap = MIN_GAP.get(host)
    if not gap:
        return
    with _pace:
        wait = _last_request.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def fetch(url, valid=lambda body: True, tries=len(BACKOFF) + 1):
    """GET with pacing, and retries on throttling, server errors and garbage bodies."""
    host = urllib.parse.urlsplit(url).hostname
    req = urllib.request.Request(url, headers={"User-Agent": "voter-info/0.1"})
    for attempt in range(tries):
        _wait_turn(host)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
            if valid(body):
                return body
        except urllib.error.HTTPError as e:
            if e.code not in (403, 429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
        except (OSError, http.client.HTTPException):  # resets, timeouts, cut-off reads
            if attempt == tries - 1:
                raise
        if attempt < tries - 1:
            time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
    raise ValueError(f"no valid response from {url}")


def cached(url, valid=lambda body: True):
    """Fetch once and keep forever. Only for records that never change."""
    path = CACHE / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.gz"
    legacy = path.with_suffix("")  # earlier uncompressed cache files
    if path.exists():
        return gzip.decompress(path.read_bytes())
    if legacy.exists():
        return legacy.read_bytes()
    body = fetch(url, valid)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body))
    return body


def is_house_roll(body):
    return b"<rollcall-vote" in body


def is_senate_roll(body):
    return b"<roll_call_vote" in body


def prefetch(urls, valid=lambda body: True, on_done=None, workers=4):
    """Download roll calls in parallel into the cache; the build then reads them."""
    with ThreadPoolExecutor(workers) as pool:
        for i, _ in enumerate(pool.map(lambda u: cached(u, valid), urls), 1):
            if on_done:
                on_done(i, len(urls))


def api(path, api_key, **params):
    url = API.format(path=path, k=api_key)
    url += "".join(f"&{k}={v}" for k, v in params.items())
    return json.loads(fetch(url))


# --- Senate ---


def senate_menu(congress, session):
    """A session's vote list. Lists for finished sessions never change, so they're
    read from the saved copy; the current session's list is re-downloaded, falling
    back to the saved copy if senate.gov is blocking us."""
    path = CACHE / "menus" / f"senate-{congress}-{session}.xml"
    finished = session_year(congress, session) < date.today().year
    if finished and path.exists():
        menu = path.read_bytes()
    else:
        try:
            menu = fetch(
                SENATE_MENU.format(c=congress, s=session),
                lambda b: b"<vote_summary" in b,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(menu)
        except (OSError, ValueError, http.client.HTTPException) as e:
            if not path.exists():
                raise
            saved = date.fromtimestamp(path.stat().st_mtime)
            print(
                f"senate.gov unreachable ({e}); using the {congress}-{session} "
                f"vote list saved {saved}. Newer Senate votes are left out.",
                file=sys.stderr,
            )
            menu = path.read_bytes()
    root = ET.fromstring(menu)
    return [
        {
            "number": int(v.findtext("vote_number")),
            "issue": " ".join((v.findtext("issue") or "").split()),
            "title": " ".join((v.findtext("title") or "").split()),
        }
        for v in root.iter("vote")
    ]


def classify_senate(entry):
    """('bill', (type, number)) | ('nomination', 'PN12-33') | None."""
    issue = entry["issue"].replace(".", "").replace(" ", "").upper()
    if NOMINATION.match(issue):
        return "nomination", issue
    m = BILL.match(issue)
    if m and not any(w in entry["title"].lower() for w in NOT_THE_BILL):
        return "bill", (m.group(1).lower(), m.group(2))
    return None


def senate_url(congress, session, number):
    return SENATE_URL.format(c=congress, s=session, n=number)


# --- House ---


def house_url(congress, session, number):
    return HOUSE_URL.format(year=session_year(congress, session), n=number)


def house_roll_numbers(congress, session, api_key):
    """1..N from congress.gov, plus any newer rolls the Clerk has already posted."""
    count = api(f"house-vote/{congress}/{session}", api_key, limit=1)["pagination"][
        "count"
    ]
    n = count
    while True:
        try:
            fetch(house_url(congress, session, n + 1), is_house_roll, tries=1)
        except (ValueError, urllib.error.HTTPError):
            break
        n += 1
    return list(range(1, n + 1))


def classify_house(meta):
    m = BILL.match(meta["legis"].replace(".", "").replace(" ", "").upper())
    return ("bill", (m.group(1).lower(), m.group(2))) if m else None


# --- Bills and members (congress.gov) ---


def bill_label(btype, number):
    return f"{LABELS[btype]} {number}"


def bill_info(congress, btype, number, api_key):
    """Title and CRS policy area. Cached once CRS has assigned an area."""
    path = CACHE / "bills" / f"{congress}-{btype}-{number}.json"
    if path.exists():
        return json.loads(path.read_text())
    try:
        bill = api(f"bill/{congress}/{btype}/{number}", api_key)["bill"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        bill = {}
    area = (bill.get("policyArea") or {}).get("name")
    info = {"title": bill.get("title", ""), "area": area or "Not yet categorized"}
    if area:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(info))
    return info


def member_details(congress, api_key):
    """bioguide id -> first, last, state, district for everyone in one Congress."""
    out, offset = {}, 0
    while True:
        page = api(f"member/congress/{congress}", api_key, limit=250, offset=offset)
        for m in page["members"]:
            last, _, first = m.get("name", "").partition(", ")
            out[m["bioguideId"]] = {
                "first": first.strip(),
                "last": last.strip(),
                "state": STATE_CODES.get(m.get("state"), m.get("state")),
                "party": PARTIES.get(
                    m.get("partyName"), (m.get("partyName") or "?")[0]
                ),
                "district": m.get("district"),
            }
        offset += 250
        if offset >= page["pagination"]["count"]:
            return out


TERRITORIES = {"DC", "PR", "GU", "AS", "VI", "MP"}


def member(bioguide, api_key):
    """One member's name, state, district and whether they serve in the House now.

    Used where the paged member lists fall short: they can repeat some members
    and skip others between pages."""
    m = api(f"member/{bioguide}", api_key)["member"]
    term = max(m.get("terms", [{}]), key=lambda t: t.get("startYear", 0))
    last, _, first = m.get("invertedOrderName", "").partition(", ")
    return {
        "first": first.strip() or m.get("firstName", ""),
        "last": last.strip() or m.get("lastName", ""),
        "state": term.get("stateCode") or STATE_CODES.get(m.get("state"), "XX"),
        "district": term.get("district"),
        "current": bool(m.get("currentMember"))
        and term.get("chamber") == "House of Representatives"
        and not term.get("endYear"),
    }
