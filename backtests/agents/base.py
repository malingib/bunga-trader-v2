"""Base agent contract for research orchestrator."""
from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class AgentResult:
    agent_id: str
    agent_type: str
    status: str  # OK | FAIL | SKIP
    metrics: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class BaseAgent(abc.ABC):
    """Minimal async-capable research agent."""

    def __init__(self, agent_type: str, config: Optional[Dict[str, Any]] = None):
        self.agent_id = f"{agent_type}-{uuid.uuid4().hex[:8]}"
        self.agent_type = agent_type
        self.config = config or {}
        self._status = "PENDING"
        self.result: Optional[AgentResult] = None

    @property
    def status(self) -> str:
        return self._status

    @abc.abstractmethod
    async def run(self) -> AgentResult:
        ...

    def _ok(self, **kw) -> AgentResult:
        self._status = "OK"
        self.result = AgentResult(agent_id=self.agent_id, agent_type=self.agent_type, status="OK", **kw)
        return self.result

    def _fail(self, msg: str, **kw) -> AgentResult:
        self._status = "FAIL"
        self.result = AgentResult(agent_id=self.agent_id, agent_type=self.agent_type, status="FAIL", notes=[msg], **kw)
        return self.result
