"""Small exact path-cover resolver for controlled handoff experiments."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AssignmentState(str, Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    REJECTED = "rejected"


@dataclass(frozen=True)
class HandoffOption:
    candidate_id: str
    origin_track_id: str
    destination_track_id: str
    score: float
    visual_coverage: float
    observed_us: int
    origin_first_observed_us: int | None = None
    destination_first_observed_us: int | None = None

    @property
    def utility(self) -> float:
        # Coverage tempers evidence-poor timing matches without erasing them.
        return self.score * (0.5 + 0.5 * self.visual_coverage)


@dataclass(frozen=True)
class AssignedHandoff:
    option: HandoffOption
    state: AssignmentState
    margin: float
    reason: str


@dataclass(frozen=True)
class SequenceResolution:
    handoffs: tuple[AssignedHandoff, ...]
    sequences: tuple[tuple[str, ...], ...]
    confirmed_sequences: tuple[tuple[str, ...], ...]
    total_utility: float


def resolve_sequences(
    options: list[HandoffOption],
    *,
    minimum_utility: float = 0.20,
    confirmation_utility: float = 0.38,
    confirmation_coverage: float = 0.50,
    ambiguity_margin: float = 0.08,
) -> SequenceResolution:
    """Choose a maximum-utility acyclic one-in/one-out path cover."""
    if not 0 <= minimum_utility <= confirmation_utility <= 1:
        raise ValueError("assignment utility limits are invalid")
    if not 0 <= confirmation_coverage <= 1 or ambiguity_margin < 0:
        raise ValueError("assignment confidence limits are invalid")
    directed_pairs = {
        (item.origin_track_id, item.destination_track_id) for item in options
    }
    reverse_chronology = {
        item.candidate_id
        for item in options
        if (item.destination_track_id, item.origin_track_id) in directed_pairs
        and item.origin_first_observed_us is not None
        and item.destination_first_observed_us is not None
        and item.origin_first_observed_us > item.destination_first_observed_us
    }
    eligible = [
        item
        for item in options
        if item.utility >= minimum_utility
        and item.candidate_id not in reverse_chronology
    ]
    if len(eligible) > 24:
        raise ValueError("exact resolver supports at most 24 eligible options")
    ordered = sorted(
        eligible,
        key=lambda item: (item.utility, item.visual_coverage, item.observed_us),
        reverse=True,
    )
    # Optimize evidence above the no-handoff baseline. Without this edge cost,
    # two marginal hops can beat one strong direct handoff simply because their
    # unadjusted utilities are added together.
    gains = {
        item.candidate_id: item.utility - minimum_utility for item in ordered
    }
    best: tuple[float, tuple[HandoffOption, ...]] = (0.0, ())

    def search(
        index: int,
        chosen: tuple[HandoffOption, ...],
        outgoing: set[str],
        incoming: set[str],
        total: float,
    ) -> None:
        nonlocal best
        optimistic = total + sum(gains[item.candidate_id] for item in ordered[index:])
        if optimistic + 1e-12 < best[0]:
            return
        if index == len(ordered):
            signature = tuple(sorted(item.candidate_id for item in chosen))
            best_signature = tuple(sorted(item.candidate_id for item in best[1]))
            if total > best[0] + 1e-12 or (
                abs(total - best[0]) <= 1e-12 and signature < best_signature
            ):
                best = (total, chosen)
            return
        item = ordered[index]
        if (
            item.origin_track_id not in outgoing
            and item.destination_track_id not in incoming
            and item.origin_track_id != item.destination_track_id
            and not _would_cycle(chosen, item)
        ):
            search(
                index + 1,
                chosen + (item,),
                outgoing | {item.origin_track_id},
                incoming | {item.destination_track_id},
                total + gains[item.candidate_id],
            )
        search(index + 1, chosen, outgoing, incoming, total)

    search(0, (), set(), set(), 0.0)
    selected_ids = {item.candidate_id for item in best[1]}
    assignments: list[AssignedHandoff] = []
    for item in options:
        if item.candidate_id not in selected_ids:
            reason = (
                "reverse_chronology"
                if item.candidate_id in reverse_chronology
                else (
                    "insufficient_utility"
                    if item.utility < minimum_utility
                    else "dominated_or_conflicting"
                )
            )
            assignments.append(
                AssignedHandoff(item, AssignmentState.REJECTED, 0.0, reason)
            )
            continue
        competitors = [
            candidate.utility
            for candidate in eligible
            if candidate.candidate_id != item.candidate_id
            and (
                candidate.origin_track_id == item.origin_track_id
                or candidate.destination_track_id == item.destination_track_id
            )
        ]
        margin = item.utility - max(competitors, default=0.0)
        confirmed = (
            item.utility >= confirmation_utility
            and item.visual_coverage >= confirmation_coverage
            and margin >= ambiguity_margin
        )
        assignments.append(
            AssignedHandoff(
                item,
                AssignmentState.CONFIRMED if confirmed else AssignmentState.PENDING,
                round(margin, 6),
                "globally_selected" if confirmed else "needs_confirmation",
            )
        )
    assigned = tuple(assignments)
    return SequenceResolution(
        handoffs=assigned,
        sequences=_sequences(best[1]),
        confirmed_sequences=_sequences(
            tuple(
                item.option
                for item in assigned
                if item.state == AssignmentState.CONFIRMED
            )
        ),
        total_utility=round(sum(item.utility for item in best[1]), 6),
    )


def _would_cycle(chosen: tuple[HandoffOption, ...], candidate: HandoffOption) -> bool:
    successors = {item.origin_track_id: item.destination_track_id for item in chosen}
    current = candidate.destination_track_id
    while current in successors:
        current = successors[current]
        if current == candidate.origin_track_id:
            return True
    return False


def _sequences(options: tuple[HandoffOption, ...]) -> tuple[tuple[str, ...], ...]:
    successors = {item.origin_track_id: item.destination_track_id for item in options}
    incoming = {item.destination_track_id for item in options}
    starts = sorted(set(successors) - incoming)
    result: list[tuple[str, ...]] = []
    for start in starts:
        path = [start]
        while path[-1] in successors:
            path.append(successors[path[-1]])
        result.append(tuple(path))
    return tuple(result)
