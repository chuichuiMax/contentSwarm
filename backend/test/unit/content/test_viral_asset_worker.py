from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from yuxi.services.viral_asset_worker import (
    MAX_INTERRUPT_RETRIES,
    _failure_message,
    _interrupt_count,
)


def test_failure_message_explains_empty_cancel_and_timeout():
    assert "中断" in _failure_message(asyncio.CancelledError())
    assert "超时" in _failure_message(TimeoutError())
    assert _failure_message(ValueError("原文已更新")) == "原文已更新"


def test_interrupt_count_reads_prepared_json():
    assert _interrupt_count(SimpleNamespace(prepared_json=None)) == 0
    assert _interrupt_count(SimpleNamespace(prepared_json={"interrupt_count": 2})) == 2
    assert _interrupt_count(SimpleNamespace(prepared_json={"interrupt_count": "bad"})) == 0
    assert MAX_INTERRUPT_RETRIES >= 1
