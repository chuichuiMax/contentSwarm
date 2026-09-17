from __future__ import annotations

import pytest
from fastapi import HTTPException

from yuxi.services.dangjia_service import (
    DangjiaContentCreate,
    _composition_layout_id,
    _require_cover_image,
    _resolve_ct_code,
    build_dangjia_brief,
    build_dangjia_form_values,
    build_persona_description,
)

VALID_TEMPLATE_ID = "b585947e-8413-4dd5-a7b5-d1486ec81882"


def make_payload(*, image_count: int = 2, cover_indexes: tuple[int, ...] = (0,), type_name: str = "施工报价"):
    images = []
    for index in range(image_count):
        images.append(
            {
                "templateId": VALID_TEMPLATE_ID if index in cover_indexes else "",
                "objectKey": f"img-{index}.jpg",
                "objectUrl": f"https://obs.example.com/img-{index}.jpg",
            }
        )
    return DangjiaContentCreate.model_validate(
        {
            "serialNo": "202609141600",
            "persona": {
                "age": "30",
                "workYears": "5",
                "serviceCity": "长沙市",
                "introduction": "我从事装修行业五年了，把你的装修需求告诉我",
                "skills": ["工长", "水电"],
                "honors": {"ownerRecommendCount": "10", "servedSiteCount": "12"},
                "tone": "有耐心",
                "serviceAdvantages": ["决策快效率高", "自有工人无转包"],
            },
            "requirementType": {
                "typeName": type_name,
                "quotationInfo": {"houseArea": "115平", "houseType": "三室二厅"},
                "prices": [
                    {"format": "工种总价", "content": "拆除：1954元；人工合计：33341元"},
                    {"format": "人工辅材", "content": "总价：6.6w"},
                ],
                "mySite": "湖南省长沙市岳麓区梅溪湖街道金茂府",
            },
            "tags": ["营销报价", "中式风格"],
            "images": images,
        }
    )


def test_persona_description_composes_all_fields():
    payload = make_payload()
    description = build_persona_description(payload.persona)
    assert "30岁" in description
    assert "5年装修工龄" in description
    assert "长沙市" in description
    assert "我从事装修行业五年了" in description
    assert "技能：工长、水电" in description
    assert "业主推荐10次" in description
    assert "累计服务工地12个" in description
    assert "有耐心" in description


def test_form_values_map_quotation_and_prices():
    values = build_dangjia_form_values(make_payload())
    assert values["external_serial_no"] == "202609141600"
    assert values["external_source"] == "dangjia"
    assert values["project_type"] == "三室二厅"
    assert values["area"] == "115平"
    assert "【工种总价】拆除：1954元" in values["budget"]
    assert "【人工辅材】总价：6.6w" in values["budget"]
    assert "水电" in values["craft_and_materials"]
    assert values["advantage"] == ["决策快效率高", "自有工人无转包"]
    assert values["project_site"] == "湖南省长沙市岳麓区梅溪湖街道金茂府"
    assert values["content_tags"] == ["营销报价", "中式风格"]
    assert values["type_name"] == "施工报价"


def test_form_values_derive_required_fields_from_real_inputs():
    values = build_dangjia_form_values(make_payload())
    assert values["brand_name"] == "长沙市装修工长"
    assert values["audience"] == ["长沙市准备装修三室二厅的业主"]
    assert "115平" in values["pain"][0] and "三室二厅" in values["pain"][0]


def test_require_cover_image_rejects_zero_or_multiple():
    with pytest.raises(HTTPException) as zero:
        _require_cover_image(make_payload(cover_indexes=()).images)
    assert zero.value.detail["error"]["code"] == "DANGJIA_COVER_IMAGE_INVALID"
    with pytest.raises(HTTPException) as multiple:
        _require_cover_image(make_payload(image_count=3, cover_indexes=(0, 2)).images)
    assert multiple.value.detail["error"]["code"] == "DANGJIA_COVER_IMAGE_INVALID"


def test_composition_layout_supports_grid_counts_only():
    assert _composition_layout_id(1) is None
    for count, layout in {2: "grid-2", 3: "grid-3", 4: "grid-4", 6: "grid-6", 9: "grid-9"}.items():
        assert _composition_layout_id(count) == layout
    for count in (5, 7, 8):
        with pytest.raises(HTTPException) as exc:
            _composition_layout_id(count)
        assert exc.value.detail["error"]["code"] == "DANGJIA_IMAGE_COUNT_UNSUPPORTED"


def test_resolve_ct_code_maps_known_type_and_rejects_unknown():
    assert _resolve_ct_code("施工报价") == "CT02"
    with pytest.raises(HTTPException) as exc:
        _resolve_ct_code("开荒保洁")
    assert exc.value.detail["error"]["code"] == "DANGJIA_TYPE_NAME_UNMAPPED"


def test_brief_builds_visual_material_with_cover_first():
    payload = make_payload(image_count=4)
    brief = build_dangjia_brief(
        payload,
        cover_item_id="mli_cover",
        ordered_item_ids=["mli_cover", "mli_b", "mli_c", "mli_d"],
    )
    visual = brief.visual_material
    assert visual is not None
    assert visual.image_item_id == "mli_cover"
    assert visual.hycanvas_template_id == VALID_TEMPLATE_ID
    assert visual.photo_composition is not None
    assert visual.photo_composition.layout_id == "grid-4"
    assert [slot.image_item_id for slot in visual.photo_composition.slots][0] == "mli_cover"
    assert brief.persona["description"]
    assert brief.form_values["budget"]


def test_brief_single_image_has_no_composition():
    payload = make_payload(image_count=1)
    brief = build_dangjia_brief(payload, cover_item_id="mli_cover", ordered_item_ids=["mli_cover"])
    assert brief.visual_material is not None
    assert brief.visual_material.photo_composition is None
    assert brief.visual_material.image_item_id == "mli_cover"


def test_brief_rejects_invalid_template_id_format():
    payload = make_payload()
    payload.images[0].templateId = "not-a-template"
    with pytest.raises(HTTPException) as exc:
        build_dangjia_brief(payload, cover_item_id="mli_cover", ordered_item_ids=["mli_cover", "mli_b"])
    assert exc.value.detail["error"]["code"] == "DANGJIA_TEMPLATE_ID_INVALID"
