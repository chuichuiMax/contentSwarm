from pydantic import ValidationError

import pytest

from yuxi.services.business_variable_service import (
    BusinessVariableCreate,
    BusinessVariableUpdate,
    _normalize_ports,
)


def test_business_variable_create_requires_variable_id():
    with pytest.raises(ValidationError):
        BusinessVariableCreate(service_entry="装修家居", content_type_id="t1", variable_id="")


def test_decoration_requires_content_type():
    with pytest.raises(ValidationError):
        BusinessVariableCreate(service_entry="装修家居", content_type_id=None, variable_id="var-1")


def test_review_notes_skips_content_type():
    payload = BusinessVariableCreate(service_entry="好评笔记", content_type_id=None, variable_id="var-1")
    assert payload.content_type_id is None
    assert payload.required is True
    assert payload.enabled is True


def test_review_notes_clears_provided_content_type():
    payload = BusinessVariableCreate(
        service_entry="好评笔记",
        content_type_id="type-should-ignore",
        variable_id="var-1",
    )
    assert payload.content_type_id is None


def test_business_variable_update_allows_partial_fields():
    payload = BusinessVariableUpdate(enabled=False)
    assert payload.model_dump(exclude_unset=True) == {"enabled": False}


def test_business_variable_update_allows_binding_fields():
    payload = BusinessVariableUpdate(
        content_type_id="ct-1",
        variable_id="var-1",
        ports=["app"],
        required=False,
        enabled=True,
    )
    assert payload.model_dump(exclude_unset=True) == {
        "content_type_id": "ct-1",
        "variable_id": "var-1",
        "ports": ["app"],
        "required": False,
        "enabled": True,
    }


def test_business_variable_create_defaults_ports():
    payload = BusinessVariableCreate(service_entry="好评笔记", variable_id="var-1")
    assert payload.ports == ["pc", "app"]


def test_business_variable_create_rejects_empty_ports():
    with pytest.raises(ValidationError):
        BusinessVariableCreate(service_entry="好评笔记", variable_id="var-1", ports=[])


def test_normalize_ports_keeps_order():
    assert _normalize_ports(["app", "pc"]) == ["pc", "app"]


@pytest.mark.asyncio
async def test_ensure_default_business_variables_adds_missing_bindings(monkeypatch):
    from types import SimpleNamespace

    from yuxi.services import business_variable_service as service

    created = []
    existing_keys = {("装修家居", "ct-craft", "v-audience")}

    class FakeRepo:
        async def get_by_key(self, *, service_entry, content_type_id, variable_id):
            if (service_entry, content_type_id, variable_id) in existing_keys:
                return SimpleNamespace(id="exists")
            return None

        async def create(self, data):
            created.append(data)
            return data

    class FakeTypeRepo:
        async def list_types(self):
            return [
                SimpleNamespace(id="ct-craft", name="工艺施工展示"),
                SimpleNamespace(id="ct-quote", name="装修报价清单"),
                SimpleNamespace(id="ct-pit", name="装修避坑分享"),
                SimpleNamespace(id="ct-case", name="装修案例分享"),
                SimpleNamespace(id="ct-edu", name="装修知识科普"),
                SimpleNamespace(id="ct-persona", name="人设自荐"),
            ]

    class FakeVariableRepo:
        async def list_variables(self):
            return [
                SimpleNamespace(id="v-audience", name="目标人群"),
                SimpleNamespace(id="v-area", name="外框面积"),
                SimpleNamespace(id="v-style", name="设计风格"),
                SimpleNamespace(id="v-region", name="所在区域"),
                SimpleNamespace(id="v-role", name="岗位"),
                SimpleNamespace(id="v-years", name="从业年限"),
                SimpleNamespace(id="v-community", name="楼盘信息"),
                SimpleNamespace(id="v-base", name="基础"),
                SimpleNamespace(id="v-wood", name="木制品"),
                SimpleNamespace(id="v-material", name="主材"),
                SimpleNamespace(id="v-household", name="居住人口"),
                SimpleNamespace(id="v-process-type", name="工艺类型"),
                SimpleNamespace(id="v-process-name", name="工艺名称"),
                SimpleNamespace(id="v-stage", name="项目阶段"),
                SimpleNamespace(id="v-designer", name="设计师"),
                SimpleNamespace(id="v-budget", name="预算师"),
                SimpleNamespace(id="v-pm", name="项目经理"),
                SimpleNamespace(id="v-cm", name="客户经理"),
                SimpleNamespace(id="v-craft", name="工匠"),
            ]

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_content_types", fake_ensure)
    monkeypatch.setattr(service, "ensure_default_variables", fake_ensure)
    monkeypatch.setattr(service, "BusinessVariableRepository", lambda _db: FakeRepo())
    monkeypatch.setattr(service, "ContentTypeRepository", lambda _db: FakeTypeRepo())
    monkeypatch.setattr(service, "VariableRepository", lambda _db: FakeVariableRepo())

    await service.ensure_default_business_variables(object())
    keys = {(item["content_type_id"], item["variable_id"]) for item in created}
    assert ("ct-craft", "v-audience") not in keys
    assert ("ct-pit", "v-base") in keys
    assert ("ct-case", "v-household") in keys
    assert ("ct-edu", "v-process-name") in keys
    assert ("ct-persona", "v-role") in keys
    persona_required = {
        item["variable_id"]: item["required"]
        for item in created
        if item["content_type_id"] == "ct-persona"
    }
    assert persona_required["v-audience"] is False
    assert persona_required["v-role"] is True


