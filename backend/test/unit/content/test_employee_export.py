import io

import pytest
from openpyxl import load_workbook

from yuxi.services import employee_service


@pytest.mark.asyncio
async def test_export_preserves_all_rows_display_values_and_text(monkeypatch):
    async def listing(db, keyword=None):
        assert db == "db"
        assert keyword is None
        return {
            "employees": [
                {
                    "employee_code": f"00{index}",
                    "name": "=1+1",
                    "current_branch": "分部",
                    "current_department": "部门",
                    "login_account": "0013800000000",
                    "gender": "female",
                    "age": None,
                    "login_port": ["app", "pc"],
                    "role": "普通用户",
                    "rough_image_count": index,
                    "enterprise_rough_image_count": index + 2,
                    "enabled": index != 0,
                }
                for index in range(25)
            ]
        }

    monkeypatch.setattr(employee_service, "list_employees", listing)
    sheet = load_workbook(io.BytesIO(await employee_service.export_employees("db"))).active
    assert sheet.max_row == 26
    assert list(next(sheet.values)) == [
        "序号",
        "员工编码",
        "姓名",
        "当前分部",
        "当前部门",
        "登录账号",
        "性别",
        "年龄",
        "登录端口",
        "角色",
        "毛坯图上传数（我的素材）",
        "毛坯图上传数（企业共享）",
        "状态",
    ]
    assert list(sheet.values)[1] == (
        1,
        "000",
        "=1+1",
        "分部",
        "部门",
        "0013800000000",
        "女",
        "-",
        "PC&APP",
        "普通用户",
        0,
        2,
        "禁用",
    )
    assert sheet["C2"].data_type == "s"
    assert sheet["B2"].data_type == sheet["F2"].data_type == "s"
    assert sheet["A26"].value == 25
    assert sheet["M26"].value == "启用"
