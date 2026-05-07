# Deploying SMMS

Three pieces deploy independently:

| Component | Recommended target | Why |
|---|---|---|
| **Frontend** (Next.js) | **Vercel** | Native Next.js host; preview deploys per PR; built-in CDN |
| **Backend API + workers** (FastAPI + Celery) | **Render**, **Fly.io**, or **Railway** | First-class Docker support, managed Redis, generous free/starter tier |
| **Database + Auth + Storage** | **Supabase** | What we already build against |

Pick one target per piece. Configs for all three backend hosts live in `infra/` so you can swap.

---

## Quickest path (Vercel + Render + Supabase)

```bash
# 1. Create a Supabase project (UI or CLI)
supabase projects create smms-prod
supabase db push                         # runs the migrations from backend/app/infrastructure/db/migrations/

# 2. Deploy backend on Render — one-shot blueprint
render blueprint launch infra/render.yaml
# (or push the repo to GitHub and click "New Blueprint" in Render's UI)

# 3. Deploy frontend on Vercel
cd frontend
vercel --prod                            # follow the prompts
# Set env vars from .env.example in the Vercel dashboard:
#   NEXT_PUBLIC_API_URL=https://smms-api.onrender.com
#   NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
#   NEXT_PUBLIC_SUPABASE_ANON_KEY=...
#   NEXTAUTH_SECRET=$(openssl rand -hex 32)
#   OKTA_CLIENT_ID / OKTA_CLIENT_SECRET (or Supabase Auth equivalents)
```

That gets you a fully working production deployment in ~15 minutes.

---

## Alternative — Fly.io for the backend

```bash
cd backend
fly launch --copy-config --config ../infra/fly.toml --no-deploy
fly secrets set \
    SUPABASE_URL=... \
    SUPABASE_SERVICE_ROLE_KEY=... \
    SUPABASE_JWT_SECRET=... \
    SUPABASE_POSTGRES_CONNECTION_STRING=... \
    SEC_TOKEN_VAULT_MASTER_KEY=$(openssl rand -hex 32) \
    LLM_ANTHROPIC_API_KEY=...
fly deploy

# Worker + beat as separate apps
fly launch --name smms-worker --copy-config --config ../infra/fly.toml --no-deploy
# ... edit fly.toml processes section to run celery worker
fly deploy
```

## Alternative — Railway

```bash
cd backend
railway link               # or `railway init` for new project
railway up                 # deploys with infra/railway.toml settings
railway add redis          # provision managed Redis
```

## Alternative — Cloudflare (frontend only)

```bash
cd frontend
npx wrangler pages deploy .next
```

---

## Production checklist before flipping the DNS

- [ ] `SEC_TOKEN_VAULT_MASTER_KEY` is a real 32-byte random value (not the dev placeholder)
- [ ] All `_CLIENT_SECRET` env vars are set to real values from the platform consoles
- [ ] Supabase RLS policies are enabled (`alter table … enable row level security` should already be there from the migration; double-check in the Supabase dashboard's "Authentication → Policies" view)
- [ ] CORS allowed origins (`SEC_CORS_ORIGINS`) lists exactly your frontend domain
- [ ] HTTPS-only — Vercel + Render + Fly all do this by default; verify with a `curl -I http://`
- [ ] Sentry / Honeycomb DSN set if you're using one
- [ ] First admin user provisioned via Supabase Admin API (see `backend/app/infrastructure/db/migrations/README.md`)
- [ ] Webhook URLs registered with WhatsApp / Telegram / Instagram (see `docs/TRIGGERS.md`)
- [ ] First workflow created and one publish round verified manually
- [ ] Set up uptime monitoring on `https://your-host/api/v1/health` and `https://your-host/`

---

## Rolling back

Render + Fly + Vercel all keep previous deploys you can roll back to with one click in their respective dashboards. For Supabase data changes, point-in-time recovery is on by default for the Pro plan.
