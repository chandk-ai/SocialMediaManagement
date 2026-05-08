# Manual test plan

Step-by-step test cases for every user-facing flow. Designed to be run by
a human with a browser + a terminal. Tick the checkboxes as you go.

The plan is organised in **the order a real user would encounter
features**, not in feature-importance order. Run from top to bottom on a
fresh deployment to confirm everything works end-to-end. Skip the
"Optional advanced" sections on a regular smoke pass.

Test prerequisites:
- The deployed URLs (frontend + backend)
- A Supabase project with email auth enabled
- An email account you control (for receiving magic links)
- A second email account (for testing invitations)
- A Telegram account + a phone (to test the bot setup)
- *Optional but recommended*: a sandbox Discord webhook URL, an Anthropic / OpenAI API key

---

## A. Bootstrap + first sign-in

The very first user on a fresh deployment should be auto-promoted to admin.

- [ ] **A1** Verify the database is empty:
  ```sql
  SELECT count(*) FROM smms.organizations;  -- should return 0
  ```
  If non-zero, you're not on a fresh deployment — skip section A.

- [ ] **A2** Visit the frontend. Should bounce to `/login` (middleware enforces auth).

- [ ] **A3** On the login page, the **Email link** tab is selected by default. Confirm:
  - "Send sign-in link" button is visible
  - "No password needed" caption is rendered
  - Switching to the Password tab swaps the form (email + password + Sign in)

- [ ] **A4** Enter your email in the Email link tab → Send sign-in link. Confirm:
  - "Check your email" success card replaces the form
  - Within ~1 minute you receive a Supabase email
  - The link in the email points to `https://<your-frontend>/login#access_token=...` (NOT localhost or a backend URL — if this is wrong, fix Site URL in Supabase, see RUNBOOK.md)

- [ ] **A5** Click the link. Confirm:
  - Browser lands on `/login` briefly, then auto-redirects to `/dashboard`
  - Sidebar is visible with all expected items
  - Dashboard renders without errors
  - You are admin: open Settings → Team and confirm your email shows the "admin" role

- [ ] **A6** Verify the bootstrap created a real org:
  ```sql
  SELECT id, name, slug FROM smms.organizations;       -- exactly 1 row
  SELECT email, role FROM smms.users WHERE email = '<your_email>';
  ```
  Org count should be 1; your user should have role=admin and the org's id (NOT `00000000-0000-0000-0000-000000000001`).

- [ ] **A7** Sign out (TopBar avatar → Sign out). Confirm bounce to `/login`.

- [ ] **A8** Sign in again with magic link. Should land on dashboard without re-bootstrapping (only one org exists).

---

## B. Team management (email-claim flow)

- [ ] **B1** Go to Settings → Team. Confirm:
  - Members card lists you with the "you" badge and "admin" role
  - Pending invitations is empty
  - "Invite a teammate" form is visible

- [ ] **B2** Invite a second email (one you can sign into separately). Pick role = `editor`. Click Send invite. Confirm:
  - Green success message: "Tell them to sign in at the app URL with that email…"
  - Pending invitations table now shows the invited email with role=editor

- [ ] **B3** *Verify the invited row is real*:
  ```sql
  SELECT email, role, claimed_at FROM smms.org_invitations WHERE email='<invitee>';
  ```
  Should be one row with `claimed_at IS NULL`.

- [ ] **B4** **In a private/incognito window**, go to the app URL and magic-link sign in with the invitee's email. Confirm:
  - Lands on dashboard (NOT the `/no-access` page)
  - In Settings → Team, the invitee's email now shows in Members with role=editor
  - The pending invitation has disappeared

- [ ] **B5** Verify the invitation is now claimed:
  ```sql
  SELECT email, claimed_at, claimed_by FROM smms.org_invitations WHERE email='<invitee>';
  ```
  `claimed_at` should be ~ now; `claimed_by` should be a UUID.

- [ ] **B6** As the second user (editor), confirm Settings → Team renders but role dropdowns + Remove buttons are NOT visible (admin-only).

- [ ] **B7** Back as the admin, change the editor's role to viewer via the dropdown. Confirm:
  - Toast / inline confirmation
  - The other user's session reflects the new role on next request (refresh in their tab)

- [ ] **B8** **Self-protection**: try to demote your own admin role. Confirm:
  - You don't see a role dropdown next to "you" — it shows the role badge only
  - Direct API attempt returns 400: `curl -X PATCH /api/v1/team/members/<your_id>/role -d '{"role":"viewer"}'`

- [ ] **B9** **Last-admin guard**: as the only admin, attempt to delete yourself via API: `curl -X DELETE /api/v1/team/members/<your_id>`. Confirm 400 "Cannot remove the last admin."

