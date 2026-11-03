from yuxi.content_cover.ai_cover_prompt import (
    AI_COVER_CATALOG_SAMPLE_SUBTITLE,
    AI_COVER_CATALOG_SAMPLE_TITLE,
    ai_cover_prompt_body_for_catalog,
    job_selection_for_variant,
    materialize_ai_cover_prompt,
)


def test_materialize_replaces_catalog_samples_in_p16_body():
    catalog = ai_cover_prompt_body_for_catalog("p16")
    assert AI_COVER_CATALOG_SAMPLE_TITLE in catalog
    live = materialize_ai_cover_prompt(
        catalog,
        title="水电阶段，先盯紧隐蔽验收",
        subtitle="190㎡洋湖天序",
    )
    assert AI_COVER_CATALOG_SAMPLE_TITLE not in live
    assert AI_COVER_CATALOG_SAMPLE_SUBTITLE not in live
    assert "水电阶段，先盯紧隐蔽验收" in live
    assert "190㎡洋湖天序" in live


def test_materialize_is_noop_for_runtime_built_prompt():
    built = job_selection_for_variant(
        "p16",
        title="水电阶段，先盯紧隐蔽验收",
        subtitle="190㎡洋湖天序",
    ).prompt
    live = materialize_ai_cover_prompt(
        built,
        title="水电阶段，先盯紧隐蔽验收",
        subtitle="190㎡洋湖天序",
    )
    assert live == built
