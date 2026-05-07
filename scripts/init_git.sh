#!/usr/bin/env bash
# One-shot: initialize git, commit, and (optionally) push to GitHub.
#
# Usage:
#   bash scripts/init_git.sh                                  # local commit only
#   bash scripts/init_git.sh git@github.com:you/smms.git      # commit + push
set -euo pipefail

REMOTE_URL="${1:-}"

# Belt-and-braces — clean up any half-finished init from previous runs.
if [ -d .git ]; then
  echo "→ .git already exists; aborting (delete it first if you want to reset)."
  exit 1
fi

git init -b main >/dev/null
git add -A

git -c user.email="${GIT_AUTHOR_EMAIL:-you@example.com}" \
    -c user.name="${GIT_AUTHOR_NAME:-You}" \
    commit -m "Initial commit: Social Media Management System v0.1.0

- 16 social platforms · 12 sources · 9 LLMs · 6 triggers · 6 review channels
  · 4 media generators · 3 engagement sources
- 4-agent orchestrator (Planner/Executor/Evaluator/Critique) on LangGraph
- Supabase persistence + Auth + Storage + Realtime, with RLS
- Multi-tenancy (shared / dedicated_db / dedicated_stack)
- Conditional fan-out targeting via natural-language directives
- Human-in-the-loop review through WhatsApp / Telegram / Instagram / email / Slack
- OAuth + PKCE for 8 platforms, per-(plugin, account) rate-limit governor,
  publish DLQ + retry, Celery Beat scheduler
- Next.js 14 portal — Dashboard / Analytics / Calendar / Workflows / Triggers
  / Reviews / Platforms / Sources / Posts / Audit / Settings / Onboarding"

echo "→ Local commit created."
if [ -n "$REMOTE_URL" ]; then
  git remote add origin "$REMOTE_URL"
  git push -u origin main
  echo "→ Pushed to $REMOTE_URL"
else
  cat <<'EOF'

Next steps:
  1. Create an empty GitHub repo (private):
     gh repo create your-org/smms --private --source=. --remote=origin
  2. Push:
     git push -u origin main
EOF
fi