@pytest.mark.asyncio
async def test_list_business_variables_filters_by_content_type(monkeypatch):
    from types import SimpleNamespace

    from yuxi.services import business_variable_service as service

    bindings = [
        SimpleNamespace(
            id="b1",
            service_entry="装修家居",
            content_type_id="ct-1",
            variable_id="v1",
            ports=["pc"],
            required=True,
            enabled=True,
            created_by="system",
            created_at=None,
            updated_at=None,
            to_dict=lambda self=None: {
                "id": "b1",
                "service_entry": "装修家居",
                "content_type_id": "ct-1",
                "variable_id": "v1",
                "ports": ["pc"],
                "required": True,
                "enabled": True,
            },
        ),
        SimpleNamespace(
            id="b2",
            service_entry="装修家居",
            content_type_id="ct-2",
            variable_id="v1",
            ports=["pc"],
            required=True,
            enabled=True,
            created_by="system",
            created_at=None,
            updated_at=None,
            to_dict=lambda self=None: {
                "id": "b2",
                "service_entry": "装修家居",
                "content_type_id": "ct-2",
                "variable_id": "v1",
                "ports": ["pc"],
                "required": True,
                "enabled": True,
            },
        ),
    ]
    for item in bindings:
        item.to_dict = (lambda row: (lambda: {
            "id": row.id,
            "service_entry": row.service_entry,
            "content_type_id": row.content_type_id,
            "variable_id": row.variable_id,
            "ports": row.ports,
            "required": row.required,
            "enabled": row.enabled,
        }))(item)

    class FakeBindingRepo:
        async def list_items(self):
            return bindings

    class FakeTypeRepo:
        async def list_types(self):
            return [
                SimpleNamespace(id="ct-1", name="工艺施工展示"),
                SimpleNamespace(id="ct-2", name="装修报价清单"),
            ]

    class FakeVariableRepo:
        async def list_variables(self):
            return [SimpleNamespace(id="v1", name="目标人群", variable_code="FWTD0100", ports=["pc", "app"])]

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_business_variables", fake_ensure)
    monkeypatch.setattr(service, "BusinessVariableRepository", lambda _db: FakeBindingRepo())
    monkeypatch.setattr(service, "ContentTypeRepository", lambda _db: FakeTypeRepo())
    monkeypatch.setattr(service, "VariableRepository", lambda _db: FakeVariableRepo())

    result = await service.list_business_variables(object(), content_type_id="ct-2")
    assert [item["id"] for item in result["business_variables"]] == ["b2"]
