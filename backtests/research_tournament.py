"""Tournament utilities for comparing strategies across markets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

from research_lab import ExperimentResult, score_result


@dataclass(frozen=True)
class TournamentEntry:
    key: str
    score: float
    status: str
    metrics: Dict[str, float]


def tournament(results: Iterable[ExperimentResult]) -> List[TournamentEntry]:
    entries = []
    for r in results:
        entries.append(TournamentEntry(
            key=r.experiment.experiment_id,
            score=score_result(r.metrics, r.metrics.get("complexity", 0.0)),
            status=r.status,
            metrics=dict(r.metrics),
        ))
    return sorted(entries, key=lambda x: x.score, reverse=True)
