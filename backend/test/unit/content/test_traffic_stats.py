from datetime import date

import pytest

from yuxi.services.employee_service import (
    _traffic_created_bounds,
    _traffic_summary,
    filter_traffic_employees,
    traffic_owner_uid,
)


def test_traffic_owner_uid_matches_platform_account():
    assert traffic_owner_uid({"id": "user:abc", "source": "user"}) == "abc"
    assert traffic_owner_uid({"id": "emp-1", "source": "employee"}) == "mp_emp1"


def test_filter_traffic_employees_by_keyword_store_and_department():
    rows = [
        {
            "name": "张三",
            "login_account": "13500000001",
            "employee_code": "H001",
            "current_branch": "芙蓉店",
            "current_department": "设计",
        },
        {
            "name": "李四",
            "login_account": "13500000002",
            "employee_code": "H002",
            "current_branch": "河西店",
            "current_department": "工程",
        },
    ]
    assert filter_traffic_employees(rows, keyword="芙蓉", branch=None, department=None) == [rows[0]]
    assert filter_traffic_employees(rows, keyword=None, branch="河西店", department="工程") == [rows[1]]
    assert filter_traffic_employees(rows, keyword="H001", branch="河西店", department=None) == []


def test_traffic_summary_includes_case_shares():
    rows = [
        {
            "decoration_count": 1,
            "review_notes_count": 2,
            "personal_generated_count": 3,
            "enterprise_generated_count": 4,
            "case_share_count": 5,
        },
        {
            "decoration_count": 10,
            "review_notes_count": 0,
            "personal_generated_count": 1,
            "enterprise_generated_count": 0,
            "case_share_count": 2,
        },
    ]
    assert _traffic_summary(rows) == {
        "decoration": 11,
        "review_notes": 2,
        "personal_generated": 4,
        "enterprise_generated": 4,
        "case_shares": 7,
    }


def test_traffic_created_bounds_rejects_inverted_range():
    with pytest.raises(Exception) as exc_info:
        _traffic_created_bounds(date(2026, 10, 24), date(2026, 10, 1))
    assert exc_info.value.status_code == 422
