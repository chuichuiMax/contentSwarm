from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WORKFLOWS = {"style_transfer", "room_adapt", "cross_space"}
WORKFLOW_PROFILE_VERSION = 1
ANALYSIS_SCHEMA_VERSION = 1
PROMPT_PLAN_VERSION = 1
PROMPT_COMPILER_SPEC = f"deterministic-prompt-compiler:v{PROMPT_PLAN_VERSION}"
DEFAULT_VISION_MODEL_SPEC = "zzz:gpt-5.6-luna"
ASPECT_SIZES = {
    "3:4": {"1K": "1152x1536", "2K": "2304x3072"},
    "4:3": {"1K": "1536x1152", "2K": "3072x2304"},
    "1:1": {"1K": "1024x1024", "2K": "2048x2048"},
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageDesignClientCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("客户名称不能为空")
        return value


class RefinementBase(StrictModel):
    user_prompt: str = Field(min_length=1, max_length=3000)
    parent_refinement_id: str | None = Field(default=None, min_length=8, max_length=80)
    edited_prompt: str | None = Field(default=None, min_length=1, max_length=3000)

    @field_validator("user_prompt", "edited_prompt")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value

    @model_validator(mode="after")
    def validate_revision_pair(self):
        if bool(self.parent_refinement_id) != bool(self.edited_prompt):
            raise ValueError("修改优化结果时必须同时提交父级优化记录和编辑内容")
        return self


class StyleTransferRefinementCreate(RefinementBase):
    workflow: Literal["style_transfer"]
    source_material_id: str = Field(min_length=1, max_length=80)
    style_label: str | None = Field(default=None, max_length=80)
    use_prompt_as_style: bool = False

    @model_validator(mode="after")
    def validate_style(self):
        if not self.style_label and not self.use_prompt_as_style:
            raise ValueError("请选择换装风格，或使用补充描述作为风格")
        return self


class RoomAdaptRefinementCreate(RefinementBase):
    workflow: Literal["room_adapt"]
    style_reference_material_id: str = Field(min_length=1, max_length=80)
    raw_structure_material_id: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_distinct_materials(self):
        if self.style_reference_material_id == self.raw_structure_material_id:
            raise ValueError("风格参考图和毛坯结构图不能是同一张图片")
        return self


class CrossSpaceRefinementCreate(RefinementBase):
    workflow: Literal["cross_space"]
    style_reference_material_id: str = Field(min_length=1, max_length=80)
    target_space: str = Field(min_length=1, max_length=80)
    layout: str = Field(min_length=1, max_length=80)
    addons: list[str] = Field(default_factory=list, max_length=12)


ImageDesignRefinementCreate = Annotated[
    StyleTransferRefinementCreate | RoomAdaptRefinementCreate | CrossSpaceRefinementCreate,
    Field(discriminator="workflow"),
]

# The old route name remains import-compatible while using the strict union contract.
ImageDesignPromptRefineCreate = ImageDesignRefinementCreate


class ImageDesignGenerateCreate(StrictModel):
    refinement_id: str = Field(min_length=8, max_length=80)
    aspect_ratio: Literal["3:4", "4:3", "1:1"] = "3:4"
    gen_count: Literal[1, 2, 4] = 1
    clarity: Literal["1K", "2K"] = "1K"
    idempotency_key: str | None = Field(default=None, max_length=128)


class ImageDesignAnalysisCreate(StrictModel):
    material_item_id: str = Field(min_length=1, max_length=80)
    role: Literal["structure_source", "style_reference", "cross_space_style"]


class ImageAnalysisResult(StrictModel):
    role: Literal["structure_source", "style_reference", "cross_space_style"]
    room_type: str = Field(min_length=1, max_length=120)
    structural_features: list[str] = Field(default_factory=list, max_length=30)
    preserve: list[str] = Field(default_factory=list, max_length=30)
    style: str = Field(default="", max_length=200)
    palette: list[str] = Field(default_factory=list, max_length=20)
    materials: list[str] = Field(default_factory=list, max_length=30)
    furniture: list[str] = Field(default_factory=list, max_length=30)
    lighting: list[str] = Field(default_factory=list, max_length=20)
    camera: list[str] = Field(default_factory=list, max_length=20)
    transferable_features: list[str] = Field(default_factory=list, max_length=30)
    exclusions: list[str] = Field(default_factory=list, max_length=30)
    confidence_notes: list[str] = Field(default_factory=list, max_length=20)


class PromptPlan(StrictModel):
    workflow: Literal["style_transfer", "room_adapt", "cross_space"]
    workflow_version: int = WORKFLOW_PROFILE_VERSION
    plan_version: int = PROMPT_PLAN_VERSION
    image_roles: list[dict[str, str]]
    hard_constraints: list[str]
    preserve: list[str]
    style_profile: dict[str, object]
    target_space: dict[str, str] | None = None
    layout: dict[str, str] | None = None
    addons: list[dict[str, str]] = Field(default_factory=list)
    user_intent: str
    materials: list[str] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    lighting: list[str] = Field(default_factory=list)
    camera: list[str] = Field(default_factory=list)
    composition: list[str] = Field(default_factory=list)
    negative_constraints: list[str]
    edited_prompt: str | None = None
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ImageDesignShowcaseCreate(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    style_text: str = Field(min_length=1, max_length=3000)
    image_material_id: str = Field(min_length=1, max_length=80)


class ImageDesignRecognizeCreate(StrictModel):
    """Legacy request retained only so older clients receive an explicit migration error."""

    material_item_id: str = Field(min_length=1, max_length=80)
