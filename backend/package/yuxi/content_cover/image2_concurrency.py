from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class _ConcurrencyState:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    active: int = 0
    waiting: int = 0
    limit: int = 1


class Image2ConcurrencyLimiter:
    """Limit concurrent image2 jobs independently for each configured account."""

    def __init__(self) -> None:
        self._states: dict[str, _ConcurrencyState] = {}

    @asynccontextmanager
    async def slot(self, owner_uid: str, max_concurrent: int) -> AsyncIterator[None]:
        state = self._states.setdefault(owner_uid, _ConcurrencyState(limit=max_concurrent))
        async with state.condition:
            state.limit = max_concurrent
            state.condition.notify_all()
            state.waiting += 1
            try:
                await state.condition.wait_for(lambda: state.active < state.limit)
                state.active += 1
            finally:
                state.waiting -= 1

        try:
            yield
        finally:
            async with state.condition:
                state.active -= 1
                state.condition.notify_all()


image2_concurrency_limiter = Image2ConcurrencyLimiter()
