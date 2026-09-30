from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from yuxi.services.viral_asset_worker import (
    CRAFT_KB_EXCLUDED_VARIABLE_CODES,
    MAX_INTERRUPT_RETRIES,
    _failure_message,
    _interrupt_count,
    filter_allowed_variable_codes,
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


def test_craft_kb_excludes_quote_variables_from_preparation_slots():
    codes = ["process", "product", "quote_type", "price", "persona_fact"]
    filtered = filter_allowed_variable_codes(codes, kb_content_type_code="CT06")
    assert CRAFT_KB_EXCLUDED_VARIABLE_CODES.isdisjoint(filtered)
    assert "process" in filtered
    assert filter_allowed_variable_codes(codes, kb_content_type_code="CT02") == codes
