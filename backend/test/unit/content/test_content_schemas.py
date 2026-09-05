import pytest
from pydantic import ValidationError

from yuxi.content.schemas import ContentVisualMaterialSelection


@pytest.mark.parametrize(
    "template_id",
    [
        "xiaohongshu-clean-title",
        "01c7f0bc-3ce5-431b-82e5-7390e9bc246e",
    ],
)
def test_visual_material_accepts_supported_hycanvas_template_ids(template_id: str):
    selection = ContentVisualMaterialSelection(
        image_item_id="image-1",
        hycanvas_template_id=template_id,
    )

    assert selection.hycanvas_template_id == template_id


def test_visual_material_rejects_invalid_hycanvas_template_id():
    with pytest.raises(ValidationError):
        ContentVisualMaterialSelection(
            image_item_id="image-1",
            hycanvas_template_id="not-a-template-id",
        )
