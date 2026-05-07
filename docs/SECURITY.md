# Security

## Identity & access

- **Authentication** — OIDC / OAuth 2.0 via **Okta** (configurable to any OIDC provider: Auth0, Azure AD, Google, Cognito, Keycloak).
- **Frontend** — `next-auth` with the Okta provider; access token stored in HTTP-only secure cookie.
- **Backend** — FastAPI dependency `get_current_user` validates the JWT against Okta's JWKS endpoint (cached + auto-rotating).
- **RBAC** — three roles: `viewer`, `editor`, `admin`. Enforced via `requires_role()` dependency.
- **Multi-tenant** — every API call is scoped by `organization_id` derived from the verified JWT claims; row-level filters in the repository layer.

## Secrets & token vault

- **Runtime secrets** (DB password, LLM API keys, Okta client secret) are read from environment variables; in production use AWS Secrets Manager / Vault via the `SecretProvider` adapter.
- **Per-platform OAuth tokens** (LinkedIn, X, etc.) are encrypted at rest with AES-256-GCM using a KMS-managed data key. Never logged, never returned via API.

## Audit log

Every state-changing action (workflow run, post publish, user role change) writes an `AuditLog` row capturing:
- `actor_id`, `actor_type` (user / system / agent)
- `action`, `resource_type`, `resource_id`
- `before` / `after` (JSON diff)
- `ip`, `user_agent`, `request_id`

Rows are append-only (insert privilege only) and shipped to immutable storage (S3 with object lock) nightly.

## Hardening checklist

- [x] CORS — strict origin allowlist; no `*`
- [x] CSP — `default-src 'self'`; explicit allowlist for Okta + analytics
- [x] HTTPS only — HSTS preload
- [x] Cookies — `Secure`, `HttpOnly`, `SameSite=Lax`
- [x] Rate limiting — per-IP + per-user, sliding window in Redis
- [x] Input validation — Pydantic v2 everywhere; reject extra fields
- [x] Output sanitization — HTML escaping in templates
- [x] Dependency scanning — `pip-audit` + `npm audit` in CI
- [x] Container scanning — Trivy in CI
- [x] SAST — `bandit` (Python), ESLint security rules (TS)
- [x] Secrets scanning — gitleaks pre-commit hook

## Threat model (top risks)

| Risk | Mitigation |
|---|---|
| Prompt injection from a `Source` causing the agent to publish malicious content | Critique agent enforces compliance; human-in-loop for `NEEDS_REVIEW`; allowlisted hashtags/links |
| Token theft from vault | KMS-wrapped encryption; key rotation; short-lived tokens; vault access audited |
| Account takeover on social platform | Per-platform OAuth, scopes minimized; revoke flow exposed in UI |
| Replay of API calls | JWT `jti` cache in Redis with TTL = token lifetime |
| Tenant data leakage | Repository-layer org-scoping; tested with multi-tenant integration tests |
