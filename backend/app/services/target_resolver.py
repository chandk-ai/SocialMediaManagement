"""TargetResolver — turn (TargetSelector + workflow defaults) into the
concrete list of `Platform` accounts to publish to."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger
from app.domain.entities.platform import Platform, PlatformStatus
from app.domain.entities.workflow import Workflow
from app.domain.value_objects.ids import OrgId
from app.domain.value_objects.targeting import TargetSelector
from app.repositories.ports import PlatformRepository

log = get_logger(__name__)


@dataclass
class ResolutionResult:
    platforms: list[Platform]
    rationale: list[str]               # human-readable explanation, kept in run trace


class TargetResolver:
    def __init__(self, platform_repo: PlatformRepository) -> None:
        self.platform_repo = platform_repo

    async def resolve(
        self, *, org_id: OrgId, workflow: Workflow,
        directive_selector: TargetSelector | None = None,
    ) -> ResolutionResult:
        """Order of precedence:
        1. Combine the workflow's stored selector with the directive's selector
           (the directive *narrows or augments* the workflow's defaults).
        2. Resolve the combined selector against connected accounts.
        3. If empty, fall back to the workflow's `platform_ids`.
        """
        all_platforms = await self.platform_repo.list(org_id)
        connected = [p for p in all_platforms
                     if p.status is not PlatformStatus.ERROR]

        ds = directive_selector or TargetSelector()
        ws = workflow.target_selector
        # Directive takes precedence over workflow defaults *only when not empty*.
        # Otherwise we union them so a workflow-level "all instagram" still wires
        # explicit handles from a user directive into the same run.
        selector = ws.merged_with(ds) if not ds.is_empty() else ws

        rationale: list[str] = []
        chosen_ids: dict[str, Platform] = {}

        if selector.is_empty():
            # Fall back to the workflow's pinned platform_ids.
            for pid in workflow.platform_ids:
                p = next((x for x in connected if x.id == pid), None)
                if p:
                    chosen_ids[str(p.id)] = p
            rationale.append(f"defaults: {len(chosen_ids)} platform(s) from workflow.platform_ids")
        else:
            # 1. explicit IDs
            if selector.explicit_platform_ids:
                for pid in selector.explicit_platform_ids:
                    p = next((x for x in connected if x.id == pid), None)
                    if p:
                        chosen_ids[str(p.id)] = p
                rationale.append(
                    f"explicit_ids: {len(selector.explicit_platform_ids)} requested"
                )
            # 2. all-of-platform fan-out
            if selector.all_of_platforms:
                added = 0
                for plug in selector.all_of_platforms:
                    for p in connected:
                        if p.plugin_name == plug:
                            chosen_ids[str(p.id)] = p
                            added += 1
                rationale.append(
                    f"all_of_platforms={list(selector.all_of_platforms)} → +{added} accounts"
                )
            # 3. by handle (case-insensitive, supports "@x" + "x" forms)
            if selector.by_handle:
                wanted = {h.lower().lstrip("@") for h in selector.by_handle}
                added = 0
                for p in connected:
                    handle = (p.account_handle or "").lower().lstrip("@")
                    if handle and handle in wanted:
                        chosen_ids[str(p.id)] = p
                        added += 1
                rationale.append(
                    f"by_handle={list(selector.by_handle)} → +{added} accounts"
                )
            # 4. tagged
            if selector.tagged:
                wanted_tags = {t.lower() for t in selector.tagged}
                added = 0
                for p in connected:
                    if {t.lower() for t in (p.tags or [])} & wanted_tags:
                        chosen_ids[str(p.id)] = p
                        added += 1
                rationale.append(
                    f"tagged={list(selector.tagged)} → +{added} accounts"
                )

        # Apply exclusions
        if selector.exclude_platform_ids:
            for pid in selector.exclude_platform_ids:
                chosen_ids.pop(str(pid), None)
            rationale.append(f"excluded_ids: {len(selector.exclude_platform_ids)}")
        if selector.exclude_plugins:
            excl = set(selector.exclude_plugins)
            for k, p in list(chosen_ids.items()):
                if p.plugin_name in excl:
                    chosen_ids.pop(k, None)
            rationale.append(f"excluded_plugins: {sorted(excl)}")

        # Final fallback — if everything got excluded, surface a clear message.
        if not chosen_ids and workflow.platform_ids:
            for pid in workflow.platform_ids:
                p = next((x for x in connected if x.id == pid), None)
                if p:
                    chosen_ids[str(p.id)] = p
            rationale.append("fell back to workflow.platform_ids (selector resolved empty)")

        return ResolutionResult(
            platforms=list(chosen_ids.values()),
            rationale=rationale,
        )
