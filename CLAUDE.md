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

## Files

- `votes.py`: parse Senate/House roll-call XML; CLI: `python votes.py senate 119-1 644 S414`, `python votes.py house 119-1 190 A000370`
- `sources.py`: fetching with retries, cache, classification, congress.gov lookups
- `record.py`: builds `docs/data/`
- `spinner.py`: stderr progress spinner, silent when not a TTY
- `docs/index.html`: searchable directory, filter by chamber, former members toggle
- `docs/member.html?id=S414[&tab=nominations][&issue=Health]`: one member's record; vote groups render lazily on open; Nominations tab only appears for senators
- `docs/senator.html`: redirect to `member.html` for old links
- `docs/states.js`: state/party names and the `role()` label shared by both pages
- `docs/site.css`: color tokens (light/dark), base type, site nav; every page links it
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
- Commit `docs/data/` with code changes; it's the published record and its history matters. The large-file hook skips `docs/data/`.

## Limits and gotchas

- congress.gov API: 5,000 requests/hour. A cold build makes roughly one call per bill; cached runs make a few dozen.
- senate.gov's firewall (Akamai) answers sustained traffic with temporary 403s. `sources.fetch` paces senate.gov to one request per second, retries 403/429/5xx and network errors with backoff (5s up to 2 min), and the Senate download runs single-threaded. Don't raise the concurrency.
- clerk.house.gov returns HTTP 200 with a tiny error body for missing rolls; validity is checked by content (`is_house_roll`, `is_senate_roll`).
- congress.gov's website (not the API) blocks scripts; vote.gov blocks automated link checks.
- Tally bars scale to 100 seats (Senate) or 435 (House). Requirements: Senate cloture `3/5` = 60; `2/3` = two-thirds of those voting (House suspensions are `2/3`); otherwise a simple majority of those voting.
- DeepSeek retired the `deepseek-chat` model name (2026-07-24); `voter_slice.py` uses `deepseek-flash`.
- `.env` holds keys and is gitignored. The commit hook only recognizes `tvly-` and `sk-` key shapes; the congress.gov key has no prefix.
- Hook versions in `.pre-commit-config.yaml` are from 2024; update deliberately with `pre-commit autoupdate` in a `chore:` commit.

## Not built yet

- Treaty votes, quorum calls, procedural votes not tied to a bill
- Statements and positions from candidates' own sites (LLM path: needs verbatim-quote checks in code and typed claims)
- A fuller methodology page
- Linking one person's Senate and House records
