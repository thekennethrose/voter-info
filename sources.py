"""Fetching from senate.gov, clerk.house.gov, congress.gov, GovInfo and GDELT, with a
disk cache."""

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
import urllib.robotparser
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


def fetch(url, valid=lambda body: True, tries=len(BACKOFF) + 1, headers=None):
    """GET with pacing, and retries on throttling, server errors and garbage bodies."""
    host = urllib.parse.urlsplit(url).hostname
    req = urllib.request.Request(
        url, headers={"User-Agent": "voter-info/0.1", **(headers or {})}
    )
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


# --- Congressional Record (GovInfo) ---

GOVINFO = "https://api.govinfo.gov/published/{start}/{end}?collection=CREC&pageSize=1000&offsetMark=*&api_key={k}"
CREC_MODS = "https://www.govinfo.gov/metadata/pkg/{pkg}/mods.xml"
CREC_HTML = "https://www.govinfo.gov/content/pkg/{pkg}/html/{granule}.htm"
CREC_PAGE = "https://www.govinfo.gov/app/details/{pkg}/{granule}"
SENATORS = "https://www.senate.gov/legislative/LIS_MEMBER/cvc_member_data.xml"
MODS = "{http://www.loc.gov/mods/v3}"


def crec_issues(start, end, api_key):
    """Daily issues of the Record published between two ISO dates, oldest first."""
    url, out = GOVINFO.format(start=start, end=end, k=api_key), []
    while url:
        page = json.loads(fetch(url))
        out += [(p["packageId"], p["dateIssued"]) for p in page["packages"]]
        nxt = page.get("nextPage")
        url = nxt and f"{nxt}&api_key={api_key}"
    return sorted(out, key=lambda p: p[1])


def is_mods(body):
    return b"<mods" in body[:2000]


def is_crec_html(body):
    return b"<pre>" in body


def crec_granules(mods):
    """Every section of one day's Record that has a member speaking: id, title,
    class (SENATE / HOUSE / EXTENSIONS), subclass, and the speakers as
    (bioguide id, name as printed, e.g. "Mr. ROGERS of Alabama")."""
    root = ET.fromstring(mods)
    for item in root.iter(f"{MODS}relatedItem"):
        ext = item.find(f"{MODS}extension")
        if item.get("type") != "constituent" or ext is None:
            continue
        speakers = [
            (m.get("bioGuideId"), m.findtext(f"{MODS}name[@type='parsed']") or "")
            for m in ext.iter(f"{MODS}congMember")
            if m.get("role") == "SPEAKING" and m.get("bioGuideId")
        ]
        if speakers:
            yield {
                "granule": ext.findtext(f"{MODS}accessId"),
                "date": ext.findtext(f"{MODS}granuleDate"),
                "title": " ".join(
                    item.findtext(f"{MODS}titleInfo/{MODS}title").split()
                ),
                "class": ext.findtext(f"{MODS}granuleClass"),
                "subclass": ext.findtext(f"{MODS}subGranuleClass"),
                "speakers": speakers,
            }


def senate_bioguide():
    """Current senators' LIS id -> bioguide id, from senate.gov. Falls back to the
    saved copy if senate.gov is blocking us."""
    path = CACHE / "menus" / "senators.xml"
    try:
        body = fetch(SENATORS, lambda b: b"<senators" in b)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    except (OSError, ValueError, http.client.HTTPException):
        if not path.exists():
            raise
        body = path.read_bytes()
    return {
        s.get("lis_member_id"): s.findtext("bioguideId")
        for s in ET.fromstring(body).iter("senator")
    }


# --- News coverage (GDELT) ---

GDELT = (
    "https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist"
    "&maxrecords=250&format=json&sort=datedesc&startdatetime={start}&enddatetime={end}"
)
GDELT_MAX = 250
# GDELT asks for one request every 5 seconds and answers faster ones with a
# plain-text notice (HTTP 200) or a 429. In practice 6 seconds still drew 429s.
MIN_GAP["api.gdeltproject.org"] = 10.0
GDELT_COOLDOWN = 90


class Throttled(Exception):
    """GDELT is refusing requests; asking again only prolongs it."""


