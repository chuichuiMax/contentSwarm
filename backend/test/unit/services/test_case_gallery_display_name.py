from yuxi.services.material_library_service import (
    format_enterprise_case_gallery_display_name,
    infer_case_gallery_fields_from_name,
    normalize_case_building_prefix,
    resolve_enterprise_case_gallery_fields,
    strip_case_building_unit,
)


def test_strip_building_unit():
    assert strip_case_building_unit("万科松湖天地 D6-202") == "万科松湖天地"
    assert strip_case_building_unit("桂语云峰 3栋1202") == "桂语云峰"


def test_format_case_gallery_display_name():
    assert (
        format_enterprise_case_gallery_display_name("万科松湖天地 D6-202", "雅致现代", "189")
        == "万科松湖天地 -雅致现代-189㎡"
    )


def test_infer_from_legacy_folder_name():
    building, area = infer_case_gallery_fields_from_name("万科松湖天地 D6-202-雅致现代-189㎡", "雅致现代")
    assert building == "万科松湖天地"
    assert area == "189"
    building2, area2 = infer_case_gallery_fields_from_name("臻湾江4-341-轻欧简美-306㎡", "轻欧简美")
    assert building2 == "臻湾江"
    assert area2 == "306"
    building3, area3 = infer_case_gallery_fields_from_name("雅致现代-洋湖映-240㎡", "雅致现代")
    assert building3 == "洋湖映"
    assert area3 == "240"
    assert normalize_case_building_prefix("观山印A6栋2201") == "观山印"


def test_resolve_prefers_name_over_stale_building_field():
    building, area = resolve_enterprise_case_gallery_fields(
        "中央小镇-雅致现代-500㎡",
        "雅致现代",
        "中央小镇-雅致现代",
        "500",
    )
    assert building == "中央小镇"
    assert area == "500"
    assert (
        format_enterprise_case_gallery_display_name(building, "雅致现代", area)
        == "中央小镇 -雅致现代-500㎡"
    )
    building2, area2 = resolve_enterprise_case_gallery_fields(
        "万科松湖天地 D6-202-雅致现代-189㎡",
        "雅致现代",
        "万科松湖天地 D6-202-雅致现代",
        "189",
    )
    assert building2 == "万科松湖天地"
    assert area2 == "189"
