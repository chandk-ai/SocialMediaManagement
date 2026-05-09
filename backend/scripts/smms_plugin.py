#!/usr/bin/env python3
"""smms-plugin — scaffold + validate + package an SMMS plugin.

Subcommands:
    init     create a new plugin directory with boilerplate code
    validate sanity-check a plugin's metadata + config_schema
    list     show what kinds of plugins are scaffoldable
    package  zip the plugin into a distributable .smms-plugin file

Usage:
    smms-plugin init source --name my_rss --display "My RSS Reader"
    smms-plugin init platform --name my_blog --display "My Blog Adapter"
    smms-plugin init selection --name my_strategy
    smms-plugin validate ./plugins/my_rss
    smms-plugin package ./plugins/my_rss

The scaffolded module includes:
    plugin.py        the @register_plugin entry point
    plugin.schema.json  config_schema mirror (for the marketplace UI)
    README.md        usage docs
    test_plugin.py   pytest smoke tests

The CLI is dependency-light: stdlib only. Designed to run in a
customer's local Python without pip installing the full backend.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import zipfile
from pathlib import Path

KINDS = {
    "source", "platform", "selection",
    "trigger", "review_channel", "media", "engagement",
}


SOURCE_TEMPLATE = '''\
"""{display} — custom Source plugin.

Yields ``SourceItem`` records that flow into the selection layer +
agent pipeline. Returned items must have:

    external_id  stable id used for de-dup (avoid timestamps)
    title        short text the agents read
    body         optional longer text
    metadata     dict of per-item extras (your plugin can add anything)

The framework calls ``fetch()`` once per workflow run, in the source-
loader phase. Cap your output at ~50 items — the selection layer will
prune further. Long-running fetches block the run, so prefer pagination
+ ``items_per_run`` config.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.adapters.sources.base import SourceConfig, SourceItem, SourcePlugin
from app.plugins.registry import register_plugin


@register_plugin("source", "{name}", api_version="1.0", category="custom")
class {ClassName}(SourcePlugin):
    plugin_name = "{name}"
    display_name = "{display}"
    description = (
        "{description}"
    )

    config_schema = {{
        "type": "object",
        "properties": {{
            "endpoint_url": {{
                "type": "string",
                "title": "Endpoint URL",
                "description": "URL to fetch items from",
            }},
            "items_per_run": {{
                "type": "integer", "minimum": 1, "maximum": 100,
                "default": 10,
                "title": "Items per run",
            }},
        }},
        "required": ["endpoint_url"],
    }}

    async def fetch(self, *, config: SourceConfig) -> list[SourceItem]:
        # TODO: real fetch logic
        return [
            SourceItem(
                external_id="stub-1",
                title="Hello from {display}",
                body="Replace this stub with your real fetch.",
                published_at=datetime.now(timezone.utc),
                metadata={{"source_kind": "{name}"}},
            )
        ]
'''

PLATFORM_TEMPLATE = '''\
"""{display} — custom Platform plugin.

Implements the SocialPlatform contract:

    publish(payload, account)  → PublishResult
    get_metrics(account, external_id)  → metrics dict (Pillar 3)

The framework calls ``publish()`` from the durable runner's run.publish
phase. Throw any exception on failure — the worker handles retries.
For permanent failures (e.g. account suspended), raise PermanentError.
"""
from __future__ import annotations

from app.adapters.platforms.base import (
    PostPayload, PublishResult, SocialPlatform,
)
from app.plugins.registry import register_plugin


@register_plugin("platform", "{name}", api_version="1.0", category="custom")
class {ClassName}(SocialPlatform):
    plugin_name = "{name}"
    display_name = "{display}"
    description = "{description}"

    config_schema = {{
        "type": "object",
        "properties": {{
            "account_handle": {{
                "type": "string",
                "title": "Account handle",
            }},
            "api_key": {{
                "type": "string",
                "title": "API key",
                "format": "password",
            }},
        }},
        "required": ["account_handle", "api_key"],
    }}

    async def publish(
        self, payload: PostPayload, *, account,
    ) -> PublishResult:
        # TODO: real publish logic
        return PublishResult(
            external_id="stub-post-id",
            url="https://example.com/posts/stub",
        )

    async def get_metrics(self, *, account, external_id):
        # TODO: real metrics fetch — feeds the engagement loop.
        return {{
            "likes": 0, "comments": 0, "shares": 0,
        }}
