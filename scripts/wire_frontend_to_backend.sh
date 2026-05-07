#!/usr/bin/env bash
# Push the canonical backend URL into Vercel (production scope) + trigger a
# fresh deploy via git push. Doesn't run `vercel deploy` directly because
# it conflicts with monorepo Root-Directory config (path gets doubled).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="${BACKEND_URL:-https://smms-api.onrender.com}"
SUPA_URL="${SUPABASE_URL:-https://ukaulrxhegrqyfrirnkb.supabase.co}"
SUPA_ANON="${SUPABASE_PUBLISHABLE_KEY:?Set SUPABASE_PUBLISHABLE_KEY (sb_publishable_…)}"
NEXTAUTH_SECRET="${NEXTAUTH_SECRET:-$(openssl rand -hex 32)}"

cd "$REPO_ROOT/frontend"

if ! command -v vercel >/dev/null; then
  VERCEL="npx vercel"
else
  VERCEL="vercel"
fi

if [ ! -f .vercel/project.json ]; then
  echo "→ Linking to Vercel project (one-time)..."
  $VERCEL link --yes
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

cat <<EOF

→ Env vars pushed to Vercel (production):
    BACKEND_URL                    = $BACKEND
    NEXT_PUBLIC_API_URL            = $BACKEND
    NEXT_PUBLIC_SUPABASE_URL       = $SUPA_URL
    NEXT_PUBLIC_SUPABASE_ANON_KEY  = sb_publishable_********
    NEXTAUTH_SECRET                = (generated)

NEXT_PUBLIC_* values are inlined at BUILD time, so a fresh build is required
for the browser bundle to see them. Trigger one of:

  • git commit --allow-empty -m "Redeploy with new env vars" && git push
  • Vercel dashboard → Deployments → top → ⋯ → Redeploy → uncheck "Use Build Cache"

EOF

cd "$REPO_ROOT"
echo "→ Creating empty redeploy commit..."
git commit --allow-empty -m "Redeploy: pick up updated Vercel env vars"
git push
echo "→ Pushed. Vercel auto-deploy should start within ~10s."
