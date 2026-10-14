# Voter info site

Nonpartisan reference showing how every member of Congress voted, grouped by issue. Every vote links to the official roll call record. Live on GitHub Pages from `docs/` on `main`.

## Non-negotiables

- **Neutrality is the product.** Report votes and official text only. No evaluative language, no advocacy-group labels ("voted with working people"), no inferred positions.
- **Votes come from official records, never an LLM.** senate.gov roll-call XML for the Senate, clerk.house.gov XML for the House; congress.gov (CRS) for bill titles, policy areas, member names and districts.
- **Issues are CRS policy areas** (32 terms, exactly one per bill). Don't hand-curate topic rules; that's an editorial choice we deliberately dropped.
- **UI stays color-neutral.** Yea/Aye = filled square, Nay/No = hollow square, Present/Not voting = dash, all in ink. Never green/red or blue/red. Show the official vote wording as recorded (House uses Aye/No on recorded votes).

## How data is built

`python record.py` (no args = sessions 117-1 through 119-2) rebuilds everything:

- **Senate:** `sources.senate_menu()` lists each session's votes. `classify_senate()` keeps bills and nominations; treaties and procedural votes are skipped, and votes whose title mentions executive session/confirmation/nomination are not attributed to the pending bill in `issue`.
- **House:** congress.gov `house-vote` gives the roll count per session; the Clerk's XML is then fetched for 1..N (plus any newer rolls). Missing rolls return HTTP 200 with a tiny `<xml>Error sanitizing…</xml>` body, so validity is checked by content, never status. `classify_house()` keeps votes whose `legis-num` is a bill; quorum calls, journal, adjournment, Speaker elections are skipped.
- Roll calls download in parallel (4 workers) into `data/cache/` as gzip and are cached forever (recorded votes never change). Bill info is cached in `data/cache/bills/` only once CRS has assigned a policy area; uncategorized bills are re-checked every run.
- Each roll call is parsed once for all members (`votes.parse_senate`, `votes.parse_house`). House XML has last names only; `name_representatives()` adds full names and districts from congress.gov.

Output, `docs/data/` (wiped and rewritten each run; always build every published session):

- `members.json`: directory (id, chamber, first, last, party, state, district, current)
- `votes/s119.json`, `votes/h119.json`: bills, nominations, vote details per chamber per Congress
- `casts/ID.json`: one member's votes, packed one character per roll call number per session: `{"119-1": "--Y-N…"}` where position *n* is roll call *n*. Codes: Y Yea, A Aye, N Nay, O No, P Present, V Not Voting, G Guilty, U Not Guilty, `-` no kept vote, `*` a rare wording stored verbatim under `"other"` (e.g. "Present, Giving Live Pair"). `record.pack()` writes it, `unpack()` in `member.html` reads it. Packed because these files change on every rebuild and the plain map was ~27 MB. Senators by LIS id (`S414`), representatives by bioguide id (`A000370`). Someone who served in both chambers has two entries.
- Senate impeachment trial votes are kept (the articles are an H.Res.), recorded as Guilty / Not Guilty.

Adding issues never adds files. Bill numbers restart each Congress, so keys include the Congress.

## In their words (pilot)

`python words.py [--no-media] [ID ...]` (no args = the 10 `PILOT` members) builds a second view on `member.html` (`&view=words[&show=media]`). No LLM anywhere in it.

