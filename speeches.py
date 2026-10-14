"""Split Congressional Record text into speakers' turns, and find news articles that
quote them word for word.

CLI: python speeches.py CREC-2026-09-30 CREC-2026-09-30-pt1-PgS5192 "Mr. PADILLA"
"""

import html
import re
import sys
from html.parser import HTMLParser

# A paragraph that starts a new speaker's turn: "Mr. PADILLA.", "Ms. OCASIO-CORTEZ.",
# "Mr. ROGERS of Alabama.", "Mr. McCONNELL.", "The PRESIDING OFFICER.",
# "The SPEAKER pro tempore (Mr. Smith)."
SPEAKER = re.compile(
    r"^(?:(?:Mr|Ms|Mrs|Miss|Dr)\. (?:[A-Z][a-z]{0,2})?[A-Z][A-Z'\-]+(?: [A-Z][A-Z'\-]+)*"
    r"(?: of [A-Z][a-z]+(?: [A-Z][a-z]+)*)?"
    r"|The (?:[A-Z]{2,} ?)+(?:pro tempore)?(?: \([^)]*\))?)\. "
)
# Senate statements submitted for the Record instead of spoken are marked with a
# bullet before the speaker's name.
BULLET = "<bullet>"
# Material a member asks to have printed (letters, articles, bill text) is not their
# own words; their turn ends where it begins.
INSERTION = re.compile(r"printed in the Record", re.I)
PAGE = re.compile(r"^\s*\[+Page[s]? [A-Z]?\d+.*\]+\s*$")


def paragraphs(page):
    """The Record's HTML rendition -> [(kind, text)], kind "p" for a paragraph and "h"
    for a centered heading. Paragraphs start with a two-space indent; continuation
    lines have none."""
    m = re.search(r"<pre>(.*?)</pre>", page.decode("utf-8", "replace"), re.S)
    text = html.unescape(re.sub(r"<(?!bullet>)[^>]+>", "", m.group(1) if m else ""))
    out, cur = [], None
    for line in text.splitlines():
        if not line.strip() or PAGE.match(line):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if line.startswith(BULLET) or indent in (1, 2):
            cur = ["p", line.strip()]
            out.append(cur)
        elif indent >= 4:
            out.append(["h", line.strip()])
            cur = None
        elif cur:
            cur[1] += " " + line.strip()
    return [(k, " ".join(t.split())) for k, t in out]


def turns(page, name, continues=False):
    """One speaker's turns in a section: [{"paras": [...], "submitted": bool}].

    `name` is the speaker as printed and as GovInfo lists them ("Mr. PADILLA").
    `continues`: GovInfo lists them as the section's only speaker, so a section that
    opens without a speaker's name is their speech carrying on from the last one."""
    lead = f"{name}. "
    out, cur = [], None
    paras = paragraphs(page)
    first = next((t for k, t in paras if k == "p"), "")
    if continues and not SPEAKER.match(first.removeprefix(BULLET).strip()):
        cur = {"paras": [], "submitted": first.startswith(BULLET)}
        out.append(cur)
    for kind, text in paras:
        bullet = text.startswith(BULLET)
        body = text.removeprefix(BULLET).strip()
        if (kind == "h" and cur and cur["paras"]) or SPEAKER.match(body):
            cur = None
        if kind == "p" and body.startswith(lead):
            cur = {"paras": [], "submitted": bullet}
            out.append(cur)
            body = body[len(lead) :]
        if cur is not None and kind == "p":
            cur["paras"].append(body)
            if INSERTION.search(body):
                cur = None
    return [t for t in out if t["paras"]]


# --- Matching ---

WORD = re.compile(r"[^\W_][\w'’]*")
N = 12  # consecutive words that must match exactly


def words(text):
    """Normalized words: lowercase, apostrophes dropped. Punctuation, dashes and
    quote marks never break or make a match."""
    return [re.sub(r"['’]", "", w).lower() for w in WORD.findall(text)]


def capitalized(text):
    """Whether each word (as `words` counts them) starts with a capital letter."""
    return [w[0].isupper() for w in WORD.findall(text)]


