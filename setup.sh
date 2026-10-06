#!/usr/bin/env bash
# Idempotent dev setup for the voter info project. Safe to re-run.
set -euo pipefail

cd "$(dirname "$0")"
die() { echo "setup: $*" >&2; exit 1; }

# Prereqs
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "need Python 3.10+"
python3 -c 'import ensurepip' 2>/dev/null || die "venv support missing (Debian/Ubuntu: apt install python3-venv)"
command -v git >/dev/null || die "git not installed"
git config user.email >/dev/null || die "set git identity: git config --global user.name/user.email"

[ -d .git ] || git init -b main

# Virtualenv + deps; lockfile pins whatever first worked
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
if [ -f requirements.lock ]; then
  .venv/bin/pip install -q -r requirements.lock
else
  .venv/bin/pip install -q -r requirements.txt
  .venv/bin/pip freeze > requirements.lock
fi

# Secrets file (never committed)
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Add your API keys."
fi

# Hooks (config lives in .pre-commit-config.yaml, versioned with the repo)
.venv/bin/pre-commit install -t pre-commit -t commit-msg >/dev/null

# First commit, only on a fresh repo. Fails loudly instead of silently.
if ! git rev-parse HEAD >/dev/null 2>&1; then
  git add -A   # hooks only see staged files
  # First pass may auto-fix files; the second must be clean
  .venv/bin/pre-commit run --all-files >/dev/null \
    || { git add -A; .venv/bin/pre-commit run --all-files; } \
    || die "hooks failing; fix the files above, then re-run"
  git add -A
  git commit -q -m "chore: initial project scaffold"
fi

cat <<'EOF'

Done. To work:
  source .venv/bin/activate
  set -a; source .env; set +a
  python voter_slice.py
EOF
