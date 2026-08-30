"""ResearchOrchestrator — spawns, reviews, and manages sub-agents for tournaments & improvement loops."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from .base import AgentResult, BaseAgent
from .tournament_agent import TournamentAgent
from .improvement_agent import ImprovementAgent
from .robustness_agent import RobustnessAgent
from .review_agent import ReviewAgent
from .portfolio_agent import PortfolioAgent

import sys
RESEARCH_ROOT = str(Path(__file__).resolve().parents[1])
if RESEARCH_ROOT not in sys.path:
    sys.path.insert(0, RESEARCH_ROOT)

from experiment_manifest import ExperimentManifest  # noqa: E402

LEDGER_DEFAULT = Path(__file__).resolve().parents[1] / "results.jsonl"

AGENT_TYPES: Dict[str, Type[BaseAgent]] = {
    "tournament": TournamentAgent,
    "improvement": ImprovementAgent,
    "robustness": RobustnessAgent,
    "review": ReviewAgent,
    "portfolio": PortfolioAgent,
}


class ResearchOrchestrator:
    """Central manager — creates sub-agents, collects results, applies RESEARCH_PROTOCOL."""

    def __init__(self, ledger: Path | str = LEDGER_DEFAULT):
        self.ledger = Path(ledger)
        self.active: Dict[str, BaseAgent] = {}
        self.history: List[AgentResult] = []
        self.manifests: List[ExperimentManifest] = []

    # ── spawning ──
    def create_agent(self, agent_type: str, **kwargs) -> BaseAgent:
        if agent_type not in AGENT_TYPES:
            raise ValueError(f"unknown agent_type {agent_type!r} expected one of {list(AGENT_TYPES)}")
        agent = AGENT_TYPES[agent_type](**kwargs)
        self.active[agent.agent_id] = agent
        return agent

    async def run_agent(self, agent: BaseAgent) -> AgentResult:
        res = await agent.run()
        self.history.append(res)
        self.active.pop(agent.agent_id, None)
        return res

    async def run_parallel(self, agents: List[BaseAgent]) -> List[AgentResult]:
        return await asyncio.gather(*(self.run_agent(a) for a in agents))

    # ── high-level workflows ──
    async def run_tournament(self, **kwargs) -> AgentResult:
        """Spawn TournamentAgent(s) — caller can shard by symbols for parallelism."""
        agent = self.create_agent("tournament", **kwargs)
        res = await self.run_agent(agent)
        # auto-review + portfolio in background if tournament succeeded
        if res.status == "OK":
            results = res.artifacts.get("results", [])
            curves = res.artifacts.get("curves", {})
            # spawn review + portfolio concurrently, but don't block tournament result
            review = self.create_agent("review", experiment_results=results)
            port = self.create_agent("portfolio", curves=curves) if curves else None
            tasks = [self.run_agent(review)]
            if port: tasks.append(self.run_agent(port))
            await asyncio.gather(*tasks)
        self._append_ledger("tournament", res)
        return res

    async def run_improvement(self, **kwargs) -> AgentResult:
        agent = self.create_agent("improvement", **kwargs)
        res = await self.run_agent(agent)
        # robustness on best if available
        best = res.artifacts.get("best")
        if best and res.status == "OK":
            # need pnls — derive from best OOS run if present
            oos = res.artifacts.get("oos")
            if oos and hasattr(oos, "metrics"):
                # robustness agent will be spawned by caller if they have pnls; skip auto here to keep API simple
                pass
        self._append_ledger("improvement", res)
        return res

    async def run_robustness(self, pnls: List[float], **kwargs) -> AgentResult:
        agent = self.create_agent("robustness", pnls=pnls, **kwargs)
        res = await self.run_agent(agent)
        self._append_ledger("robustness", res)
        return res

    # ── ledger & manifests ──
    def _append_ledger(self, loop_tag: str, result: AgentResult) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        row = dict(ts=datetime.now(timezone.utc).isoformat(), loop_tag=loop_tag, agent_id=result.agent_id,
                   agent_type=result.agent_type, status=result.status, metrics=result.metrics, notes=result.notes)
        with open(self.ledger, "a") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def manifest(self, strategy_id: str, symbol: str, timeframe: str, data_start: str, data_end: str, parameters: Dict[str, Any], hypothesis: str = "") -> ExperimentManifest:
        m = ExperimentManifest(strategy_id=strategy_id, version="1.0", symbol=symbol, timeframe=timeframe,
                               data_start=data_start, data_end=data_end, parameters=parameters, hypothesis=hypothesis)
        self.manifests.append(m)
        return m

    # ── introspection ──
    def leaderboard(self) -> List[AgentResult]:
        return sorted([r for r in self.history if r.agent_type == "tournament"], key=lambda x: x.metrics.get("oos_pass", 0), reverse=True)

    def active_agents(self) -> List[str]:
        return list(self.active.keys())
