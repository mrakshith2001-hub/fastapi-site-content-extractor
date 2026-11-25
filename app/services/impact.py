"""Decide which pages must be re-rendered for a set of file changes."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.db.models import Operation
from app.services.change_detection import asset_type_for


@dataclass(frozen=True)
class PathChange:
    path: str
    operation: Operation


@dataclass
class ImpactPlan:
    to_render: dict[str, list[str]] = field(default_factory=dict)
    to_retire: list[str] = field(default_factory=list)


def plan_reprocessing(
    changes: Iterable[PathChange],
    dependencies: Mapping[str, set[str]],
) -> ImpactPlan:
    """Map changes to the pages they genuinely affect.

    * a created/updated HTML page is rendered itself;
    * a deleted HTML page is retired (its extracted content is kept but flagged);
    * any other file change re-renders only the pages whose dependency set contains it.
      Dependencies include files a page requested but that were missing, so creating a
      previously-404 asset also triggers the right pages.
    """
    reasons: dict[str, list[str]] = defaultdict(list)
    retired: list[str] = []

    dependents: dict[str, set[str]] = defaultdict(set)
    for page, assets in dependencies.items():
        for asset in assets:
            dependents[asset].add(page)

    for change in changes:
        if asset_type_for(change.path) == "html":
            if change.operation == Operation.DELETED:
                retired.append(change.path)
            else:
                reasons[change.path].append(f"page {change.operation.value}")
        for page in sorted(dependents.get(change.path, ())):
            if page != change.path:
                reasons[page].append(f"dependency {change.path} {change.operation.value}")

    for page in retired:
        reasons.pop(page, None)

    return ImpactPlan(to_render=dict(sorted(reasons.items())), to_retire=sorted(retired))
