from datetime import timedelta

from yuxi.knowledge.base import PROCESSING_STALE_SECONDS, _is_processing_stale
from yuxi.utils.datetime_utils import utc_isoformat, utc_now


def test_processing_status_is_not_stale_right_after_update():
    now = utc_now()
    assert _is_processing_stale({"updated_at": utc_isoformat(now)}, now=now) is False


def test_processing_status_is_stale_after_grace_period():
    now = utc_now()
    updated = now - timedelta(seconds=PROCESSING_STALE_SECONDS + 1)
    assert _is_processing_stale({"updated_at": utc_isoformat(updated)}, now=now) is True


def test_processing_status_without_timestamp_is_stale():
    assert _is_processing_stale({}) is True
