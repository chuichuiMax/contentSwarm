from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services.enterprise_gallery_audience import (
    DEFAULT_AUDIENCE_ID,
    WOODWORK_AUDIENCE_ID,
    enterprise_category_visible_for_audience,
    match_audience_for_branch,
)


def _audience(*, audience_id: str, code: str, name: str, keywords: list[str], is_default: bool, sort_order: int):
    return SimpleNamespace(
        id=audience_id,
        code=code,
        name=name,
        match_keywords=keywords,
        is_default=is_default,
        sort_order=sort_order,
        enabled=True,
    )


def _category(*, category_id: str, visibility: str = "enterprise", parent_id: str | None = None, audience_id: str | None = None):
    return SimpleNamespace(
        id=category_id,
        visibility=visibility,
        parent_id=parent_id,
        audience_id=audience_id,
    )


@pytest.fixture
def audiences():
    return [
        _audience(
            audience_id=DEFAULT_AUDIENCE_ID,
            code="default",
            name="默认员工",
            keywords=[],
            is_default=True,
            sort_order=1000,
        ),
        _audience(
            audience_id=WOODWORK_AUDIENCE_ID,
            code="woodwork",
            name="木作事业部",
            keywords=["木作事业部"],
            is_default=False,
            sort_order=10,
        ),
        _audience(
            audience_id="mga_soft",
            code="softfurnish",
            name="软装事业部",
            keywords=["软装事业部"],
            is_default=False,
            sort_order=20,
        ),
    ]


def test_match_contains_woodwork_branch(audiences):
    matched = match_audience_for_branch("鸿扬家居/木作事业部/直销门店", audiences)
    assert matched.id == WOODWORK_AUDIENCE_ID


def test_match_empty_branch_uses_default(audiences):
    matched = match_audience_for_branch("", audiences)
    assert matched.id == DEFAULT_AUDIENCE_ID


def test_match_unrelated_branch_uses_default(audiences):
    matched = match_audience_for_branch("长沙直营店", audiences)
    assert matched.id == DEFAULT_AUDIENCE_ID


def test_match_softfurnish_branch(audiences):
    matched = match_audience_for_branch("软装事业部-旗舰店", audiences)
    assert matched.id == "mga_soft"


def test_enterprise_visibility_is_mutual_exclusive():
    roots = {
        "case": _category(category_id="case", audience_id=DEFAULT_AUDIENCE_ID),
        "wood": _category(category_id="wood", audience_id=WOODWORK_AUDIENCE_ID),
    }
    child = _category(category_id="wood-child", parent_id="wood", audience_id=None)

    assert enterprise_category_visible_for_audience(
        roots["case"],
        user_audience_id=DEFAULT_AUDIENCE_ID,
        is_admin=False,
        roots_by_id=roots,
        default_audience_id=DEFAULT_AUDIENCE_ID,
    )
    assert not enterprise_category_visible_for_audience(
        roots["wood"],
        user_audience_id=DEFAULT_AUDIENCE_ID,
        is_admin=False,
        roots_by_id=roots,
        default_audience_id=DEFAULT_AUDIENCE_ID,
    )
    assert enterprise_category_visible_for_audience(
        roots["wood"],
        user_audience_id=WOODWORK_AUDIENCE_ID,
        is_admin=False,
        roots_by_id=roots,
        default_audience_id=DEFAULT_AUDIENCE_ID,
    )
    assert not enterprise_category_visible_for_audience(
        roots["case"],
        user_audience_id=WOODWORK_AUDIENCE_ID,
        is_admin=False,
        roots_by_id=roots,
        default_audience_id=DEFAULT_AUDIENCE_ID,
    )
    assert enterprise_category_visible_for_audience(
        child,
        user_audience_id=WOODWORK_AUDIENCE_ID,
        is_admin=False,
        roots_by_id=roots,
        default_audience_id=DEFAULT_AUDIENCE_ID,
    )
    assert enterprise_category_visible_for_audience(
        roots["case"],
        user_audience_id=WOODWORK_AUDIENCE_ID,
        is_admin=True,
        roots_by_id=roots,
        default_audience_id=DEFAULT_AUDIENCE_ID,
    )


def test_match_requires_default_audience():
    with pytest.raises(HTTPException) as exc:
        match_audience_for_branch(
            "木作事业部",
            [
                _audience(
                    audience_id=WOODWORK_AUDIENCE_ID,
                    code="woodwork",
                    name="木作事业部",
                    keywords=["木作事业部"],
                    is_default=False,
                    sort_order=10,
                )
            ],
        )
    assert exc.value.status_code == 500
