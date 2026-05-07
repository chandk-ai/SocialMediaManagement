"""TargetSelector — declarative routing of a single workflow run to specific
social accounts.

A workflow can be configured with a default selector (e.g. "all my Instagram
accounts tagged 'marketing'") and an inbound trigger (WhatsApp / Telegram /
the in-app prompt) can override it for a specific run by parsing the user's
directive (`DirectiveRouter` does that).

Selectors are *combined additively*. Resolution = union of:
* explicit_platform_ids
* every account whose `plugin_name` is in `all_of_platforms`
* every account whose `account_handle` matches one in `by_handle`
* every account whose tags intersect `tagged`

If the resulting set is empty, `TargetResolver` falls back to the workflow's
`platform_ids` so a vague directive never silently drops a publish.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ids import PlatformId


@dataclass(frozen=True, slots=True)
class TargetSelector:
    explicit_platform_ids: tuple[PlatformId, ...] = ()
    all_of_platforms: tuple[str, ...] = ()       # plugin names: "instagram", "linkedin", ...
    by_handle: tuple[str, ...] = ()              # ["@brand", "@marketing"]
    tagged: tuple[str, ...] = ()                 # ["marketing", "us-region"]
    exclude_platform_ids: tuple[PlatformId, ...] = ()
    exclude_plugins: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (
            self.explicit_platform_ids
            or self.all_of_platforms
            or self.by_handle
            or self.tagged
        )

    def merged_with(self, other: "TargetSelector") -> "TargetSelector":
        """Combine two selectors — used when a directive narrows or broadens
        the workflow's default selector."""
        return TargetSelector(
            explicit_platform_ids=tuple({*self.explicit_platform_ids, *other.explicit_platform_ids}),
            all_of_platforms=tuple({*self.all_of_platforms, *other.all_of_platforms}),
            by_handle=tuple({*self.by_handle, *other.by_handle}),
            tagged=tuple({*self.tagged, *other.tagged}),
            exclude_platform_ids=tuple({*self.exclude_platform_ids, *other.exclude_platform_ids}),
            exclude_plugins=tuple({*self.exclude_plugins, *other.exclude_plugins}),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "TargetSelector":
        return cls(
            explicit_platform_ids=tuple(PlatformId(p) for p in data.get("explicit_platform_ids") or []),
            all_of_platforms=tuple(data.get("all_of_platforms") or []),
            by_handle=tuple(data.get("by_handle") or []),
            tagged=tuple(data.get("tagged") or []),
            exclude_platform_ids=tuple(PlatformId(p) for p in data.get("exclude_platform_ids") or []),
            exclude_plugins=tuple(data.get("exclude_plugins") or []),
        )

    def to_dict(self) -> dict:
        return {
            "explicit_platform_ids": [str(p) for p in self.explicit_platform_ids],
            "all_of_platforms": list(self.all_of_platforms),
            "by_handle": list(self.by_handle),
            "tagged": list(self.tagged),
            "exclude_platform_ids": [str(p) for p in self.exclude_platform_ids],
            "exclude_plugins": list(self.exclude_plugins),
        }