'''

SELECTION_TEMPLATE = '''\
"""{display} — custom Selection strategy.

Decides which SourceItems the agents process this run.
Receives a SelectionContext with candidates + consumed_keys; returns
a SelectionResult with chosen items and (optionally) skip reasons.

Strategies should be deterministic given the same input — the
orchestrator records the rationale in the run trace. If you need an
LLM, set ``needs_llm = True`` and use ``ctx.llm.embed()`` /
``ctx.llm.complete()``.
"""
from __future__ import annotations

from app.domain.value_objects.selection import (
    ItemMode, SelectionResult,
)
from app.plugins.registry import register_plugin
from app.services.selection.base import SelectionContext, SelectionStrategy


@register_plugin("selection", "{name}", api_version="1.0", category="custom")
class {ClassName}(SelectionStrategy):
    plugin_name = "{name}"
    display_name = "{display}"
    description = "{description}"
    needs_llm = False

    config_schema = {{
        "type": "object",
        "properties": {{
            "top_k": {{
                "type": "integer", "minimum": 1, "maximum": 50,
                "default": 5,
                "title": "Top K",
            }},
        }},
    }}

    async def select(self, ctx: SelectionContext) -> SelectionResult:
        top_k = int(self.config.get("top_k", 5))
        unseen = [
            it for it in ctx.candidates
            if (str(it.metadata.get("source_id", "")), it.external_id)
            not in ctx.consumed_keys
        ]
        chosen = unseen[:top_k]
        return SelectionResult(
            chosen=chosen, mode=ItemMode.SYNTHESIZE,
            rationale=f"{name}: top {{len(chosen)}} unseen",
            skipped=[], candidates=len(ctx.candidates),
        )
'''

GENERIC_TEMPLATE = '''\
"""{display} — custom {kind} plugin.

Implement the appropriate base class for your plugin kind, then
register it with @register_plugin. See the SDK docs for plugin
contracts.
"""
from __future__ import annotations

from app.plugins.registry import register_plugin


@register_plugin("{kind}", "{name}", api_version="1.0", category="custom")
class {ClassName}:
    plugin_name = "{name}"
    display_name = "{display}"
    description = "{description}"
    config_schema = {{"type": "object", "properties": {{}}}}
'''


README_TEMPLATE = """\
# {display}

{description}

## Install

Drop this directory under `backend/app/plugins/` (or any package the
plugin manager scans), then restart the backend. The plugin appears
in the `/plugins` API + workflow wizard automatically.

## Configuration

See `plugin.schema.json` for the full config shape — the wizard
renders a form from this file.

## Test

    pytest test_plugin.py
"""


TEST_TEMPLATE = '''\
"""Smoke tests for {display}."""
import pytest


@pytest.mark.asyncio
async def test_plugin_registration():
    from app.plugins.manager import PluginManager
    PluginManager().load_all()
    from app.plugins.registry import PluginKind, registry
    assert registry.get(PluginKind.{KIND_UPPER}, "{name}") is not None
