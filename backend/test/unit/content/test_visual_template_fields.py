from yuxi.content.control.visual_template_fields import (
    resolve_hycanvas_template_fields,
    template_fact_sources,
)


def test_template_fact_sources_read_cover_business_variables():
    sources = template_fact_sources(
        {
            "business_variables": {
                "设计风格": "雅致现代",
                "户型": "三室两厅",
                "所在区域": "长沙市",
                "目标人群": ["改善家庭", "新婚"],
                "工艺类型": "水电工艺",
                "从业年限": "8",
            }
        }
    )

    assert sources["design_style"] == "雅致现代"
    assert sources["layout"] == "三室两厅"
    assert sources["service_region"] == "长沙市"
    assert sources["target_audience"] == "改善家庭、新婚"
    assert sources["process_type"] == "水电工艺"
    assert sources["service_years"] == "8年"


def test_resolve_hycanvas_template_fields_fills_job_position():
    fields = resolve_hycanvas_template_fields(
        [
            {
                "kind": "text",
                "label": "岗位",
                "key": "job",
                "semanticRole": "job_position",
                "constraints": {"required": True, "maxChars": 12},
            }
        ],
        visual_text=["标题", "副标题"],
        brief={"business_variables": {"岗位": "项目经理"}},
    )

    assert fields == {"job": "项目经理"}


def test_resolve_hycanvas_template_fields_fills_service_region():
    fields = resolve_hycanvas_template_fields(
        [
            {
                "kind": "text",
                "label": "区域",
                "key": "region",
                "semanticRole": "service_region",
                "constraints": {"required": True, "maxChars": 12},
            }
        ],
        visual_text=["标题", "副标题"],
        brief={"business_variables": {"所在区域": "株洲市"}},
    )

    assert fields == {"region": "株洲市"}


def test_resolve_hycanvas_template_fields_fills_new_cover_roles():
    fields = resolve_hycanvas_template_fields(
        [
            {
                "kind": "text",
                "label": "设计风格",
                "key": "style",
                "semanticRole": "design_style",
                "constraints": {"required": True, "maxChars": 20},
            },
            {
                "kind": "text",
                "label": "年限",
                "key": "years",
                "semanticRole": "service_years",
                "constraints": {"maxChars": 10},
            },
        ],
        visual_text=["标题", "副标题"],
        brief={"form_values": {"设计风格": "写意木构", "从业年限": "12年"}},
    )

    assert fields == {"style": "写意木构", "years": "12年"}


def test_resolve_hycanvas_template_fields_maps_audience_label_to_target_audience():
    fields = resolve_hycanvas_template_fields(
        [
            {
                "kind": "text",
                "label": "新房装修",
                "key": "audience_tag",
                "constraints": {"required": True, "maxChars": 12},
            }
        ],
        visual_text=["别墅预算怎么控", "副标题"],
        brief={"business_variables": {"目标人群": "毛坯"}},
    )

    assert fields == {"audience_tag": "毛坯"}


def test_resolve_hycanvas_template_fields_uses_semantic_role_target_audience():
    fields = resolve_hycanvas_template_fields(
        [
            {
                "kind": "text",
                "label": "<新房装修>",
                "key": "field_3",
                "semanticRole": "target_audience",
                "constraints": {"required": True, "maxChars": 6},
            }
        ],
        visual_text=["标题", "副标题"],
        brief={"business_variables": {"目标人群": "别墅"}, "form_values": {}},
    )

    assert fields == {"field_3": "别墅"}


def test_resolve_hycanvas_template_fields_accepts_chinese_semantic_role():
    fields = resolve_hycanvas_template_fields(
        [
            {
                "kind": "text",
                "label": "法式奶油风",
                "key": "field_4",
                "semanticRole": "设计风格",
                "constraints": {"required": True, "maxChars": 8},
            }
        ],
        visual_text=["标题", "副标题"],
        brief={"business_variables": {"设计风格": "法式奶油风"}},
    )

    assert fields == {"field_4": "法式奶油风"}


def test_resolve_hycanvas_template_fields_accepts_chinese_service_years_role():
    fields = resolve_hycanvas_template_fields(
        [
            {
                "kind": "text",
                "label": "从业年限",
                "key": "years",
                "semanticRole": "从业年限",
                "constraints": {"required": True, "maxChars": 10},
            }
        ],
        visual_text=["标题", "副标题"],
        brief={"business_variables": {"从业年限": "15"}},
    )

    assert fields == {"years": "15年"}


def test_resolve_hycanvas_template_fields_covers_fact_roles_from_business_variables():
    brief = {
        "brand": {"name": "鸿扬家装"},
        "business_variables": {
            "楼盘信息": "洋湖天序",
            "外框面积": "142㎡",
            "设计风格": "雅致现代",
            "户型": "四室两厅",
            "所在区域": "长沙市",
            "目标人群": "毛坯",
            "工艺名称": "水电布管",
            "岗位": "设计师",
            "从业年限": "12",
        },
    }
    declarations = [
        {"kind": "text", "key": "k1", "label": "项目", "semanticRole": "project_name", "constraints": {}},
        {"kind": "text", "key": "k2", "label": "面积", "semanticRole": "project_area", "constraints": {}},
        {"kind": "text", "key": "k3", "label": "风格", "semanticRole": "design_style", "constraints": {}},
        {"kind": "text", "key": "k4", "label": "户型", "semanticRole": "layout", "constraints": {}},
        {"kind": "text", "key": "k5", "label": "区域", "semanticRole": "service_region", "constraints": {}},
        {"kind": "text", "key": "k6", "label": "人群", "semanticRole": "target_audience", "constraints": {}},
        {"kind": "text", "key": "k7", "label": "工艺", "semanticRole": "process_type", "constraints": {}},
        {"kind": "text", "key": "k8", "label": "品牌", "semanticRole": "brand_name", "constraints": {}},
        {"kind": "text", "key": "k9", "label": "岗位", "semanticRole": "job_position", "constraints": {}},
        {"kind": "text", "key": "k10", "label": "年限", "semanticRole": "service_years", "constraints": {}},
    ]
    fields = resolve_hycanvas_template_fields(declarations, visual_text=["主标题", "副标题"], brief=brief)

    assert fields["k1"] == "洋湖天序"
    assert fields["k2"] == "142"
    assert fields["k3"] == "雅致现代"
    assert fields["k4"] == "四室两厅"
    assert fields["k5"] == "长沙市"
    assert fields["k6"] == "毛坯"
    assert fields["k7"] == "水电布管"
    assert fields["k8"] == "鸿扬家装"
    assert fields["k9"] == "设计师"
    assert fields["k10"] == "12年"


def test_resolve_hycanvas_template_fields_infers_role_from_chinese_label():
    fields = resolve_hycanvas_template_fields(
        [
            {
                "kind": "text",
                "key": "field_style",
                "label": "设计风格",
                "constraints": {"maxChars": 20},
            },
            {
                "kind": "text",
                "key": "field_community",
                "label": "楼盘信息",
                "constraints": {"maxChars": 24},
            },
        ],
        visual_text=["标题", "副标题"],
        brief={"business_variables": {"设计风格": "法式奶油风", "楼盘信息": "新芙蓉之都"}},
    )

    assert fields == {"field_style": "法式奶油风", "field_community": "新芙蓉之都"}
