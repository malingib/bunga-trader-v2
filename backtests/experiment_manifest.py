"""Reproducible experiment manifests for the research lab."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Dict, Sequence


@dataclass(frozen=True)
class ExperimentManifest:
    strategy_id: str
    version: str
    symbol: str
    timeframe: str
    data_start: str
    data_end: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    parents: Sequence[str] = field(default_factory=tuple)
    hypothesis: str = ""
    code_revision: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=list)

    def experiment_id(self) -> str:
        return sha256(self.canonical_json().encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["experiment_id"] = self.experiment_id()
        return result
