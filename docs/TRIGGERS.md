# Triggers & the human-in-the-loop review flow

A **Trigger** is *the entry point that wakes the agent pipeline*. The system supports six out of the box, all built on the same plugin pattern:

| Plugin       | Kind        | Where it fires from |
|--------------|-------------|---------------------|
| `manual`     | UI / API    | "Run now" button or `POST /workflows/{id}/run` |
| `schedule`   | Cron        | Celery Beat fires on the workflow's schedule |
| `webhook`    | HTTP        | `POST /api/v1/webhooks/{trigger_id}` with HMAC signature |
| `whatsapp`   | Messaging   | Inbound WhatsApp Business message |
| `instagram`  | Messaging   | Inbound Instagram DM or @-mention |
| `telegram`   | Messaging   | Inbound Telegram bot message + inline-keyboard button presses |

Triggers and review channels share the same plug-and-play architecture as platforms / sources / LLMs — adding a fresh entry point (Telegram, SMS, Discord) is one file plus a `@register_plugin('trigger', '<name>')` decorator.

---

## End-to-end flow with WhatsApp

```
        ┌────────────┐
        │  You (WA)  │ "Post about our Q4 SF launch event tomorrow at 6pm."
        └──────┬─────┘
               ▼  (HTTPS POST)
   /webhooks/whatsapp/{trigger_id}
               │
               ▼
   WhatsAppTrigger.parse()  ──► TriggerEvent(directive="Post about...", sender=+1...)
               │
               ▼
   WorkflowService.run_from_trigger(directive=...)
               │
               ▼
   ┌──────────────────────────────────────────────┐
   │  Agents: Planner → Executor → Evaluator →    │
   │           Critique  (LangGraph state machine)│
   └──────────────────────────────────────────────┘
               │
               ▼  (drafts + decision)
   require_human_approval == true  →  ReviewSession PENDING
               │
               ▼  (outbound)
   WhatsAppReviewChannel.send_for_review()
        ──► interactive WhatsApp message:
            "Here's your draft for LinkedIn …
             [Approve] [Revise] [Reject]"
               │
               ▼  (inbound reply, same webhook)
   ReviewService.apply_reply()
               │
               ├── parse_decision("Approve")            → APPROVE
               ├── parse_decision("revise: shorter")    → REVISE + feedback
               └── parse_decision("no")                 → REJECT
               │
               ▼
   WorkflowService.resume_after_review()
        ├─ APPROVE → publish via every connected platform (LinkedIn, X, IG, ...)
        ├─ REVISE  → re-run Executor with feedback as critique notes; new review round
        └─ REJECT  → cancel the run
```

Instagram works the same way — DMs and quick-reply buttons map to the same `parse_decision()` helper. Email, Slack and the in-app dashboard are wired in identically.

---

## Configuring a WhatsApp trigger

1. **Register a Meta WhatsApp Business** number; note the `phone_number_id` and **App secret**.
2. In the SMMS dashboard, **Triggers → Add → WhatsApp**:
   - Pick the workflow it should fire.
   - `verify_token`: any high-entropy string you'll paste into Meta's webhook setup form.
   - `app_secret`: from Meta App Settings — used to verify the HMAC `X-Hub-Signature-256` on every inbound POST.
   - `phone_number_id`: Meta-issued sender ID.
   - **Allowed senders**: comma-separated phone numbers permitted to fire this trigger (empty = anyone the channel routes to us).
   - **Review channel**: `whatsapp` (so drafts come back to you in the same chat) — or `in_app` if you'd rather approve from the dashboard.
   - **Review recipient**: phone number that should receive the draft for approval.
3. In Meta's developer console, point the webhook to:
   `https://<your-domain>/api/v1/webhooks/whatsapp/<trigger_id>`
   subscribed to the `messages` field on your WhatsApp Business Account.

That's it — a real WhatsApp message now drives the pipeline.

## Configuring a Telegram trigger

1. Create a bot via **@BotFather** in Telegram → save the bot token.
2. Register the webhook (Telegram does the rest of the wiring):
   ```
   curl https://api.telegram.org/bot<TOKEN>/setWebhook \
        -d url=https://<your-domain>/api/v1/webhooks/telegram/<trigger_id> \
        -d secret_token=<random-32-char-string>
   ```
3. In the SMMS dashboard, **Triggers → Add → Telegram**:
   - Pick the workflow it should fire.
   - `secret_token`: the same value you used in step 2 — verified against the
     `X-Telegram-Bot-Api-Secret-Token` header on every inbound POST.
   - `bot_token_env`: env var holding the bot token (default `TELEGRAM_BOT_TOKEN`).
   - **Allowed chat ids** (optional): chat IDs permitted to fire the trigger
     (find with `/start`, then check the webhook log). Empty = anyone who can
     message the bot.
   - **Review channel**: `telegram` — drafts come back to the same chat with
     **inline-keyboard buttons** (✅ Approve / ✏️ Revise / ❌ Reject). Pressing
     a button delivers a `callback_query`, which the trigger maps back to the
     pending `ReviewSession` and applies the decision instantly.
4. Send `/start` to the bot, then any message — the agents pick it up as a
   directive and reply with the draft + buttons.

## Configuring an Instagram trigger

Same pattern, with these differences:

- IG triggers fire on DMs to your IG business account (and optionally `@`-mentions).
- The webhook URL is `https://<your-domain>/api/v1/webhooks/instagram/<trigger_id>`.
- The Meta app needs `instagram_manage_messages` + `pages_messaging` scopes.

---

## How replies are correlated

When a draft is delivered, the channel adapter returns a **channel-side message ID**. SMMS stores it on the `ReviewSession` row. When the reviewer replies:

1. If the reply carries an `in_reply_to` (WhatsApp's `context.id`, IG's `reply_to.mid`), we look the session up by `(channel, message_id)`.
2. Otherwise we fall back to "latest pending review for `(channel, sender)`" — works fine when each reviewer has at most one open draft at a time.
3. The text is parsed by `parse_decision()` — supports keywords (`yes / approve / lgtm / ✅`), revise prompts (`"revise: make it casual"`), or substantive free-text (treated as revision feedback).

A detected **APPROVE** triggers publishing to every connected social platform at once. A **REVISE** re-enters the Executor with the feedback as critique notes, then opens another review round. A **REJECT** cancels the run cleanly.

---

## Human approval as a workflow setting

`Workflow.config.require_human_approval = True` makes review mandatory regardless of how the run was started — even cron / manual runs will pause and ping the configured review channel. Combine with `quality_threshold` for a hybrid model: low-quality drafts auto-escalate, everything else publishes silently.
