from yuxi.services.employee_service import filter_traffic_employees, traffic_owner_uid


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
