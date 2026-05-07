# Get started in 10 minutes

A step-by-step trainer for a brand-new user. The same content drives the in-app **`/onboarding`** wizard — finish either and you'll have a working publish loop.

---

## Step 1 — Sign in

Open `https://your-host/` and click **Continue with Okta** (or Continue with Supabase, depending on which auth backend your admin enabled). When the OIDC redirect returns, you land on the dashboard with an empty workspace.

> ⚙️ **Local-dev shortcut.** Set `DEV_BEARER_TOKEN=dev.admin.you@example.com.<your-org-uuid>` in `.env` to bypass auth completely.

---

## Step 2 — Connect your first social account (≈ 2 min)

1. **Platforms → Add → LinkedIn** (or any of the 16 supported networks).
2. Give the account a label (e.g. "Acme · Marketing LinkedIn") and an optional handle.
3. Click **Create + start OAuth** — a new tab opens to LinkedIn's consent screen.
4. Approve. The tab closes; the Platforms page now shows your account with status `connected`.

You can repeat this for **multiple accounts on the same platform** (3 Instagrams, 2 Facebook pages, etc.) — they all get their own row and can be tagged for later targeting.

---

## Step 3 — Connect a content source (≈ 1 min)

Sources are where the agents pull *reference material* from. Easiest to start with an RSS feed:

1. **Sources → Add a source → RSS / Atom feed**.
2. Display name: "Engineering blog".
3. Config (JSON):
   ```json
   { "feed_url": "https://yourcompany.com/blog/feed.xml", "max_items": 20 }
   ```
4. **Create source**.

Other quick wins: paste a Notion database id, point at a Google Drive folder, or skip sources entirely and drive runs from a WhatsApp / Telegram message (Step 5).

---

## Step 4 — Build your first workflow (≈ 2 min)

A workflow ties sources → agents → target accounts → schedule.

1. **Workflows → New workflow**.
2. **Basics**: name "Daily LinkedIn from blog", tone "professional", audience "engineering managers", LLM provider "anthropic" (or "ollama" for self-hosted).
3. **Schedule**: pick **Optimal** for AI-driven scheduling, or **Manual** if you want to run on demand.
4. **Sources**: tick the RSS source from step 3.
5. **Target accounts**: pick the LinkedIn account from step 2. To target *all* connected accounts of a platform, tick them individually or set the workflow's default selector to `all_of_platforms=["linkedin"]` via the API for now (UI follow-up coming).
6. **Create workflow** → click **Activate**.

---

## Step 5 — (Optional but powerful) Add a WhatsApp / Telegram trigger

Lets you start a workflow run by texting a bot.

1. **Triggers → Add → WhatsApp** (or Telegram).
2. For WhatsApp:
   - In Meta's developer console, register a WhatsApp Business phone-number.
   - Set the webhook URL to `https://<your-host>/api/v1/webhooks/whatsapp/<the-trigger-id>`.
   - Paste the same `verify_token` and `app_secret` you set in Meta into the SMMS form.
3. For Telegram:
   - Talk to **@BotFather** to create a bot, save the token.
   - Run `curl https://api.telegram.org/bot<TOKEN>/setWebhook -d url=<your-host>/api/v1/webhooks/telegram/<trigger-id> -d secret_token=<random>`.
   - Paste the same `secret_token` into the SMMS form.
4. Pick a **review channel** — same network is the most natural ("send drafts back as WhatsApp / Telegram messages with Approve / Revise / Reject buttons").
5. Set **review recipient** = your phone / chat id.
6. Send the bot any message: *"Post about our Q4 launch in SF on every IG and FB account"*. Within ~30 seconds, the bot replies with the draft and three buttons.

---

## Step 6 — Run, review, publish

- **Manual run from the UI**: Workflows → click **Run now**. Watch the dashboard's recent-posts section.
- **Trigger run from a chat app**: see Step 5.
- When the workflow has `require_human_approval = true` (default for trigger-driven runs), drafts wait for you on the **Reviews** page or in your messaging channel.
- Reply **Approve** → the post fans out to every targeted account. Reply with text → the agents revise. Reply **Reject** → the run cancels and posts are discarded.

---

## Step 7 — Live trace + analytics

- The **Analytics** page shows publish volume per platform, last-14-day throughput, and current status mix.
- The **Calendar** page is the month grid of scheduled and published content.
- The **Audit log** is the timeline of every state-changing event.
- The **Reviews** page is your queue of items waiting for a human decision.

---

## Common gotchas

| Symptom | Fix |
|---|---|
| "Please reconnect" badge on a Platform | OAuth token expired — click Reconnect on the account card. |
| Webhook hits but nothing happens | Check Triggers → your trigger → "Allowed senders" — empty = anyone, populated = only the listed handles. |
| IG / TikTok publish fails with "media required" | Make sure a `MediaGenerator` is configured (`Workflow.config.extra.media_generator = "openai_image"`). The `mock` generator returns picsum images for dev. |
| Agent always escalates to human review | `Workflow.config.quality_threshold` is too high — lower it (default 0.75; try 0.6). |
| "rate-limited on linkedin" in logs | The system is honouring LinkedIn's API limits. The post is auto-queued and will retry. |

---

## Going further

- [`docs/TRIGGERS.md`](TRIGGERS.md) — full WhatsApp / Telegram / Instagram setup with payloads.
- [`docs/TARGETING.md`](TARGETING.md) — natural-language directives for fan-out.
- [`docs/SUPABASE.md`](SUPABASE.md) — production persistence + RLS + realtime.
- [`docs/TENANCY.md`](TENANCY.md) — when to upgrade a tenant from shared to dedicated infra.
- [`docs/ROADMAP.md`](ROADMAP.md) — what's still missing and how it's prioritised.