def grams(tokens):
    return {tuple(tokens[i : i + N]) for i in range(len(tokens) - N + 1)}


class Index:
    """Every run of N words in a set of speeches, for finding quotes."""

    def __init__(self, skip=frozenset()):
        self.skip = skip  # runs too common to mean anything (procedural phrases)
        self.at = {}  # run -> [(speech key, word position)]

    def add(self, key, tokens):
        for i in range(len(tokens) - N + 1):
            g = tuple(tokens[i : i + N])
            if g not in self.skip:
                self.at.setdefault(g, []).append((key, i))

    def quotes(self, text, keys=None):
        """Spans of the indexed speeches that appear word for word in `text`:
        {speech key: [(start, end), ...]} in speech word positions."""
        tokens = words(text)
        hits = {}
        for i in range(len(tokens) - N + 1):
            for key, pos in self.at.get(tuple(tokens[i : i + N]), ()):
                if keys is None or key in keys:
                    hits.setdefault(key, set()).add(pos)
        out = {}
        for key, starts in hits.items():
            spans = []
            for s in sorted(starts):
                if spans and s <= spans[-1][1]:
                    spans[-1][1] = max(spans[-1][1], s + N)
                else:
                    spans.append([s, s + N])
            out[key] = [tuple(x) for x in spans]
        return out


# Never shown to a reader: code, and anything the page hides. (aria-hidden is not
# hiding: it keeps text from screen readers, not from the screen.) News sites often send
# the whole article and hide most of it from non-subscribers; a match there is one
# a reader can't check by following the link, so it doesn't count.
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "head"}
# One class name that marks a subscribers-only or paywalled part ("subscriber-only",
# "paywall", "premium-content"). Whole class names only: a page-level class like
# "not_premium_user" says nothing about what's on screen.
HIDDEN_CLASS = re.compile(
    r"^(?:[\w-]*(?:subscriber|paywall|regwall)[\w-]*|premium-content|locked-content)$",
    re.I,
)
HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
HIDDEN_WORDS = 50
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}  # fmt: skip


class _Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], []  # skip: [tag, depth, hidden from readers?]
        self.hidden_words = 0

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        if self.skip:
            if tag == self.skip[0]:
                self.skip[1] += 1
            return
        a = dict(attrs)
        hidden = tag not in ("html", "body") and (
            "hidden" in a
            or HIDDEN_STYLE.search(a.get("style") or "")
            or any(HIDDEN_CLASS.match(c) for c in (a.get("class") or "").split())
        )
        if tag in SKIP_TAGS or hidden:
            self.skip = [tag, 1, tag not in SKIP_TAGS]

    def handle_endtag(self, tag):
        if self.skip and tag == self.skip[0]:
            self.skip[1] -= 1
            if not self.skip[1]:
                self.skip = []

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)
        elif self.skip[2]:
            self.hidden_words += len(data.split())


def page_text(page):
    """The text a reader sees on an HTML page, and whether a real amount of text
    (50+ words) was hidden, usually a subscribers-only part: (text, partial)."""
    p = _Visible()
    p.feed(page.decode("utf-8", "replace"))
    p.close()
    return " ".join(" ".join(p.out).split()), p.hidden_words >= HIDDEN_WORDS


def page_title(page):
    """The outlet's own headline: og:title, else <title>."""
    s = page[:300_000].decode("utf-8", "replace")
    for pat in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title',
        r"<title[^>]*>(.*?)</title>",
    ):
        m = re.search(pat, s, re.S | re.I)
        if m:
            return " ".join(html.unescape(m.group(1)).split())
    return None


if __name__ == "__main__":
    import sources

    pkg, granule, name = sys.argv[1:4]
    page = sources.cached(
        sources.CREC_HTML.format(pkg=pkg, granule=granule), sources.is_crec_html
    )
    for t in turns(page, name):
        print("[submitted, not spoken]" if t["submitted"] else "[floor]")
        for p in t["paras"]:
            print("  " + p)
