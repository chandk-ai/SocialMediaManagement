#!/usr/bin/env python3
"""Production smoke test for the deployed SMMS stack.

Runs through the invariants that should hold on a live deployment after
every release. Read-only by default — pass ``--write`` to also exercise
the create-and-clean-up paths (creates a source, a platform, a workflow,
verifies they show up, then deletes them).

What gets checked
-----------------
* Liveness   — ``/api/v1/health`` returns 200
* Readiness  — ``/api/v1/ready`` returns 200 with both Postgres + Redis OK
* Auth       — ``/api/v1/auth/me`` returns the expected org + role
* Audit chain— ``/api/v1/audit-logs/verify`` reports ``chain_intact: true``
* LLM usage  — ``/api/v1/llm-usage`` returns a sane budget summary shape
* Compliance — ``/api/v1/compliance/profiles`` returns ≥ 1 profile
* Plugins    — ``/api/v1/plugins`` returns the expected counts
* Members    — ``/api/v1/team/members`` returns at least 1 row

Optional with ``--write``:
* Source CRUD       — create RSS source, list, delete
* Platform CRUD     — create Discord platform with a fake webhook, list, delete
* Workflow CRUD     — create with manual schedule + mock LLM, list, delete

The script is idempotent and self-cleaning — every artifact is named
``smoke-<timestamp>-<rand>`` and explicitly deleted in a finally block,
so a failed run leaves at most one stray row that's easy to identify.

Usage
-----
    # Read-only, against your prod URL:
    BASE_URL=https://api.example.com \\
    SUPABASE_URL=https://xxx.supabase.co \\
    SUPABASE_ANON_KEY=ey... \\
    SMMS_EMAIL=admin@acme.com \\
    SMMS_PASSWORD=... \\
    python scripts/smoke_test.py

    # Read + create + cleanup:
    python scripts/smoke_test.py --write

Dependencies
------------
Standard library only. ``urllib`` instead of httpx so the script runs on
any Python 3.10+ install without an env setup.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import string
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# ── colours / formatting ────────────────────────────────────────────────────
def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


_GREEN = "\033[32m" if _supports_color() else ""
_RED = "\033[31m" if _supports_color() else ""
_YELLOW = "\033[33m" if _supports_color() else ""
_DIM = "\033[2m" if _supports_color() else ""
_BOLD = "\033[1m" if _supports_color() else ""
_RESET = "\033[0m" if _supports_color() else ""


def ok(msg: str) -> None:
    print(f"  {_GREEN}✓{_RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {_RED}✗{_RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {_YELLOW}!{_RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {_DIM}·{_RESET} {msg}")


def section(title: str) -> None:
    print(f"\n{_BOLD}── {title}{_RESET}")


# ── HTTP helpers ────────────────────────────────────────────────────────────
@dataclass
class HttpError(Exception):
    status: int
    body: str
    url: str

    def __str__(self) -> str:
        return f"HTTP {self.status} on {self.url}: {self.body[:200]}"


def http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict | str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8")
        except Exception:                                                  # noqa: BLE001
            pass
        raise HttpError(exc.code, raw, url) from exc
    except urllib.error.URLError as exc:
        raise HttpError(0, str(exc.reason), url) from exc


# ── Supabase auth ───────────────────────────────────────────────────────────
def supabase_signin(supabase_url: str, anon_key: str, email: str, password: str) -> str:
    """Sign in via Supabase Auth and return the access_token."""
    url = f"{supabase_url.rstrip('/')}/auth/v1/token?grant_type=password"
    status, body = http(
        "POST", url,
        headers={"apikey": anon_key},
        body={"email": email, "password": password},
    )
    if status != 200 or not isinstance(body, dict) or not body.get("access_token"):
        raise SystemExit(f"Supabase sign-in failed: status={status} body={body!r:.200}")
    return body["access_token"]


# ── test result tracking ────────────────────────────────────────────────────
@dataclass
class Results:
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    failures: list[str] = field(default_factory=list)

    def record(self, name: str, success: bool, detail: str = "") -> None:
        if success:
            self.passed += 1
            ok(name + (f" — {detail}" if detail else ""))
        else:
            self.failed += 1
            self.failures.append(name + (f": {detail}" if detail else ""))
            fail(name + (f" — {detail}" if detail else ""))


# ── individual checks ───────────────────────────────────────────────────────
def check_liveness(base: str, r: Results) -> None:
    section("Liveness")
    try:
        status, _ = http("GET", f"{base}/api/v1/health")
        r.record("GET /health returns 200", status == 200, f"status={status}")
    except HttpError as exc:
        r.record("GET /health returns 200", False, str(exc))


def check_readiness(base: str, r: Results) -> None:
    section("Readiness (DB + Redis)")
    try:
        status, body = http("GET", f"{base}/api/v1/ready", timeout=8.0)
        r.record("GET /ready returns 200", status == 200, f"status={status}")
        if isinstance(body, dict):
            checks = body.get("checks", {})
            for dep in ("postgres", "redis"):
                c = checks.get(dep, {})
                ok_or_skipped = c.get("ok") or c.get("skipped")
                r.record(
                    f"  {dep} check passes",
                    bool(ok_or_skipped),
                    f"{c.get('ok', False)} skipped={c.get('skipped', False)} "
                    f"err={c.get('error', '')}",
                )
            elapsed = body.get("elapsed_ms")
            if isinstance(elapsed, (int, float)) and elapsed > 1000:
                warn(f"  /ready took {elapsed}ms — investigate if persistent")
                r.warnings += 1
    except HttpError as exc:
        r.record("GET /ready returns 200", False, str(exc))


def check_auth_me(base: str, headers: dict[str, str], r: Results) -> dict | None:
    section("Auth")
    try:
        status, body = http("GET", f"{base}/api/v1/auth/me", headers=headers)
        r.record("GET /auth/me returns 200", status == 200, f"status={status}")
        if not isinstance(body, dict):
            return None
        org_id = body.get("org_id")
        role = body.get("role")
        info(f"  signed in as {body.get('email')} · org={org_id} · role={role}")
        r.record("  has org_id", bool(org_id) and org_id != "00000000-0000-0000-0000-000000000001",
                 "got placeholder default" if org_id == "00000000-0000-0000-0000-000000000001"
                 else "")
        r.record("  has role", role in {"viewer", "editor", "admin"}, f"role={role}")
        return body
    except HttpError as exc:
        if exc.status == 403 and "no_membership" in exc.body:
            warn("  /auth/me returned 403 no_membership — this account isn't a member of any org")
            r.record("GET /auth/me returns 200", False, "no_membership 403")
        else:
            r.record("GET /auth/me returns 200", False, str(exc))
        return None


def check_audit_chain(base: str, headers: dict[str, str], r: Results) -> None:
    section("Tamper-evident audit log")
    try:
        status, body = http("GET", f"{base}/api/v1/audit-logs/verify?limit=1000", headers=headers)
        r.record("GET /audit-logs/verify returns 200", status == 200, f"status={status}")
        if isinstance(body, dict):
            total = body.get("total", 0)
            ok_count = body.get("ok", 0)
            tampered = body.get("tampered", 0)
            intact = bool(body.get("chain_intact"))
            info(f"  rows: total={total} ok={ok_count} tampered={tampered}")
            r.record("  chain_intact", intact and tampered == 0,
                     f"intact={intact} tampered={tampered}")
            if total == 0:
                warn("  audit_log is empty — nothing has been recorded yet")
                r.warnings += 1
    except HttpError as exc:
        if exc.status == 403:
            warn("  /audit-logs/verify is admin-only — sign in as admin to verify")
            r.warnings += 1
        elif exc.status == 503:
            warn("  audit_log table not available (memory backend)")
            r.warnings += 1
        else:
            r.record("GET /audit-logs/verify returns 200", False, str(exc))


def check_llm_usage(base: str, headers: dict[str, str], r: Results) -> None:
    section("LLM budget guard")
    try:
        status, body = http("GET", f"{base}/api/v1/llm-usage", headers=headers)
        if status == 503:
            warn("  /llm-usage returned 503 (memory backend) — skipping")
            r.warnings += 1
            return
        r.record("GET /llm-usage returns 200", status == 200, f"status={status}")
        if isinstance(body, dict):
            for k in ("billing_month", "mtd_spend_usd", "budget_usd",
                      "remaining_usd", "over_budget"):
                r.record(f"  has key {k}", k in body)
            mtd = body.get("mtd_spend_usd", 0)
            cap = body.get("budget_usd", 0)
            info(f"  MTD spend ${mtd:.2f} of ${cap:.2f} — over_budget={body.get('over_budget')}")
    except HttpError as exc:
        r.record("GET /llm-usage returns 200", False, str(exc))


def check_compliance_profiles(base: str, headers: dict[str, str], r: Results) -> None:
    section("Compliance scanner")
    try:
        status, body = http("GET", f"{base}/api/v1/compliance/profiles", headers=headers)
        r.record("GET /compliance/profiles returns 200", status == 200, f"status={status}")
        if isinstance(body, dict):
            profiles = body.get("profiles", [])
            r.record("  has ≥ 4 profiles", len(profiles) >= 4, f"{len(profiles)} found")
            names = [p.get("name") for p in profiles]
            for expected in ("finra", "hipaa", "fda", "crypto"):
                r.record(f"  has {expected}", expected in names)
    except HttpError as exc:
        r.record("GET /compliance/profiles returns 200", False, str(exc))


def check_plugins(base: str, headers: dict[str, str], r: Results) -> None:
    section("Plugin registry")
    for kind, expected_min in (
        ("platform", 10), ("source", 5), ("llm", 4),
        ("trigger", 4), ("review_channel", 3),
    ):
        try:
            status, body = http("GET", f"{base}/api/v1/plugins?kind={kind}", headers=headers)
            count = len(body) if isinstance(body, list) else 0
            r.record(f"  {kind}: ≥ {expected_min}", count >= expected_min, f"got {count}")
        except HttpError as exc:
            r.record(f"  {kind}: ≥ {expected_min}", False, str(exc))


def check_team_members(base: str, headers: dict[str, str], r: Results) -> None:
    section("Team members")
    try:
        status, body = http("GET", f"{base}/api/v1/team/members", headers=headers)
        if status == 503:
            warn("  /team/members returned 503 (memory backend) — skipping")
            r.warnings += 1
            return
        r.record("GET /team/members returns 200", status == 200, f"status={status}")
        if isinstance(body, dict):
            members = body.get("members", [])
            r.record("  has ≥ 1 member", len(members) >= 1, f"{len(members)} found")
    except HttpError as exc:
        r.record("GET /team/members returns 200", False, str(exc))


# ── write-mode CRUD round-trip ─────────────────────────────────────────────
def stamp() -> str:
    return f"smoke-{int(time.time())}-{''.join(random.choices(string.ascii_lowercase, k=4))}"


def write_round_trip(base: str, headers: dict[str, str], r: Results) -> None:
    section("Write round-trip (create → list → delete)")
    tag = stamp()
    source_id: str | None = None
    platform_id: str | None = None
    workflow_id: str | None = None
    try:
        # ── source ──
        try:
            _, src = http("POST", f"{base}/api/v1/sources", headers=headers, body={
                "plugin_name": "rss",
                "display_name": tag + "-rss",
                "config": {"feed_url": "https://example.com/feed.xml"},
            })
            if isinstance(src, dict) and src.get("id"):
                source_id = src["id"]
                r.record("create source", True, source_id[:8])
            else:
                r.record("create source", False, str(src)[:200])
        except HttpError as exc:
            r.record("create source", False, str(exc))

        # ── platform (Discord with a fake webhook — no real publish) ──
        try:
            _, plat = http("POST", f"{base}/api/v1/platforms", headers=headers, body={
                "plugin_name": "discord",
                "display_name": tag + "-discord",
                "account_handle": "smoke-channel",
                "config": {"webhook_url": "https://discord.com/api/webhooks/0/smoke"},
                "is_default": False,
                "tags": ["smoke"],
            })
            if isinstance(plat, dict) and plat.get("id"):
                platform_id = plat["id"]
                r.record("create platform", True, platform_id[:8])
        except HttpError as exc:
            r.record("create platform", False, str(exc))

        # ── workflow (manual schedule, mock LLM — won't actually run) ──
        if source_id and platform_id:
            try:
                _, wf = http("POST", f"{base}/api/v1/workflows", headers=headers, body={
                    "name": tag,
                    "description": "Created by smoke_test.py",
                    "source_ids": [source_id],
                    "platform_ids": [platform_id],
                    "schedule": {"kind": "manual"},
                    "config": {
                        "tone": "neutral", "audience": "general",
                        "max_revisions": 1, "quality_threshold": 0.7,
                        "low_quality_threshold": 0.3,
                        "require_human_approval": True,           # never auto-publish
                        "llm_provider": "mock", "llm_model": "mock-1",
                        "extra": {},
                    },
                })
                if isinstance(wf, dict) and wf.get("id"):
                    workflow_id = wf["id"]
                    r.record("create workflow", True, workflow_id[:8])
            except HttpError as exc:
                r.record("create workflow", False, str(exc))

        # ── verify the workflow shows up in the list ──
        if workflow_id:
            try:
                _, items = http("GET", f"{base}/api/v1/workflows", headers=headers)
                listed = any(
                    isinstance(w, dict) and w.get("id") == workflow_id
                    for w in (items if isinstance(items, list) else [])
                )
                r.record("workflow appears in list", listed)
            except HttpError as exc:
                r.record("workflow appears in list", False, str(exc))
    finally:
        # Best-effort cleanup — try every delete regardless of failures above.
        if workflow_id:
            try:
                http("DELETE", f"{base}/api/v1/workflows/{workflow_id}", headers=headers)
                ok("cleanup: workflow deleted")
            except HttpError as exc:
                warn(f"cleanup: workflow delete failed — {exc}")
        if platform_id:
            try:
                http("DELETE", f"{base}/api/v1/platforms/{platform_id}", headers=headers)
                ok("cleanup: platform deleted")
            except HttpError as exc:
                warn(f"cleanup: platform delete failed — {exc}")
        if source_id:
            try:
                http("DELETE", f"{base}/api/v1/sources/{source_id}", headers=headers)
                ok("cleanup: source deleted")
            except HttpError as exc:
                warn(f"cleanup: source delete failed — {exc}")


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="SMMS production smoke test")
    p.add_argument("--write", action="store_true",
                   help="Run write round-trip (create + cleanup) in addition to read-only checks.")
    p.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000"),
                   help="Backend URL (default: $BASE_URL or http://localhost:8000)")
    args = p.parse_args()

    base = args.base_url.rstrip("/")
    print(f"{_BOLD}SMMS smoke test{_RESET} → {base} (mode={'read+write' if args.write else 'read-only'})")

    # ── auth ──
    supabase_url = os.environ.get("SUPABASE_URL")
    anon_key = os.environ.get("SUPABASE_ANON_KEY")
    email = os.environ.get("SMMS_EMAIL")
    password = os.environ.get("SMMS_PASSWORD")
    bearer = os.environ.get("SMMS_BEARER")        # optional shortcut
    if bearer:
        access_token = bearer
        info("using SMMS_BEARER from env")
    elif supabase_url and anon_key and email and password:
        info(f"signing in as {email} via Supabase…")
        try:
            access_token = supabase_signin(supabase_url, anon_key, email, password)
            ok("supabase sign-in")
        except SystemExit as exc:
            fail(str(exc))
            return 2
    else:
        fail(
            "Need either SMMS_BEARER, or all of "
            "SUPABASE_URL, SUPABASE_ANON_KEY, SMMS_EMAIL, SMMS_PASSWORD"
        )
        return 2
    headers = {"Authorization": f"Bearer {access_token}"}

    # ── read-only checks (always run) ──
    r = Results()
    check_liveness(base, r)
    check_readiness(base, r)
    me = check_auth_me(base, headers, r)
    if me:
        check_audit_chain(base, headers, r)
        check_llm_usage(base, headers, r)
        check_compliance_profiles(base, headers, r)
        check_plugins(base, headers, r)
        check_team_members(base, headers, r)
    if args.write and me:
        write_round_trip(base, headers, r)

    # ── summary ──
    print()
    print(f"{_BOLD}── summary{_RESET}")
    print(f"  {_GREEN}{r.passed} passed{_RESET}, {_RED}{r.failed} failed{_RESET}, "
          f"{_YELLOW}{r.warnings} warnings{_RESET}")
    if r.failures:
        print(f"\n{_BOLD}failures:{_RESET}")
        for f in r.failures:
            print(f"  {_RED}✗{_RESET} {f}")
    return 0 if r.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
