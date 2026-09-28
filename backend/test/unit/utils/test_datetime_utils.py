import datetime as dt

from yuxi.utils.datetime_utils import SHANGHAI_TZ, format_utc_datetime


def test_format_utc_datetime_treats_database_naive_value_as_utc():
    persisted_utc = dt.datetime(2026, 9, 28, 13, 37, 0)

    assert format_utc_datetime(persisted_utc) == "2026-09-28T13:37:00Z"


def test_format_utc_datetime_converts_aware_value_to_utc():
    shanghai_time = dt.datetime(2026, 9, 28, 21, 37, 0, tzinfo=SHANGHAI_TZ)

    assert format_utc_datetime(shanghai_time) == "2026-09-28T13:37:00Z"