- [ ] **B10** **Unauthorised email**: in another private window, magic-link sign in with an email that has NO invitation. Confirm:
  - You land on `/no-access` with the friendly "Ask your admin to invite you" page
  - Your email is shown for forwarding to the admin
  - Sign Out + Try a different account both work

---

## C. LLM keys + budget

- [ ] **C1** Settings → AI / LLM providers. Confirm a list of providers with no keys configured.

- [ ] **C2** Add an Anthropic key (or OpenAI). Confirm:
  - "Saved" toast
  - Provider row shows the "configured · ····XXXX" badge with the last 4 chars
  - The "Default LLM for new workflows" picker now lists the provider as an option

- [ ] **C3** Set Anthropic as the default. Confirm green "Saved" inline.

- [ ] **C4** **Budget cap test**: set monthly LLM budget to $0.01 (just to trigger). Confirm:
  - Save succeeds
  - Spend bar shows e.g. "$0.00 / $0.01" with green meter

- [ ] **C5** Trigger a workflow that calls the LLM (you'll need at least one Source + Platform + Workflow — come back to this after section F). Confirm:
  - The first call succeeds
  - The second call returns 402 with `code: llm_budget_exceeded`
  - Settings page meter shows over_budget = red bar + "Cap reached — runs paused" badge

- [ ] **C6** Raise the cap back to $100. Confirm subsequent runs work again.

- [ ] **C7** **Audit hook**: every key-set / set-preferred / remove should land in the audit log. Verify:
  - Audit page shows entries `llm_key.set`, `llm_key.set_preferred`, etc.
  - `actor_id` on each row is the local `smms.users.id` (not the Supabase UID)

---

## D. Sources

- [ ] **D1** Sources → New. Confirm the schema-driven form auto-renders fields from the chosen plugin's `config_schema`.

- [ ] **D2** Pick **RSS** → fill in feed_url (e.g. `https://hnrss.org/frontpage`) → save. Confirm:
  - Source appears in the list
  - Click "Test" → preview shows recent items from the feed
  - "Last fetched" timestamp updates

- [ ] **D3** **Bad config**: edit the source, set feed_url to a non-existent URL, save. Test. Confirm:
  - Test fails with a clear error message (no 500)
  - The source's `last_failure_at` and `last_error` columns get populated:
    ```sql
    SELECT last_failure_at, last_error, error_count FROM smms.sources WHERE id='<id>';
    ```

- [ ] **D4** Edit it back to a working URL. Test. Confirm error counters reset.

- [ ] **D5** **CMS mode (Notion)** *— skip if you don't have a Notion DB to test against*:
  - Create a Notion integration, get the secret + database id
  - Create a Source with cms_mode=true
  - Create a row in Notion with Status=Ready, Content=test post, Platforms=discord
  - Run a workflow that uses this source
  - Confirm the row's Status flips to `Published` and the URL field is populated

---

## E. Platforms

For OAuth platforms (LinkedIn / Twitter / Meta / YouTube), you need the client id/secret configured in env. For a quick test, use **Discord** (just a webhook URL — no OAuth).

- [ ] **E1** Platforms → New. Confirm:
  - Plugin picker lists all 17 platforms
  - Brand-correct icons render
  - Plugins flagged `experimental` show the "Preview" badge

- [ ] **E2** Pick **Discord** → enter a webhook URL (use a sandbox channel) + display name + tags → save. Confirm appears in list.

- [ ] **E3** Click "Test publish" on the row. Confirm:
  - A "Hello from SMMS — please ignore" message lands in your Discord channel
  - The button toggles to "✓ test ok" with the external_post_id

- [ ] **E4** **Real OAuth flow** (any of LinkedIn / Twitter / IG):
  - Pick the platform, save with empty config
  - Click "Connect" / "Start OAuth"
  - Browser redirects to the platform's authorize page
  - Approve → bounces back to `/oauth/callback`
  - Platform row's status becomes `connected`
  - For Meta: if you have multiple Pages, the picker dialog appears

- [ ] **E5** **Multi-account**: connect a second account on the same plugin. Confirm both rows show in Platforms grouped under the plugin.

- [ ] **E6** Audit hooks: connecting + removing a platform writes `platform.connect` / `platform.remove` to the audit log.

---

## F. Workflows

- [ ] **F1** Workflows → New. Step through the wizard:
  - Pick a template OR start blank
  - Sources step: pick at least one source
  - Accounts step: pick at least one platform
  - Voice & schedule step:
    - Tone, audience, LLM picker (defaults to your preferred provider AND model from Settings)
    - **Compliance profile** dropdown lists FINRA / HIPAA / FDA / Crypto / GDPR — leave blank for now
    - Schedule kind: pick Manual for first test
  - Review step: confirm the summary is accurate
  - Click Create → workflow appears in list

- [ ] **F2** Click "Run now" on the workflow. Confirm:
  - Status transitions: PLANNING → EXECUTING → REVIEW (or PUBLISHING if no approval required)
  - Posts appear in Posts page with status REVIEW
  - Audit log entry: `workflow.run`

- [ ] **F3** Open a Post → review the agent trace. Confirm Planner / Executor / Evaluator / Critique all left log entries.

- [ ] **F4** Approve the post. Confirm:
  - Post enters PUBLISHED status (or SCHEDULED if you set a future time)
  - The Discord channel actually receives the message
  - Audit log entry: `post.publish`

- [ ] **F5** **Schedule kinds**:
  - [ ] **Cron**: create a workflow with `* * * * *` (every minute). Wait 2 minutes. Confirm a new run appears.
  - [ ] **Interval**: same idea with `interval=1`.
  - [ ] **Adaptive**: pick adaptive. Confirm description text mentions "Posts more often when engagement is rising" and that on first run (cold-start, < 8 published posts) the run uses the fallback interval.

---

## G. Triggers (Telegram)

- [ ] **G1** Open Telegram → message `@BotFather` → `/newbot` → follow prompts. Save the bot token.

- [ ] **G2** Triggers page. Confirm Telegram is the **first / "recommended"** card with a sparkle badge.

- [ ] **G3** Click Configure on Telegram. Wizard step 1:
  - Paste bot token
  - Pick the workflow created in F1
  - Leave allowed chat ids blank (or add your chat id from `@userinfobot`)
  - Approvals required: 1 (default)
  - Click Create trigger

- [ ] **G4** Wizard step 2: a curl command is rendered with bot token + secret + URL pre-filled. Confirm:
  - URL is `https://<frontend>/api/proxy/webhooks/telegram/<trigger_id>` (single domain — no CORS gotcha)
  - "Copy" button copies the curl to clipboard

- [ ] **G5** Paste the curl in your terminal and run. Confirm `{"ok":true,"result":true}`.

- [ ] **G6** In Telegram, message your bot: "Post about today's launch". Confirm:
  - Within ~5 seconds the bot replies with the draft + ✅ ✏️ ❌ inline keyboard
  - Audit log gets `workflow.run` and `review.session.create`

- [ ] **G7** Tap ✅ Approve. Confirm:
  - Bot replies with confirmation
  - Post status flips to PUBLISHED
  - Discord receives the post
  - Audit log gets `post.publish`

- [ ] **G8** Repeat with ✏️ Revise and ❌ Reject — verify each routes correctly:
  - Revise → run goes back to EXECUTING and a new draft comes back
  - Reject → run goes to CANCELLED, posts marked FAILED with "review rejected"

---

## H. Telegram group quorum

- [ ] **H1** Create a Telegram group. Add your bot to the group (group settings → Add member → search for it).

- [ ] **H2** Create a NEW trigger (not the one from G), this time with:
  - Allowed chat ids: the group's chat id (negative number, e.g. `-1001234567890`)
  - Approvals required: 2

- [ ] **H3** Run the workflow associated. The bot posts the draft to the group with header `Quorum: any 2 ✅ to publish · any 1 ❌ to veto` and Approve button labelled `✅ Approve (0/2)`.

- [ ] **H4** Have one member of the group tap ✅. Confirm:
  - Tally moves to (1/2)
  - Audit log: `review.vote.approve` with the Telegram username + tally
  - Run is still PAUSED (waiting for quorum)

- [ ] **H5** A second member taps ✅. Confirm:
  - Run resumes, post publishes
  - Audit log: another `review.vote.approve` then `post.publish`

- [ ] **H6** Run again. This time, one member taps ❌. Confirm:
  - Run is INSTANTLY cancelled (single-vote veto)
  - Audit log: `review.vote.reject`

- [ ] **H7** Run again. One member taps ✅, then the same member changes to ✏️ Revise. Confirm:
  - Tally still shows 1 vote (per-actor dedupe)
  - Run goes to revision (single-vote pull-back)

---

## I. Compliance scanning

- [ ] **I1** Edit (or create) a workflow with **Compliance profile = FINRA**.

- [ ] **I2** Run the workflow with a directive that includes a forbidden phrase, e.g. "Post about our guaranteed 20% returns next quarter". Confirm:
  - Run pauses for human review with status NEEDS_REVIEW
  - Critique trace shows ESCALATE with the FINRA-specific violation message
  - Post evaluation `flags` field contains the rule message

- [ ] **I3** **Required-disclaimer test**: directive "Post about our 8% YTD performance" (no disclaimer phrase). Confirm:
  - Same escalate behaviour
  - Flag message: "Posts mentioning returns/performance must include a disclaimer like 'Past performance does not guarantee future results' or 'Not investment advice'."

- [ ] **I4** Re-run with directive "Post about our 8% YTD performance — past performance does not guarantee future results". Confirm:
  - Required-presence rule passes
  - Forbidden patterns also pass
  - Run completes normally (or the LLM is asked to revise based on quality, but no compliance flag)

---

## J. Audit log + tamper-evident chain

- [ ] **J1** Audit page renders the activity timeline. Confirm:
  - "✓ chain verified · N/N" badge in the header
  - Filter by action substring works ("workflow", "team.invite", etc.)
  - Each row shows action badge + actor + resource description

- [ ] **J2** **Tamper test**: directly modify a row in the DB:
  ```sql
  UPDATE smms.audit_log
  SET action = 'tampered'
  WHERE org_id = '<your_org>'
  ORDER BY occurred_at DESC LIMIT 1;
  ```
  Refresh the Audit page. Confirm:
  - Badge changes to `⚠ chain break · 1 suspect row`
  - The verify endpoint returns `chain_intact: false`

- [ ] **J3** Restore the row (or just accept the chain is broken from this point — every row after a break is also "suspect" by design).

---

## K. Rate limiting + readiness

- [ ] **K1** **Rate limit**: in a terminal, hammer an endpoint:
  ```bash
  for i in $(seq 1 200); do
    curl -s -o /dev/null -w "%{http_code}\n" \
      "https://<api>/api/v1/workflows" \
      -H "Authorization: Bearer <your_token>"
  done | sort | uniq -c
  ```
  Confirm:
  - Most return 200
  - At least some return 429 with a `Retry-After` header
  - The 429 response body has `retry_after_seconds`

- [ ] **K2** **Per-tenant override**: set the org's `rate_limit_per_minute` to 10 (admin-only, via SQL since the Settings UI doesn't expose it yet):
  ```sql
  UPDATE smms.organizations SET rate_limit_per_minute = 10 WHERE id = '<your_org>';
  ```
  Wait 60s for cache invalidation. Re-run the hammer test. Confirm 429s start sooner (after ~10 requests instead of ~120).

