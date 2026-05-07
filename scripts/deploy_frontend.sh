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
# Configure via env (or edit the defaults below):
#   SUPABASE_URL                 — your Supabase project URL
#   SUPABASE_PUBLISHABLE_KEY     — sb_publishable_… (safe in browser)
#   NEXTAUTH_SECRET              — auto-generated if unset
#
# Run from the repo root:
#     export SUPABASE_URL=https://<project>.supabase.co
#     export SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
#     bash scripts/deploy_frontend.sh

set -euo pipefail

cd "$(dirname "$0")/../frontend"

if ! command -v vercel >/dev/null; then
  echo "→ Vercel CLI not found; using npx."
  VERCEL="npx vercel"
else
  VERCEL="vercel"
fi

# Required env — fail fast if not provided.
: "${SUPABASE_URL:?Set SUPABASE_URL (e.g. https://<ref>.supabase.co)}"
: "${SUPABASE_PUBLISHABLE_KEY:?Set SUPABASE_PUBLISHABLE_KEY (sb_publishable_…)}"
NEXTAUTH_SECRET="${NEXTAUTH_SECRET:-$(openssl rand -hex 32)}"

# 1. Link
$VERCEL link --yes 2>/dev/null || $VERCEL link

# 2. Push env vars (production scope).
push() {
  local key=$1; local val=$2
  printf '%s' "$val" | $VERCEL env add "$key" production --force >/dev/null 2>&1 || \
    printf '%s' "$val" | $VERCEL env add "$key" production >/dev/null
}

push NEXT_PUBLIC_SUPABASE_URL      "$SUPABASE_URL"
push NEXT_PUBLIC_SUPABASE_ANON_KEY "$SUPABASE_PUBLISHABLE_KEY"
push NEXTAUTH_SECRET               "$NEXTAUTH_SECRET"
echo "→ Production env vars pushed."

# 3. Deploy.
$VERCEL deploy --prod
