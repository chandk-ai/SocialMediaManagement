#!/usr/bin/env bash
# Push the canonical backend URL into Vercel + redeploy the frontend.
# Run from repo root.
set -euo pipefail

BACKEND="${BACKEND_URL:-https://smms-api.onrender.com}"
SUPA_URL="${SUPABASE_URL:-https://ukaulrxhegrqyfrirnkb.supabase.co}"
SUPA_ANON="${SUPABASE_PUBLISHABLE_KEY:?Set SUPABASE_PUBLISHABLE_KEY (sb_publishable_…)}"
NEXTAUTH_SECRET="${NEXTAUTH_SECRET:-$(openssl rand -hex 32)}"

cd "$(dirname "$0")/../frontend"

if ! command -v vercel >/dev/null; then
  VERCEL="npx vercel"
else
  VERCEL="vercel"
fi

push() {
  local key=$1 val=$2
  printf '%s' "$val" | $VERCEL env add "$key" production --force >/dev/null 2>&1 || \
    printf '%s' "$val" | $VERCEL env add "$key" production >/dev/null
}

push BACKEND_URL                    "$BACKEND"
push NEXT_PUBLIC_API_URL            "$BACKEND"
push NEXT_PUBLIC_SUPABASE_URL       "$SUPA_URL"
push NEXT_PUBLIC_SUPABASE_ANON_KEY  "$SUPA_ANON"
push NEXTAUTH_SECRET                "$NEXTAUTH_SECRET"

echo "→ Env vars pushed:"
echo "    BACKEND_URL                    = $BACKEND"
echo "    NEXT_PUBLIC_API_URL            = $BACKEND"
echo "    NEXT_PUBLIC_SUPABASE_URL       = $SUPA_URL"
echo "    NEXT_PUBLIC_SUPABASE_ANON_KEY  = sb_publishable_********"
echo "    NEXTAUTH_SECRET                = (generated)"
echo
echo "→ Triggering a fresh build (no cache)..."
$VERCEL deploy --prod --force
