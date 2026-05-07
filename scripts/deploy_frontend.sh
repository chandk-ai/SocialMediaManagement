#!/usr/bin/env bash
# One-shot Vercel deploy for the frontend.
#
# Pre-reqs:
#   - Node 20+
#   - npm i -g vercel    (or use `npx vercel` everywhere below)
#   - You're logged in: `vercel login`
#
# What this does:
#   1. Links the local /frontend directory to a Vercel project
#      (creates one if it doesn't exist — accept the prompts).
#   2. Pushes production env vars (Supabase URL/key + a generated NEXTAUTH_SECRET).
#   3. Builds and deploys to production.
#
# Run from the repo root:
#     bash scripts/deploy_frontend.sh

set -euo pipefail

cd "$(dirname "$0")/../frontend"

if ! command -v vercel >/dev/null; then
  echo "→ Vercel CLI not found; using npx."
  VERCEL="npx vercel"
else
  VERCEL="vercel"
fi

# 1. Link
$VERCEL link --yes 2>/dev/null || $VERCEL link

# 2. Push env vars (production scope).
SUPA_URL="https://ukaulrxhegrqyfrirnkb.supabase.co"
SUPA_ANON="sb_publishable_FnQFGibT0kNMhZ5lFUA_7A_MUXpAD51"
NEXTAUTH_SECRET="${NEXTAUTH_SECRET:-$(openssl rand -hex 32)}"

push() {
  local key=$1; local val=$2
  # `--force` overwrites if the key already exists.
  printf '%s' "$val" | $VERCEL env add "$key" production --force >/dev/null 2>&1 || \
    printf '%s' "$val" | $VERCEL env add "$key" production >/dev/null
}

push NEXT_PUBLIC_SUPABASE_URL      "$SUPA_URL"
push NEXT_PUBLIC_SUPABASE_ANON_KEY "$SUPA_ANON"
push NEXTAUTH_SECRET               "$NEXTAUTH_SECRET"
echo "→ Production env vars pushed."

# 3. Deploy.
$VERCEL deploy --prod
