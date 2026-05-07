# Plugins: Adding Platforms, Sources, and LLMs

Three plugin categories, all built on the same registry pattern.

```python
from app.plugins.registry import register_plugin
from app.adapters.platforms.base import SocialPlatform, PostPayload, PublishResult

@register_plugin("platform", "pinterest")
class PinterestPlatform(SocialPlatform):
    name = "pinterest"
    display_name = "Pinterest"
    capabilities = {"image": True, "video": True, "text_only": False}
    max_text_length = 500

    async def authenticate(self, credentials: dict) -> bool:
        ...

    async def publish(self, payload: PostPayload) -> PublishResult:
        ...
```

That's it. The plugin is now visible in:
- `GET /api/v1/plugins` (frontend will render a card automatically)
- The workflow editor's platform-selector
- The orchestrator's platform-fanout step

---

## The three plugin contracts

### 1. `SocialPlatform` (publishing target)

| Method | Required | Purpose |
|---|---|---|
| `authenticate(credentials)` | yes | OAuth flow — store tokens in vault |
| `publish(payload)` | yes | Send the post; return platform-side ID |
| `validate(payload)` | yes | Pre-flight check (length, media types, ...) |
| `fetch_metrics(post_id)` | optional | Engagement metrics for analytics |
| `delete(post_id)` | optional | Retract a post |

Class attributes describe capabilities (`capabilities`, `max_text_length`, `supported_media`) and are exposed to the frontend so the UI adapts automatically.

### 2. `ContentSource` (input)

| Method | Required | Purpose |
|---|---|---|
| `connect(config)` | yes | Establish connection / validate config |
| `fetch(since)` | yes | Yield `SourceItem`s newer than `since` |
| `disconnect()` | optional | Clean up |

A `SourceItem` carries `title`, `body`, `url`, `published_at`, `metadata`, optional `media[]`.

### 3. `LLMProvider` (generative model)

| Method | Required | Purpose |
|---|---|---|
| `complete(prompt, **opts)` | yes | Single-shot completion |
| `stream(prompt, **opts)` | yes | Streaming completion |
| `embed(texts)` | optional | Embeddings for RAG |
| `count_tokens(text)` | yes | For budget enforcement |

---

## Discovery & loading

`app.plugins.manager.PluginManager.load_all()` runs at startup:

1. Imports every module in `app/adapters/{platforms,sources,llm}/`.
2. Each `@register_plugin` decorator pushes an entry into `PluginRegistry`.
3. The registry is exposed via DI (`Provide[Container.plugin_registry]`).

To install a third-party plugin shipped as a Python package:

```toml
# pyproject.toml of an external plugin
[project.entry-points."smms.plugins"]
mastodon = "smms_mastodon.platform:MastodonPlatform"
```

`PluginManager` also walks the `smms.plugins` entry point group, so external packages register themselves on install.

---

## Versioning & compatibility

Every plugin declares an `api_version` class attribute. The registry rejects plugins outside the supported range (`>=1.0,<2.0`) and the UI surfaces incompatibility warnings.

---

## Bundled plugins

### Social platforms (16)

LinkedIn · X / Twitter · Facebook · Instagram · YouTube · TikTok · Threads · Pinterest · Reddit · Mastodon · Bluesky · Medium · Discord · Slack · Telegram · Tumblr

A `Platform` row represents a *single connected account*. Connect multiple accounts per platform by adding several rows with the same `plugin_name` — the workspace UI groups them under one header so you can fan a workflow out to one account, several, or all.

### Sources (12)

RSS · web page · file folder · **recursive web crawler (multi-level)** · **Google Drive** · **SQL database** (any SQLAlchemy URL) · **MongoDB** · **Vector DB** (Pinecone / Chroma / Qdrant / Weaviate) · **YouTube channel + transcripts** · **Notion** · **GitHub** · **S3 / MinIO**

### LLM providers (9)

Anthropic Claude · OpenAI · Azure OpenAI · **Ollama (self-hosted)** · **OpenAI-compatible** (covers vLLM, Together AI, Groq, Fireworks, LM Studio, LocalAI, DashScope/Qwen, DeepSeek, Mistral, Perplexity — anything that speaks the OpenAI Chat Completions shape) · **Hugging Face Inference** · **Google Gemini** · **AWS Bedrock** · Mock (deterministic, for tests)
