"""Fail-closed dose-response scanning and piecewise bracketed search.

The legacy phosphorus optimizer assumed a globally monotone response and moved
the physical upper bound whenever a chemistry solve failed.  This module keeps
numerical failure separate from physical direction: it first samples the full
dose interval, identifies every resolved target crossing, and bisects only
inside a valid local bracket.  A failed evaluation is retried at the same dose
and never changes a bracket.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Mapping, Optional

DoseSearchStatus = Literal[
    "converged_target_met",
    "converged_target_not_met",
    "nonconverged",
    "infeasible",
    "solver_error",
]


class MissingSimulationOutputError(RuntimeError):
    """Raised when a successful solver payload omits a required result."""


class DoseEvaluationError(RuntimeError):
    """Raised when a dose point cannot be evaluated after retries."""


@dataclass(frozen=True)
class DoseEvaluation:
    dose_mmol: float
    residual_p_mg_l: float
    payload: Mapping[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "dose_mmol": self.dose_mmol,
            "residual_p_mg_l": self.residual_p_mg_l,
        }


@dataclass(frozen=True)
class DoseBranch:
    branch_id: str
    dose_low_mmol: float
    dose_high_mmol: float
    residual_low_mg_l: float
    residual_high_mg_l: float
    direction: Literal["decreasing", "increasing"]
    resolved: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "dose_low_mmol": self.dose_low_mmol,
            "dose_high_mmol": self.dose_high_mmol,
            "residual_low_mg_l": self.residual_low_mg_l,
            "residual_high_mg_l": self.residual_high_mg_l,
            "direction": self.direction,
            "resolved": self.resolved,
        }


@dataclass
class DoseSearchResult:
    status: DoseSearchStatus
    selected: Optional[DoseEvaluation]
    evaluations: list[DoseEvaluation] = field(default_factory=list)
    branches: list[DoseBranch] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    monotonic_nonincreasing: bool = False
    monotonicity_resolved: bool = False
    bracket_resolved: bool = False
    iterations_used: int = 0
    final_bounds: Optional[tuple[float, float]] = None

    @property
    def target_met(self) -> bool:
        return self.status == "converged_target_met"


async def _evaluate_with_retries(
    evaluate: Callable[[float], Awaitable[Mapping[str, Any]]],
    dose_mmol: float,
    *,
    residual_key: str,
    retry_count: int,
) -> tuple[Optional[DoseEvaluation], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    for attempt in range(retry_count + 1):
        try:
            payload = await evaluate(dose_mmol)
            if residual_key not in payload:
                raise MissingSimulationOutputError(
                    f"simulation at {dose_mmol:g} mmol/L omitted {residual_key!r}"
                )
            residual = float(payload[residual_key])
            if not math.isfinite(residual):
                raise MissingSimulationOutputError(
                    f"simulation at {dose_mmol:g} mmol/L returned non-finite "
                    f"{residual_key}={residual!r}"
                )
            return DoseEvaluation(dose_mmol, residual, payload), errors
        except Exception as exc:  # The caller receives classified, redacted text.
            errors.append(
                {
                    "dose_mmol": dose_mmol,
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
    return None, errors


def _crosses_target(left: DoseEvaluation, right: DoseEvaluation, target: float) -> bool:
    left_delta = left.residual_p_mg_l - target
    right_delta = right.residual_p_mg_l - target
    # Attribute an exact scan hit to the interval ending at that point. This
    # avoids creating two artificial branches around one exact endpoint.
    return right_delta == 0.0 or (left_delta != 0.0 and left_delta * right_delta < 0.0)


async def scan_and_bracket_dose(
    evaluate: Callable[[float], Awaitable[Mapping[str, Any]]],
    *,
    target_residual_p_mg_l: float,
    max_dose_mmol: float,
    tolerance_mg_l: float,
    max_iterations: int,
    scan_points: int = 9,
    retry_count: int = 1,
    residual_key: str = "residual_p_mg_l",
) -> DoseSearchResult:
    """Scan the dose interval and solve every locally bracketed target crossing.

    The lowest-dose converged branch is selected. Persistent solver/parser
    errors remain explicit and never imply that a dose is physically too high.
    """

    if max_dose_mmol <= 0:
        raise ValueError("max_dose_mmol must be positive")
    if tolerance_mg_l <= 0:
        raise ValueError("tolerance_mg_l must be positive")
    if scan_points < 3:
        raise ValueError("scan_points must be at least 3")

    scan: list[Optional[DoseEvaluation]] = []
    evaluations: list[DoseEvaluation] = []
    errors: list[dict[str, Any]] = []
    for index in range(scan_points):
        dose = max_dose_mmol * index / (scan_points - 1)
        point, point_errors = await _evaluate_with_retries(
            evaluate,
            dose,
            residual_key=residual_key,
            retry_count=retry_count,
        )
        scan.append(point)
        errors.extend(point_errors)
        if point is not None:
            evaluations.append(point)

    valid_scan = [point for point in scan if point is not None]
    if not valid_scan:
        return DoseSearchResult(
            status="solver_error",
            selected=None,
            errors=errors,
            monotonicity_resolved=False,
        )

    monotonicity_resolved = len(valid_scan) == len(scan)
    monotonic_nonincreasing = all(
        right.residual_p_mg_l <= left.residual_p_mg_l + tolerance_mg_l
        for left, right in zip(valid_scan, valid_scan[1:])
    )

    # If no reagent is already sufficient, zero is the only defensible minimum.
    zero = scan[0]
    if zero is not None and zero.residual_p_mg_l <= target_residual_p_mg_l + tolerance_mg_l:
        return DoseSearchResult(
            status="converged_target_met",
            selected=zero,
            evaluations=evaluations,
            errors=errors,
            monotonic_nonincreasing=monotonic_nonincreasing,
            monotonicity_resolved=monotonicity_resolved,
            bracket_resolved=True,
            final_bounds=(0.0, 0.0),
        )

    branches: list[DoseBranch] = []
    branch_pairs: list[tuple[DoseEvaluation, DoseEvaluation]] = []
    for index in range(len(scan) - 1):
        left, right = scan[index], scan[index + 1]
        # An error gap is not a physical bracket.
        if left is None or right is None or not _crosses_target(left, right, target_residual_p_mg_l):
            continue
        direction = (
            "decreasing"
            if right.residual_p_mg_l <= left.residual_p_mg_l
            else "increasing"
        )
        branch = DoseBranch(
            branch_id=f"branch_{len(branches) + 1}",
            dose_low_mmol=left.dose_mmol,
            dose_high_mmol=right.dose_mmol,
            residual_low_mg_l=left.residual_p_mg_l,
            residual_high_mg_l=right.residual_p_mg_l,
            direction=direction,
        )
        branches.append(branch)
        branch_pairs.append((left, right))

    converged_candidates: list[tuple[DoseEvaluation, tuple[float, float], str]] = []
    unresolved_branch = False
    iterations_used = 0
    for branch, (left_start, right_start) in zip(branches, branch_pairs):
        left, right = left_start, right_start
        candidate: Optional[DoseEvaluation] = None
        if abs(left.residual_p_mg_l - target_residual_p_mg_l) <= tolerance_mg_l:
            candidate = left
        elif abs(right.residual_p_mg_l - target_residual_p_mg_l) <= tolerance_mg_l:
            candidate = right

        branch_failed = False
        for _ in range(max_iterations if candidate is None else 0):
            iterations_used += 1
            dose_mid = (left.dose_mmol + right.dose_mmol) / 2.0
            mid, point_errors = await _evaluate_with_retries(
                evaluate,
                dose_mid,
                residual_key=residual_key,
                retry_count=retry_count,
            )
            errors.extend(point_errors)
            if mid is None:
                # Preserve both physical bounds. Alternate numerical controls
                # belong inside ``evaluate``; an unresolved midpoint cannot
                # establish directionality.
                branch_failed = True
                break
            evaluations.append(mid)
            if abs(mid.residual_p_mg_l - target_residual_p_mg_l) <= tolerance_mg_l:
                candidate = mid
                break
            if _crosses_target(left, mid, target_residual_p_mg_l):
                right = mid
            elif _crosses_target(mid, right, target_residual_p_mg_l):
                left = mid
            else:
                branch_failed = True
                break

        if candidate is not None and candidate.residual_p_mg_l <= target_residual_p_mg_l + tolerance_mg_l:
            converged_candidates.append(
                (candidate, (left.dose_mmol, right.dose_mmol), branch.branch_id)
            )
        else:
            unresolved_branch = True
        if branch_failed:
            unresolved_branch = True

    if converged_candidates:
        selected, bounds, selected_branch_id = min(
            converged_candidates, key=lambda item: item[0].dose_mmol
        )
        branches = [
            DoseBranch(**{**branch.__dict__, "resolved": branch.branch_id == selected_branch_id})
            for branch in branches
        ]
        return DoseSearchResult(
            status="converged_target_met",
            selected=selected,
            evaluations=sorted(evaluations, key=lambda point: point.dose_mmol),
            branches=branches,
            errors=errors,
            monotonic_nonincreasing=monotonic_nonincreasing,
            monotonicity_resolved=monotonicity_resolved,
            bracket_resolved=True,
            iterations_used=iterations_used,
            final_bounds=bounds,
        )

    if branches or unresolved_branch:
        status: DoseSearchStatus = "solver_error" if errors else "nonconverged"
    elif not monotonicity_resolved:
        status = "solver_error"
    else:
        max_point = scan[-1]
        if max_point is not None and max_point.residual_p_mg_l > target_residual_p_mg_l + tolerance_mg_l:
            status = "infeasible"
        else:
            status = "converged_target_not_met"

    best = min(
        valid_scan,
        key=lambda point: abs(point.residual_p_mg_l - target_residual_p_mg_l),
    )
    return DoseSearchResult(
        status=status,
        selected=best,
        evaluations=sorted(evaluations, key=lambda point: point.dose_mmol),
        branches=branches,
        errors=errors,
        monotonic_nonincreasing=monotonic_nonincreasing,
        monotonicity_resolved=monotonicity_resolved,
        bracket_resolved=False,
        iterations_used=iterations_used,
        final_bounds=None,
    )


__all__ = [
    "DoseBranch",
    "DoseEvaluation",
    "DoseEvaluationError",
    "DoseSearchResult",
    "DoseSearchStatus",
    "MissingSimulationOutputError",
    "scan_and_bracket_dose",
]
