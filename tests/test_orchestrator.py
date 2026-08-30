import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backtests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtests.agents.orchestrator import ResearchOrchestrator


def test_orchestrator_creates_agents():
    orch = ResearchOrchestrator(ledger=Path("/tmp/test_orch_ledger.jsonl"))
    a = orch.create_agent("tournament", symbols=["XAUUSD"])
    assert a.agent_type == "tournament"
    assert a.agent_id in orch.active
    assert len(orch.active_agents()) == 1


def test_orchestrator_tournament_run():
    async def _run():
        orch = ResearchOrchestrator(ledger=Path("/tmp/test_orch_ledger2.jsonl"))
        res = await orch.run_tournament(symbols=["XAUUSD"])
        assert res.status == "OK"
        assert res.metrics["total"] == 30  # 10 strategies * 3 tfs
        assert res.metrics["oos_pass"] >= 5
        # review agent should have been spawned
        assert len([h for h in orch.history if h.agent_type == "review"]) == 1
        return True

    assert asyncio.run(_run())


def test_orchestrator_parallel_spawn():
    async def _run():
        orch = ResearchOrchestrator(ledger=Path("/tmp/test_orch_ledger3.jsonl"))
        agents = [orch.create_agent("tournament", symbols=[s]) for s in ["XAUUSD", "SP500"]]
        results = await orch.run_parallel(agents)
        assert len(results) == 2
        assert all(r.status == "OK" for r in results)
        assert len(orch.active) == 0
        return True

    assert asyncio.run(_run())


def test_review_agent_gates():
    async def _run():
        from backtests.agents.review_agent import ReviewAgent
        from backtests.research_lab import Experiment, ExperimentResult
        # fake result with low trades should be failed
        exp = Experiment("id", "strat", "1.0", "XAUUSD", "15m", {})
        good = ExperimentResult(exp, {"trades": 40, "oos_trades": 25, "profit_factor": 1.5, "oos_profit_factor": 1.2, "max_drawdown_pct": 5, "complexity": 1}, "OOS_PASS")
        bad = ExperimentResult(exp, {"trades": 2, "oos_trades": 2, "profit_factor": 99, "oos_profit_factor": 99, "max_drawdown_pct": 0, "complexity": 1}, "VALIDATION_REJECT")
        agent = ReviewAgent([good, bad])
        res = await agent.run()
        assert res.metrics["passed"] == 1
        assert res.metrics["failed"] == 1 or res.metrics["killed"] == 0
        return True

    assert asyncio.run(_run())