def gdelt(query, start, end):
    """Articles GDELT indexed between two datetimes (YYYYMMDDHHMMSS) that match.
    After a refusal, waits once and tries again; retrying faster keeps the block
    going."""
    url = GDELT.format(q=urllib.parse.quote(query), start=start, end=end)
    for attempt in range(2):
        try:
            body = fetch(url, lambda b: b.lstrip()[:1] == b"{", tries=1)
            return json.loads(body or b"{}").get("articles", [])
        except (ValueError, urllib.error.HTTPError) as e:
            if attempt:
                raise Throttled(str(e)) from e
            time.sleep(GDELT_COOLDOWN)


# --- News coverage (Media Cloud) ---

MEDIACLOUD = "https://search.mediacloud.org/api/{path}?{query}"
# Media Cloud allows at most 10 requests a minute.
MIN_GAP["search.mediacloud.org"] = 7.0


def mediacloud(path, api_key, **params):
    url = MEDIACLOUD.format(path=path, query=urllib.parse.urlencode(params))
    headers = {"Authorization": f"Token {api_key}", "Accept": "application/json"}
    return json.loads(fetch(url, headers=headers))


def mediacloud_stories(query, start, end, collections, api_key):
    """Every story in Media Cloud's collections matching the query between two ISO
    dates: url, title, publish_date, media_url, media_name."""
    out, token = [], None
    while True:
        params = {"q": query, "start": start, "end": end,
                  "cs": ",".join(str(c) for c in collections),
                  "platform": "onlinenews-mediacloud", "page_size": 1000}  # fmt: skip
        if token:
            params["pagination_token"] = token
        page = mediacloud("search/story-list", api_key, **params)
        out += page.get("stories", [])
        token = page.get("pagination_token")
        if not token:
            return out


ARTICLE_UA = "voter-info/0.1 (+https://github.com/thekennethrose/voter-info)"
_robots = {}
_robots_lock = threading.Lock()


def _allowed(url):
    """Honor each site's robots.txt."""
    parts = urllib.parse.urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}"
    with _robots_lock:
        rp = _robots.get(root)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                req = urllib.request.Request(
                    f"{root}/robots.txt", headers={"User-Agent": ARTICLE_UA}
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    rp.parse(r.read(500_000).decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as e:  # same rules as RobotFileParser.read
                rp.parse(
                    ["User-agent: *", "Disallow: /"] if e.code in (401, 403) else []
                )
            except (OSError, http.client.HTTPException, ValueError):
                rp.parse([])
            _robots[root] = rp
    return rp.can_fetch(ARTICLE_UA, url)


ARTICLE_GAP = 1.0  # seconds between requests to the same news site


def article(url):
    """A news article's headline and the text a reader can see ({"title", "text",
    "partial"}: partial if some was hidden, e.g. for subscribers), or None if the
    site refuses, blocks or fails. The page is saved compressed to data/raw/media/
    (never published) so text can be re-extracted without downloading again;
    failures are remembered too, so reruns don't hammer sites that block us."""
    import speeches

    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    path = Path("data/raw/media") / f"{key}.html.gz"
    failed = path.with_name(f"{key}.failed")
    if path.exists():
        body = gzip.decompress(path.read_bytes())
    elif failed.exists():
        return None
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = None
        try:
            if _allowed(url):
                host = urllib.parse.urlsplit(url).hostname
                MIN_GAP.setdefault(host, ARTICLE_GAP)
                _wait_turn(host)
                req = urllib.request.Request(url, headers={"User-Agent": ARTICLE_UA})
                with urllib.request.urlopen(req, timeout=15) as r:
                    if "html" in (r.headers.get("Content-Type") or ""):
                        body = r.read(4_000_000)
        except (OSError, http.client.HTTPException, ValueError):
            pass
        if not body:
            failed.touch()
            return None
        path.write_bytes(gzip.compress(body))
    text, partial = speeches.page_text(body)
    return {"title": speeches.page_title(body), "text": text, "partial": partial}


# --- Outlet ratings ---

ROBERTSON = "https://personalization.ccs.neu.edu/static/archive/bias_scores.tar.gz"


def robertson_scores():
    """domain -> partisan audience score, -1 (shared only by Democrats) to 1
    (shared only by Republicans). Robertson et al. 2018, from ~500K Twitter users
    matched to voter records."""
    import csv
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(cached(ROBERTSON)), mode="r:gz") as tar:
        f = tar.extractfile("bias_scores/bias_scores.csv")
        rows = csv.DictReader(io.TextIOWrapper(f, "utf-8"))
        return {r["domain"]: float(r["score"]) for r in rows if r["score"]}
