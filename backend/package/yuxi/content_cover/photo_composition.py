"""图库组合的布局与选择契约；与画布 photo grid 使用相同的行列、跨格结构。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _grid(rows, cols):
    return [dict(row=i // cols, col=i % cols, rowSpan=1, colSpan=1) for i in range(rows * cols)]


PHOTO_LAYOUTS = [
    {"id": f"grid-{r * c}", "name": f"{r * c} 张图片", "rows": r, "cols": c, "cells": _grid(r, c)}
    for r, c in [(1, 2), (1, 3), (2, 2), (2, 3), (3, 3)]
] + [
    {
        "id": key,
        "name": name,
        "rows": rows,
        "cols": cols,
        "cells": [dict(row=r, col=c, rowSpan=rs, colSpan=cs) for r, c, rs, cs in cells],
    }
    for key, name, rows, cols, cells in [
        ("feature-left", "焦点居左", 2, 2, [(0, 0, 2, 1), (0, 1, 1, 1), (1, 1, 1, 1)]),
        ("feature-right", "焦点居右", 2, 2, [(0, 1, 2, 1), (0, 0, 1, 1), (1, 0, 1, 1)]),
        ("feature-top", "焦点居上", 2, 2, [(0, 0, 1, 2), (1, 0, 1, 1), (1, 1, 1, 1)]),
        (
            "hero",
            "主视觉拼贴",
            3,
            3,
            [(0, 0, 2, 2), (0, 2, 1, 1), (1, 2, 1, 1), (2, 0, 1, 1), (2, 1, 1, 1), (2, 2, 1, 1)],
        ),
    ]
]


class PhotoSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_item_id: str | None = Field(default=None, min_length=1, max_length=64)
    focal_x: float = Field(default=0.5, ge=0, le=1)
    focal_y: float = Field(default=0.5, ge=0, le=1)


class PhotoComposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layout_id: str
    slots: list[PhotoSlot] = Field(min_length=2, max_length=9)

    @model_validator(mode="after")
    def validate_layout(self):
        layout = next((item for item in PHOTO_LAYOUTS if item["id"] == self.layout_id), None)
        if layout is None or len(self.slots) != len(layout["cells"]):
            raise ValueError("图片数量与组合布局不匹配")
        return self

    def require_complete(self, primary_id: str):
        if any(not slot.image_item_id for slot in self.slots):
            raise ValueError("请填满图片组合的所有位置")
        if not any(slot.image_item_id == primary_id for slot in self.slots):
            raise ValueError("图片组合必须包含已选首图")

    def render_layout(self):
        layout = next(item for item in PHOTO_LAYOUTS if item["id"] == self.layout_id)
        return {"rows": layout["rows"], "cols": layout["cols"], "cells": layout["cells"], "gap": 8}
