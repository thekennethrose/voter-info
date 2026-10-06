# Version control schema

## Branches
- `main`: always runnable. Never commit directly once the first slice works. Locally this is
  honor-system only; to enforce it, push to GitHub and turn on branch protection for `main`.
- Short-lived branches off `main`, named `type/short-desc`, merged by PR (squash) and deleted:
  `feat/candidate-index`, `fix/source-index-bounds`, `data/fec-ingest`, `prompt/neutrality-v2`, `chore/ci`.
- One branch per vertical slice or fix. If it lives longer than a few days, it's too big.

## Commits
Conventional Commits, enforced by the commit-msg hook:
`feat: ...`, `fix: ...`, `data: ...`, `prompt: ...`, `docs: ...`, `chore: ...`, `test: ...`, `refactor: ...`

Types `data` and `prompt` are non-standard but worth having here. The system prompt is the product's
neutrality guarantee, so changes to it should be easy to find in `git log --grep '^prompt'`.
Adding a type means adding it to the hook's `args` in `.pre-commit-config.yaml`, which lists every
allowed type. List them all: a list without `feat`/`fix` silently drops `chore`, `docs`, etc.

## Tags
Tag each working vertical slice: `v0.1.0` (one candidate, one issue), `v0.2.0` (many issues), etc.
Pre-1.0, bump minor for a new capability and patch for fixes.

## What goes in the repo
- Code, prompts (move `SYSTEM` into `prompts/`), schemas, tests, small golden fixtures
  (a saved source set plus the expected JSON shape) for regression-testing the summarizer.
- Not in the repo: `.env`, raw scraped pages, bulk voting-record dumps, model output. Those
  are regenerable; keep a fetch script in the repo instead of the data.

## Rules
- Secrets only in `.env`. The `no-api-keys` hook pattern-matches Tavily/DeepSeek key shapes as a
  backstop; add a pattern whenever a new provider's key enters the project.
- Tool versions: `requirements.lock` pins Python deps; hook revs pin linters. Upgrade either on
  purpose in a `chore:` commit (`pre-commit autoupdate`, delete and regenerate the lock), never as a side effect.
- Rotate any key that is ever committed, even briefly. Deleting the commit doesn't un-leak it.
- Prompt changes get a before/after run on the golden fixtures in the PR description.