- [ ] **K3** Reset to NULL: `UPDATE smms.organizations SET rate_limit_per_minute = NULL WHERE id = '<your_org>';`

- [ ] **K4** **Readiness**: `curl https://<api>/api/v1/ready | jq`. Confirm both `postgres` and `redis` checks return `ok: true`.

- [ ] **K5** **Readiness failure mode**: temporarily break Redis (e.g. wrong REDIS_URL). Confirm:
  - `/ready` returns 503 with the failed check listed
  - Render takes the instance out of rotation but doesn't kill it (liveness `/health` still 200)

---

## L. Smoke-test script (full automated pass)

After all of the above passes manually once, the smoke-test script gives you a one-shot sanity check after every deploy:

- [ ] **L1** Run the read-only suite:
  ```bash
  BASE_URL=https://<api> \
  SUPABASE_URL=<...> \
  SUPABASE_ANON_KEY=<...> \
  SMMS_EMAIL=<your_admin_email> \
  SMMS_PASSWORD=<...> \
  python scripts/smoke_test.py
  ```
  Expected: every check ✓; exit code 0.

- [ ] **L2** Run the write round-trip:
  ```bash
  python scripts/smoke_test.py --write
  ```
  Expected: source / platform / workflow each created, listed, deleted in a finally block. Exit code 0.

- [ ] **L3** Confirm there are no `smoke-*` artefacts left in the database:
  ```sql
  SELECT count(*) FROM smms.sources WHERE display_name LIKE 'smoke-%';
  SELECT count(*) FROM smms.platforms WHERE display_name LIKE 'smoke-%';
  SELECT count(*) FROM smms.workflows WHERE name LIKE 'smoke-%';
  ```
  All three should be 0.

---

## What "passing" means

A green test plan means:
- Auth works end-to-end across magic-link + OAuth + email-claim invite
- The bootstrap-first-user pattern correctly seeds an admin org
- The `/no-access` 403 path is reachable for un-affiliated emails
- LLM costs are gated by the budget guard with a clean 402
- Rate limits are enforced both globally and per-org
- The audit chain is tamper-evident and the verify badge surfaces breaks
- Telegram approve and quorum behave correctly
- Compliance profiles block the obvious mistakes
- Adaptive scheduling falls back to the configured interval during cold start
- The smoke-test script provides repeat coverage for every release

If any of these fail, fix before more feature work — the runbook
(`RUNBOOK.md`) covers the known gotchas.
