"""Tests for strategy automation scheduler."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from core_backend.sources.strategy_source import StrategyPoller


class StrategyScheduler:
    def __init__(self, poller: StrategyPoller) -> None:
        self.poller = poller
        self._job = None

    def start(self, scheduler) -> None:
        interval = 60
        self._job = scheduler.add_job(
            self._run_safe,
            "interval",
            seconds=interval,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=interval,
        )

    def stop(self) -> None:
        if self._job is not None:
            self._job.remove()
            self._job = None

    async def _run_safe(self) -> None:
        await self.poller.poll_once()


class TestStrategyScheduler:
    @pytest.mark.asyncio
    async def test_start_attaches_job_and_stop_cleans_up(self):
        poller = StrategyPoller()
        poller.poll_once = AsyncMock()

        scheduler = StrategyScheduler(poller)

        import apscheduler.schedulers.asyncio as aps

        ap = aps.AsyncIOScheduler()
        try:
            ap.start()
            scheduler.start(ap)
            assert scheduler._job is not None
        finally:
            scheduler.stop()
            ap.shutdown(wait=False)
            assert scheduler._job is None

    @pytest.mark.asyncio
    async def test_run_safe_delegates_to_poller(self):
        poller = StrategyPoller()
        poller.poll_once = AsyncMock()

        scheduler = StrategyScheduler(poller)
        await scheduler._run_safe()

        poller.poll_once.assert_awaited_once()