'''


def init_plugin(args) -> None:
    if args.kind not in KINDS:
        sys.exit(f"unknown plugin kind: {args.kind}. options: {sorted(KINDS)}")
    name = _slug(args.name)
    display = args.display or name.replace("_", " ").title()
    description = args.description or f"Custom {args.kind} plugin: {display}"
    out_dir = Path(args.out or f"./plugins/{name}").resolve()
    if out_dir.exists():
        sys.exit(f"directory already exists: {out_dir}")
    out_dir.mkdir(parents=True)

    class_name = "".join(p.capitalize() for p in name.split("_")) + \
                  args.kind.capitalize()

    template = {
        "source": SOURCE_TEMPLATE,
        "platform": PLATFORM_TEMPLATE,
        "selection": SELECTION_TEMPLATE,
    }.get(args.kind, GENERIC_TEMPLATE)

    code = template.format(
        name=name, display=display, description=description,
        ClassName=class_name, kind=args.kind,
    )
    (out_dir / "plugin.py").write_text(code)
    (out_dir / "README.md").write_text(README_TEMPLATE.format(
        display=display, description=description,
    ))
    schema = _extract_schema_from_code(code)
    (out_dir / "plugin.schema.json").write_text(
        json.dumps({"name": name, "display": display, "kind": args.kind,
                    "description": description, "config_schema": schema},
                   indent=2),
    )
    (out_dir / "test_plugin.py").write_text(TEST_TEMPLATE.format(
        display=display, name=name, KIND_UPPER=args.kind.upper(),
    ))
    (out_dir / "__init__.py").write_text("")
    print(f"✔ scaffolded {args.kind} plugin '{name}' at {out_dir}")
    print(f"  Open {out_dir}/plugin.py and replace the stub.")


def validate_plugin(args) -> None:
    p = Path(args.path).resolve()
    code_path = p / "plugin.py"
    if not code_path.exists():
        sys.exit(f"missing plugin.py at {code_path}")
    code = code_path.read_text()
    if "@register_plugin" not in code:
        sys.exit("plugin.py missing @register_plugin decorator")
    if "config_schema" not in code:
        print("⚠ no config_schema declared — wizard form will be empty",
              file=sys.stderr)
    schema = _extract_schema_from_code(code)
    if not isinstance(schema, dict) or schema.get("type") != "object":
        sys.exit("config_schema must be a JSON Schema object")
    print(f"✔ {p.name} looks valid")


def list_kinds(args) -> None:
    for k in sorted(KINDS):
        print(f"  {k}")


def package_plugin(args) -> None:
    p = Path(args.path).resolve()
    if not (p / "plugin.py").exists():
        sys.exit(f"not a plugin: {p}")
    out = Path(args.out or f"{p.name}.smms-plugin")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in p.rglob("*"):
            if f.is_file() and "__pycache__" not in str(f):
                z.write(f, f.relative_to(p))
    print(f"✔ packaged → {out}")


# ── helpers ────────────────────────────────────────────────────────────
def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", s.strip())
    s = re.sub(r"^_+|_+$", "", s)
    return s.lower() or "my_plugin"


def _extract_schema_from_code(code: str) -> dict:
    """Crude regex pull of the literal config_schema dict so the
    generated JSON file mirrors the Python source. Good enough for
    the scaffolded templates; if the user later edits the schema in
    code they should re-run this CLI's `validate`."""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {"type": "object", "properties": {}}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "config_schema":
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:                                # noqa: BLE001
                        return {"type": "object", "properties": {}}
    return {"type": "object", "properties": {}}


def main() -> None:
    p = argparse.ArgumentParser(
        prog="smms-plugin",
        description="Scaffold + validate SMMS plugins.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              smms-plugin init source --name my_rss
              smms-plugin validate ./plugins/my_rss
              smms-plugin package ./plugins/my_rss
        """),
    )
    sp = p.add_subparsers(dest="cmd", required=True)

    pi = sp.add_parser("init", help="scaffold a new plugin")
    pi.add_argument("kind", choices=sorted(KINDS))
    pi.add_argument("--name", required=True)
    pi.add_argument("--display")
    pi.add_argument("--description")
    pi.add_argument("--out")
    pi.set_defaults(func=init_plugin)

    pv = sp.add_parser("validate", help="check a plugin's metadata")
    pv.add_argument("path")
    pv.set_defaults(func=validate_plugin)

    sp.add_parser("list", help="list scaffoldable plugin kinds").set_defaults(func=list_kinds)

    pk = sp.add_parser("package", help="zip a plugin into .smms-plugin")
    pk.add_argument("path")
    pk.add_argument("--out")
    pk.set_defaults(func=package_plugin)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
