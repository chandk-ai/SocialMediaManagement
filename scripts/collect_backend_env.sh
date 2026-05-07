#!/usr/bin/env bash
# Prints the env-var values you need to paste into Render Blueprint setup.
# Reads from your local environment, falling back to obvious defaults so
# you can see what shape each value should be.
set -euo pipefail

cat <<EOF
Paste these into Render's Blueprint setup screen
=================================================

SUPABASE_URL
  → https://ukaulrxhegrqyfrirnkb.supabase.co

SUPABASE_SERVICE_ROLE_KEY
  → Get from Supabase Dashboard → Project Settings → API → "service_role"
    (long JWT, starts with eyJhbGciOi...)

SUPABASE_JWT_SECRET
  → Get from Supabase Dashboard → Project Settings → API → "JWT Settings"
    → "JWT Secret" (the bare HMAC key, NOT a JWT token)

SUPABASE_POSTGRES_CONNECTION_STRING
  → postgresql+asyncpg://postgres.ukaulrxhegrqyfrirnkb:<DB-PASSWORD>@aws-0-us-east-1.pooler.supabase.com:6543/postgres
  → <DB-PASSWORD> is in Supabase Dashboard → Project Settings → Database
    → Connection pooling → "Database password" (reset if you forgot it)

LLM_ANTHROPIC_API_KEY
  → Your Anthropic API key (starts with sk-ant-...)
    OR: leave blank and switch LLM_DEFAULT_PROVIDER to "mock" / "ollama" / etc.

Auto-handled (do NOT enter; Render fills from the Redis service):
  REDIS_URL · QUEUE_BROKER_URL · QUEUE_RESULT_BACKEND

Auto-handled (Render generates a 32-byte secret):
  SEC_TOKEN_VAULT_MASTER_KEY

EOF
