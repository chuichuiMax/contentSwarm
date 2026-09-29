from __future__ import annotations

import asyncio

import pytest

from yuxi.content_cover.image2_concurrency import Image2ConcurrencyLimiter


@pytest.mark.asyncio
async def test_image2_concurrency_limiter_queues_jobs_above_account_limit():
    limiter = Image2ConcurrencyLimiter()
    release = asyncio.Event()
    two_started = asyncio.Event()
    active = 0
    peak = 0

    async def run_job() -> None:
        nonlocal active, peak
        async with limiter.slot("alice", 2):
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
            await release.wait()
            active -= 1

    tasks = [asyncio.create_task(run_job()) for _ in range(3)]
    await asyncio.wait_for(two_started.wait(), timeout=1)
    await asyncio.sleep(0)

    assert active == 2
    assert peak == 2

    release.set()
    await asyncio.gather(*tasks)
    assert peak == 2


@pytest.mark.asyncio
async def test_image2_concurrency_limiter_is_independent_per_account():
    limiter = Image2ConcurrencyLimiter()
    release = asyncio.Event()
    both_started = asyncio.Event()
    started: set[str] = set()

    async def run_job(owner_uid: str) -> None:
        async with limiter.slot(owner_uid, 1):
            started.add(owner_uid)
            if len(started) == 2:
                both_started.set()
            await release.wait()

    tasks = [asyncio.create_task(run_job(owner_uid)) for owner_uid in ("alice", "bob")]
    await asyncio.wait_for(both_started.wait(), timeout=1)

    assert started == {"alice", "bob"}

    release.set()
    await asyncio.gather(*tasks)
