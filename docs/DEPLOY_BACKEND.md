# Deploy the backend to Render

The backend is FastAPI + 2 Celery processes (worker + beat). Render provisions all three plus a managed Redis from the **`render.yaml`** Blueprint at the repo root.

## 5-minute walkthrough

### 1. Make sure the latest is on GitHub

```bash
cd ~/Documents/Claude/Projects/SocialMediaManagmentSystem
git add render.yaml scripts/collect_backend_env.sh docs/DEPLOY_BACKEND.md
git commit -m "Render Blueprint at repo root + env collector script"
git push
```

### 2. Sign in to Render

[https://dashboard.render.com/](https://dashboard.render.com/) — sign in with GitHub.

### 3. Create the Blueprint

- Click **+ New** (top right) → **Blueprint**
- Pick your repo (`chandk-ai/smms`)
- Render auto-detects `render.yaml` at the root
- Give it a service group name, e.g. `smms-prod`
- Click **Apply**

### 4. Fill the prompted env vars

Render shows a form for every `sync: false` variable. Run this to get the right values:

```bash
bash scripts/collect_backend_env.sh
```

Paste the values it tells you to grab. The fields are:

| Field | Where to get it |
|---|---|
| `SUPABASE_URL` | `https://ukaulrxhegrqyfrirnkb.supabase.co` (already known) |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Settings → API → `service_role` |
| `SUPABASE_JWT_SECRET` | Supabase → Settings → API → JWT Settings → JWT Secret |
| `SUPABASE_POSTGRES_CONNECTION_STRING` | `postgresql+asyncpg://postgres.ukaulrxhegrqyfrirnkb:<password>@aws-0-us-east-1.pooler.supabase.com:6543/postgres` (get the password from Supabase → Settings → Database) |
| `LLM_ANTHROPIC_API_KEY` | Your Anthropic key (or leave blank and use `mock` / `ollama` for now) |

### 5. Deploy

Click **Apply** → Render builds the Docker image (~3 min first time), boots all four services, runs the health-check on `/api/v1/health`, and gives you a public URL.

When it's green, the API is at:

```
https://smms-api.onrender.com   (or whatever Render assigns)
```

Test it:

```bash
curl https://smms-api.onrender.com/api/v1/health
# → {"status": "ok"}
```

### 6. Wire the frontend to the new backend

Set two env vars in Vercel (Settings → Environment Variables):

```
NEXT_PUBLIC_API_URL = https://smms-api.onrender.com
BACKEND_URL         = https://smms-api.onrender.com
```

Then redeploy the frontend (Vercel → Deployments → top → ⋯ → Redeploy).

---

## What if the build fails?

Common issues + fixes:

| Symptom | Fix |
|---|---|
| `ENOENT: package.json` | Render's `rootDir: backend` not set — check the service config in dashboard |
| `pip install fails: anthropic / openai sdk` | These are optional; Dockerfile installs them but they fall back to mock when keys are missing |
| Health check times out | Check `/api/v1/health` returns 200 — first cold start can take ~30s |
| Worker keeps restarting | Open the worker logs; usually a missing env var |
| `croniter not found` | Already in pyproject.toml; if missing, add `croniter>=2.0` and redeploy |

---

## Render plan & cost

- **Starter** (`plan: starter` in render.yaml): ~$7/month per service × 3 services + ~$10 for Redis ≈ **$31/month**
- **Free** (`plan: free`): works but services sleep after 15 min of inactivity, which means the agent worker stops processing — **don't use free for the worker/beat**, only for the API on a side project

For production you'll likely want `plan: standard` ($25/service) for the API + workers — the Celery worker can use real CPU during agent runs.

## Alternatives if you'd rather not Render

- **Fly.io**: `cd backend && fly launch --config ../infra/fly.toml` — requires separate apps for worker/beat; more fiddly but cheaper at scale
- **Railway**: `railway up` — simplest UI, pay-per-use