- **On the floor:** GovInfo's Congressional Record, 119th Congress on. Each day's MODS (`www.govinfo.gov/metadata/pkg/CREC-…/mods.xml`) lists every section with speakers' bioguide ids; `speeches.turns()` cuts a speaker's paragraphs out of the section's HTML. A section that opens without a speaker's name continues the previous speech, so it's attributed only when MODS lists one speaker. Turns stop at "printed in the Record" (inserted letters, articles, bill text aren't the member's words). MODS sometimes lists a member who doesn't speak in the section; those yield no turns and are dropped.
- **Labels come from the Record's own markings:** Senate `<bullet>` = submitted, not spoken (legend printed in every issue); class `EXTENSIONS` = Extension of Remarks; subclasses `SSTATEMENTS*` = statements on introduced bills; everything else is "Senate/House proceedings", never "speech". The House marks inserted words only by typeface, which the HTML lacks. GovInfo prints "Statements on Introduced Bills" twice (umbrella + per bill); `dedupe()` drops the copy.
- **In the media:** two indexes, merged by normalized URL (`url_key`; each article records `via`). GDELT DOC API: `"First Last" sourcecountry:US`, one query over `MEDIA_DAYS` (30 in the pilot) split in half while it hits the 250 cap, paced 10 s, one 90 s retry, then skipped for the run (`sources.Throttled`). Media Cloud (`MEDIACLOUD_API_KEY` in `.env`; skipped if unset): `search/story-list` over its U.S. National and U.S. State & Local collections (`MEDIACLOUD_COLLECTIONS`), paced 7 s (10/min limit; quota 10,000 requests/week, 1,000 stories per request); `mediacloud_query` puts the title-or-state test in the query (`"Sen. Name"`, `"Name State"~3`), which cut namesakes ~3x; no article text from the API. Articles are checked only against the text a reader can see (`speeches.page_text` skips `display:none`/`visibility:hidden`, the `hidden` attribute, and whole class names like `subscriber-only`/`paywall`, never on `<html>`/`<body>`; not `aria-hidden`, which is visible. Sites like dailyrepublic.com send the full article in a hidden `subscriber-only` div, and a match there can't be checked by a reader. Raw pages are cached gzipped in `data/raw/media/*.html.gz` so extraction rules can change without refetching), one request per second per site. Every article either returns is kept in `data/media/ID.json` (committed, not published) because GDELT only searches back three months. Each article is fetched (honest UA, robots.txt honored; snapshots in `data/raw/media/`, never published) and kept only if it names the member with their chamber title or state nearby (`names_member`). Unreadable articles are kept only if the headline has the last name, marked unchecked. We never choose or rank outlets.
- **Comparison:** an article "quotes the Record" if it shares 12+ consecutive words with the member's speeches from the 120 days before (`speeches.Index`; punctuation, case, apostrophes ignored). 12-word runs used by 3+ members are stock phrasing and don't count. Runs more than half capitalized words are names (usually bill titles) and don't count (`is_name`). The UI says "Same wording as the Record", not "quotes": some matches are the member's own press-release language. Identical headlines (wire stories, often 100+ sites) are shown once with the other sites folded under them. Full text is published only for quoted sections (`docs/data/words/text/ID/GRANULE.json`); the rest link to GovInfo. Spans are word positions; `words.js` uses the same word regex as `speeches.WORD`.
- **Outlet ratings are always someone else's, attributed, ink only.** Raters live in `words.RATERS`; only those in `PUBLISHED_RATERS` are written to `outlets.json`, and the page shows no rating UI when none are. It's empty for now: Robertson et al. 2018 partisan audience scores (`sources.robertson_scores`) are "research purposes only", so not published until the authors agree; AllSides blocks scripts, waiting on their API. Never bin, relabel or average ratings.
- GDELT answers over-eager clients with a plain-text notice (HTTP 200) or 429; on 2026-10-13 it throttled a residential IP for hours after a handful of queries plus retries. Never retry it quickly. `--no-media` rebuilds without either index.

## Files

- `words.py`: builds `docs/data/words/` (In their words); `speeches.py`: Record turns and quote matching; CLI: `python speeches.py CREC-2026-09-30 CREC-2026-09-30-pt1-PgS5192 "Mr. PADILLA"`
- `docs/words.js`: the In their words view, loaded by `member.html`
- `votes.py`: parse Senate/House roll-call XML; CLI: `python votes.py senate 119-1 644 S414`, `python votes.py house 119-1 190 A000370`
- `sources.py`: fetching with retries, cache, classification, congress.gov lookups
- `record.py`: builds `docs/data/`
- `spinner.py`: stderr progress spinner, silent when not a TTY
- `docs/index.html`: searchable directory, filter by chamber, former members toggle
- `docs/member.html?id=S414[&tab=nominations][&issue=Health]`: one member's record; vote groups render lazily on open; Nominations tab only appears for senators
- `docs/senator.html`: redirect to `member.html` for old links
- `docs/states.js`: state/party names and the `role()` label shared by both pages
- `docs/site.css`: color tokens (light/dark), base type, site nav; every page links it
- Tip jar: every page ends with the same `<section class="tip">` linking ko-fi.com/broaden1202; the button is Ko-fi's official image, saved as `docs/kofi.png` so pages make no third-party requests
- `docs/glossary.html`: plain-English definitions of terms in vote titles, each with an `id` anchor (`glossary.html#cloture`)
- `docs/how-it-works.html`: how elections work and how Congress votes, linking only to official sources (.gov) that have been checked to load
- Definitions and process descriptions must match official sources (senate.gov, house.gov, usa.gov, archives.gov, CRS). Verify before adding or changing one.
- `voter_slice.py`: original LLM experiment (Tavily + DeepSeek). Not used by the site. Known issues: unbounded `source_index`, quotes not verified verbatim, advocacy framing leaks through. Don't build on it without fixing those.

## Commands

```bash
source .venv/bin/activate
set -a; source .env; set +a          # CONGRESS_API_KEY (+ TAVILY/DEEPSEEK for voter_slice.py)
python record.py                      # first full run is long; cached runs are fast
python -m http.server -d docs 8000    # preview; pages need HTTP, not file://
.venv/bin/pre-commit run --all-files  # lint/format (ruff runs via the hook, not the venv)
```

## Git workflow

- `main` is protected by a ruleset: no direct pushes. Always `git switch -c type/desc` from an up-to-date `main`, and check `git branch --show-current` before committing.
- Conventional Commits, enforced by a hook. Allowed types include `data` and `prompt`.
- PR then `gh pr merge --squash --delete-branch`.
- Commit `docs/data/` with code changes; it's the published record and its history matters. The large-file hook skips `docs/data/` and `data/media/`.

## Limits and gotchas

- congress.gov API: 5,000 requests/hour. A cold build makes roughly one call per bill; cached runs make a few dozen.
- senate.gov's firewall (Akamai) answers sustained traffic with temporary 403s. `sources.fetch` paces senate.gov to one request per second, retries 403/429/5xx and network errors with backoff (5s up to 2 min), and the Senate download runs single-threaded. Don't raise the concurrency.
- clerk.house.gov returns HTTP 200 with a tiny error body for missing rolls; validity is checked by content (`is_house_roll`, `is_senate_roll`).
- congress.gov's website (not the API) blocks scripts; vote.gov blocks automated link checks.
- congress.gov's paged member lists (`/member`, `/member/congress/N`) can repeat some members and skip others between pages (539 reported, 533 returned). Never decide membership from them alone; `sources.member()` (one request per member) is reliable.
- **Who is current:** voting members are current if they're in their chamber's latest roll call (everyone sitting is listed, even as Not Voting). Delegates from D.C. and the territories vote only on amendments in the Committee of the Whole, so they're checked individually on congress.gov. The Clerk's XML gives delegates the state "XX"; the real territory comes from congress.gov.
- Senate vote lists are saved to `data/cache/menus/`; finished sessions are read from there, and the current session falls back to the saved copy if senate.gov blocks requests (a VPN's shared IP gets blocked quickly).
- Tally bars scale to 100 seats (Senate) or 435 (House). Requirements: Senate cloture `3/5` = 60; `2/3` = two-thirds of those voting (House suspensions are `2/3`); otherwise a simple majority of those voting.
- DeepSeek retired the `deepseek-chat` model name (2026-07-24); `voter_slice.py` uses `deepseek-flash`.
- `.env` holds keys and is gitignored. The commit hook only recognizes `tvly-` and `sk-` key shapes; the congress.gov key has no prefix.
- Hook versions in `.pre-commit-config.yaml` are from 2024; update deliberately with `pre-commit autoupdate` in a `chore:` commit.

## Not built yet

- Treaty votes, quorum calls, procedural votes not tied to a bill
- In their words for all members (pilot covers 10), a weekly GitHub Action for it, AllSides ratings
- Statements and positions from candidates' own sites (LLM path: needs verbatim-quote checks in code and typed claims)
- A fuller methodology page
- Linking one person's Senate and House records
