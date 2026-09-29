from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from yuxi.image_design.schemas import ImageDesignSaveTarget as SaveTarget

__all__ = ["SaveTarget", "MpDrafts", "MpDraftsUpdate", "MpLibraryCreate"]


class MpDrafts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    redesign: dict[str, Any] = Field(default_factory=dict)
    adapt: dict[str, Any] = Field(default_factory=dict)
    transfer: dict[str, Any] = Field(default_factory=dict)


class MpDraftsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drafts: MpDrafts


class MpLibraryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_library_item_id: str = Field(min_length=1, max_length=64)
    source_gallery_id: str | None = Field(default=None, min_length=1, max_length=100)
    source_role: Literal["source", "reference", "rough"]


class MpImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["source", "reference", "rough"]
    library_item_id: str = Field(min_length=1, max_length=80)
    # Compatibility metadata is never used to resolve or authorize the asset.
    source_item_id: str | None = None
    asset_id: str | None = None


class MpPolishCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workflow: Literal["redesign", "adapt", "transfer"]
    images: list[MpImageInput] = Field(min_length=1, max_length=2)
    description: str = Field(default="", max_length=3000)
    style: str | None = Field(default=None, max_length=80)
    target_space: str | None = Field(default=None, max_length=80)
    layout_type: str | None = Field(default=None, max_length=80)
    extra_element: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=2)

    @field_validator("extra_element")
    @classmethod
    def deduplicate_elements(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_workflow_inputs(self):
        expected = {"redesign": {"source"}, "adapt": {"reference", "rough"}, "transfer": {"reference"}}
        roles = [image.role for image in self.images]
        if set(roles) != expected[self.workflow] or len(roles) != len(set(roles)):
            raise ValueError("图片角色与工作流不匹配")
        if self.workflow == "adapt" and len({image.library_item_id for image in self.images}) != 2:
            raise ValueError("参考图和毛坯图不能是同一张图片")
        if self.workflow == "transfer" and (not self.target_space or not self.layout_type):
            raise ValueError("请选择目标空间和布局")
        return self


class MpTaskCreate(MpPolishCreate):
    refinement_id: str = Field(min_length=8, max_length=80)
    save_target: SaveTarget
    ratio: Literal["portrait", "landscape", "square"] = "portrait"
    count: Literal[2, 4] = 2
    quality: Literal["1k", "2k"] = "1k"
    polished_prompt: str | None = Field(default=None, max_length=20000)
    idempotency_key: str | None = Field(default=None, max_length=128)
