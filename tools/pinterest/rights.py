"""The rights gate. Every candidate for a photo pin (and, by default, every
prompt for a text pin) passes through `RightsGate.check` before rendering.

An excluded asset reaching the renderer is a bug, not a warning:
`RightsViolation` propagates to a non-zero exit.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from .catalog import Pose, PromptText
from .provenance import Provenance


class RightsViolation(RuntimeError):
    """Raised when an excluded asset reaches rendering."""


@dataclass
class Exclusion:
    pose_id: str
    rule: str          # filename_pattern | excluded_shoot | excluded_pose_id
    detail: str


@dataclass
class RightsGate:
    filename_patterns: list[str]
    excluded_shoots: set[str]
    excluded_pose_ids: set[str]
    provenance: dict[str, Provenance] = field(default_factory=dict)
    apply_to_text_pins: bool = True

    @classmethod
    def from_config(cls, cfg: dict, provenance: dict[str, Provenance]) -> "RightsGate":
        ex = cfg["exclusions"]
        return cls(
            filename_patterns=list(ex["filename_patterns"]),
            excluded_shoots=set(ex["excluded_shoots"]),
            excluded_pose_ids=set(ex["excluded_pose_ids"]),
            provenance=provenance,
            apply_to_text_pins=bool(ex.get("apply_to_text_pins", True)),
        )

    # -- evaluation ----------------------------------------------------------

    def _matches_pattern(self, filename: str) -> str | None:
        for pattern in self.filename_patterns:
            if fnmatch.fnmatchcase(filename, pattern):
                return pattern
        return None

    def reasons(self, pose: Pose) -> list[Exclusion]:
        """Every rule this pose trips (all three layers, always evaluated)."""
        found: list[Exclusion] = []
        prov = self.provenance.get(pose.id)
        names = list(pose.image_filenames)
        if prov and prov.source_file:
            names.insert(0, prov.source_file)
        for name in names:
            pattern = self._matches_pattern(name)
            if pattern:
                found.append(Exclusion(pose.id, "filename_pattern", f"{name} ~ {pattern}"))
                break
        if prov and prov.shoot in self.excluded_shoots:
            found.append(Exclusion(pose.id, "excluded_shoot", prov.shoot))
        if pose.id in self.excluded_pose_ids:
            found.append(Exclusion(pose.id, "excluded_pose_id", pose.id))
        return found

    def is_excluded(self, pose: Pose) -> bool:
        return bool(self.reasons(pose))

    def check(self, pose: Pose) -> None:
        """Hard gate: raise if the pose is excluded by any rule."""
        reasons = self.reasons(pose)
        if reasons:
            rules = ", ".join(f"{r.rule} ({r.detail})" for r in reasons)
            raise RightsViolation(
                f"RIGHTS VIOLATION: pose {pose.id} ({pose.slug}) reached the renderer "
                f"but is excluded by: {rules}")

    def check_prompt(self, prompt: PromptText, poses_by_id: dict[str, Pose]) -> None:
        if not self.apply_to_text_pins:
            return
        for pid in prompt.pose_ids:
            pose = poses_by_id.get(pid)
            if pose is not None and self.is_excluded(pose):
                raise RightsViolation(
                    f"RIGHTS VIOLATION: prompt {prompt.id} reached the renderer but its "
                    f"source pose {pid} is excluded")

    def prompt_excluded(self, prompt: PromptText, poses_by_id: dict[str, Pose]) -> bool:
        if not self.apply_to_text_pins:
            return False
        return any(pid in poses_by_id and self.is_excluded(poses_by_id[pid])
                   for pid in prompt.pose_ids)

    # -- reporting -----------------------------------------------------------

    def report(self, poses: list[Pose]) -> dict:
        by_rule: dict[str, set[str]] = {"filename_pattern": set(), "excluded_shoot": set(),
                                        "excluded_pose_id": set()}
        excluded: dict[str, list[Exclusion]] = {}
        for pose in poses:
            reasons = self.reasons(pose)
            if reasons:
                excluded[pose.id] = reasons
                for r in reasons:
                    by_rule[r.rule].add(pose.id)
        return {"excluded": excluded, "by_rule": {k: sorted(v) for k, v in by_rule.items()},
                "total": len(excluded), "scanned": len(poses)}

    @staticmethod
    def format_report(report: dict, prompts_excluded: int | None = None) -> str:
        lines = [f"Rights exclusions: {report['total']} of {report['scanned']} poses excluded"]
        for rule, ids in report["by_rule"].items():
            lines.append(f"  by {rule}: {len(ids)}")
        for pid, reasons in sorted(report["excluded"].items()):
            lines.append(f"    {pid}: " + "; ".join(f"{r.rule}={r.detail}" for r in reasons))
        if prompts_excluded is not None:
            lines.append(f"  text prompts withheld (source pose excluded): {prompts_excluded}")
        return "\n".join(lines)
