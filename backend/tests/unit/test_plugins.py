"""Verify that all reference plugins register and are discoverable."""
from app.plugins.manager import PluginManager
from app.plugins.registry import PluginKind


def test_all_reference_platforms_register():
    registry = PluginManager().load_all()
    platforms = {e.name for e in registry.list(PluginKind.PLATFORM)}
    expected = {
        # original five
        "linkedin", "twitter", "facebook", "instagram", "youtube",
        # added in v0.2
        "tiktok", "threads", "pinterest", "reddit", "mastodon",
        "bluesky", "medium", "discord", "slack", "telegram", "tumblr",
    }
    missing = expected - platforms
    assert not missing, f"missing platform plugins: {missing}"


def test_all_reference_sources_register():
    registry = PluginManager().load_all()
    sources = {e.name for e in registry.list(PluginKind.SOURCE)}
    expected = {
        "rss", "web_scraper", "file",
        "web_crawler", "google_drive", "sql_database", "mongodb",
        "vector_db", "youtube", "notion", "github", "s3",
    }
    missing = expected - sources
    assert not missing, f"missing source plugins: {missing}"


def test_all_reference_llms_register():
    registry = PluginManager().load_all()
    llms = {e.name for e in registry.list(PluginKind.LLM)}
    expected = {
        "anthropic", "openai", "azure_openai", "mock",
        "ollama", "openai_compatible", "huggingface", "gemini", "bedrock",
    }
    missing = expected - llms
    assert not missing, f"missing LLM plugins: {missing}"


def test_platform_capabilities_are_exposed():
    registry = PluginManager().load_all()
    twitter = registry.get(PluginKind.PLATFORM, "twitter")
    assert twitter.cls.max_text_length == 280
    bluesky = registry.get(PluginKind.PLATFORM, "bluesky")
    assert bluesky.cls.max_text_length == 300


def test_triggers_and_review_channels_register():
    registry = PluginManager().load_all()
    triggers = {e.name for e in registry.list(PluginKind.TRIGGER)}
    assert {"manual", "schedule", "webhook", "whatsapp", "instagram", "telegram"} <= triggers

    review_channels = {e.name for e in registry.list(PluginKind.REVIEW_CHANNEL)}
    assert {"in_app", "whatsapp", "instagram", "telegram", "email", "slack"} <= review_channels
