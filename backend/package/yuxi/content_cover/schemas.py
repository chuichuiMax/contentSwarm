from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

COVER_SIZES = {"1080x1440", "1080x1080"}
AI_MODES = {"text_to_image", "image_to_image", "multi_reference", "mask"}


class HyCanvasDesignCreate(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=64)
    template_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=(
            r"^(?:xiaohongshu-[a-z0-9-]+|"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
        )
    )
    title: str = Field(min_length=1, max_length=200)
    fields: dict[str, str]
    image_asset_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 20:
            raise ValueError("模板字段数量不能超过 20")
        if any(not label.strip() or len(text) > 500 for label, text in value.items()):
            raise ValueError("模板字段名称不能为空，内容不能超过 500 字")
        return value


class HyCanvasDesignSync(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=64)


class HyCanvasTemplatePreviewCreate(BaseModel):
    image_item_id: str = Field(min_length=1, max_length=64)


class HyCanvasEditorSessionCreate(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=64)
    return_url: str = Field(min_length=1, max_length=2000)
    return_label: str = Field(default="返回 ContentFlow", min_length=1, max_length=40)


class Image2GlobalConfigUpdate(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    model: str = Field(default="gpt-image-2", min_length=1, max_length=255)

    @field_validator("base_url")
    @classmethod
    def strip_base_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("image2 Base URL 不能为空")
        return value

    @field_validator("api_key")
    @classmethod
    def strip_api_key(cls, value: str | None) -> str | None:
        value = value.strip() if value is not None else None
        return value or None

    @field_validator("model")
    @classmethod
    def strip_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("image2 模型不能为空")
        return value


class Image2ConfigTestRequest(Image2GlobalConfigUpdate):
    """A draft configuration may be verified before or after it is saved."""


class Image2CapabilityProfile(BaseModel):
    model: str
    reachable: bool
    model_discovered: bool | None = None
    supports_generation: bool
    supports_edit: bool
    supports_multi_reference: bool
    supports_mask: bool
    supports_async: bool | None = None
    unsupported_parameters: list[str] = Field(default_factory=list)
    checked_at: datetime
    message: str = ""


class CoverComposeCreate(BaseModel):
    asset_ids: list[str] = Field(min_length=2, max_length=9)
    template_id: str
    theme_id: str = "editorial_ink"
    size: str = "1080x1440"
    layout: dict[str, Any] = Field(default_factory=dict)
    content_task_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("asset_ids")
    @classmethod
    def unique_asset_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("asset_ids 不能重复")
        return value

    @field_validator("size")
    @classmethod
    def supported_size(cls, value: str) -> str:
        if value not in COVER_SIZES:
            raise ValueError("仅支持 1080x1440 或 1080x1080")
        return value


class SlotLayoutOverride(BaseModel):
    x_offset: float = Field(default=0, ge=-0.02, le=0.02)
    y_offset: float = Field(default=0, ge=-0.02, le=0.02)
    font_scale: float = Field(default=1, ge=0.85, le=1.15)


class CoverGenerateCreate(BaseModel):
    mode: Literal["text_to_image", "image_to_image", "multi_reference", "mask"]
    content_task_id: str | None = None
    source_asset_ids: list[str] = Field(default_factory=list, max_length=9)
    template_asset_id: str | None = None
    mask_asset_id: str | None = None
    title: str = Field(default="", max_length=60)
    prompt: str = Field(default="", max_length=8000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    size: str = "1080x1440"
    n: int = Field(default=1, ge=1, le=4)
    parameters: dict[str, Any] = Field(default_factory=dict)
    copy_overrides: dict[str, str] = Field(default_factory=dict)
    layout_overrides: dict[str, SlotLayoutOverride] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("size")
    @classmethod
    def supported_size(cls, value: str) -> str:
        if value not in COVER_SIZES:
            raise ValueError("仅支持 1080x1440 或 1080x1080")
        return value

    @field_validator("source_asset_ids")
    @classmethod
    def unique_source_assets(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("source_asset_ids 不能重复")
        return value

    @model_validator(mode="after")
    def validate_mode_inputs(self):
        if self.mode == "text_to_image":
            if not (self.prompt.strip() or self.content_task_id):
                raise ValueError("文生图需要提示词或内容任务")
            if self.source_asset_ids or self.template_asset_id or self.mask_asset_id:
                raise ValueError("文生图不能携带参考图或蒙版")
        if self.mode == "image_to_image":
            if len(self.source_asset_ids) != 1:
                raise ValueError("图生图需要且仅需要一张原图")
            if self.template_asset_id or self.mask_asset_id:
                raise ValueError("单图图生图不能携带模板图或蒙版")
        if self.mode == "multi_reference":
            reference_count = len(self.source_asset_ids) + int(bool(self.template_asset_id))
            if reference_count < 2:
                raise ValueError("多图参考至少需要两张参考图")
            if self.mask_asset_id:
                raise ValueError("多图参考不能携带蒙版")
            if self.template_asset_id and len(self.source_asset_ids) != 1:
                raise ValueError("模板复刻需要且仅需要一张原图")
        if self.mode == "mask":
            if len(self.source_asset_ids) != 1:
                raise ValueError("蒙版生成需要且仅需要一张原图")
            if not self.mask_asset_id:
                raise ValueError("蒙版生成需要蒙版图")
            if self.template_asset_id:
                raise ValueError("蒙版生成不能同时携带模板图")
        return self


class NormalizedBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def stays_inside_canvas(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("归一化区域必须位于画布内")
        return self


class TemplateTextFillRun(BaseModel):
    start: int = Field(ge=0, le=120)
    end: int = Field(gt=0, le=120)
    fill: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")

    @model_validator(mode="after")
    def has_positive_range(self):
        if self.end <= self.start:
            raise ValueError("文字颜色分段结束位置必须大于开始位置")
        return self


class TemplateTextStyle(BaseModel):
    fill: str = "#FFFFFF"
    fill_runs: list[TemplateTextFillRun] = Field(default_factory=list, max_length=24)
    stroke: str | None = None
    stroke_width_ratio: float = Field(default=0, ge=0, le=0.1)
    font_size_ratio: float = Field(gt=0, le=0.5)
    bold: bool = False
    align: Literal["left", "center", "right"] = "center"
    panel_fill: str | None = None
    panel_opacity: float = Field(default=1, ge=0, le=1)
    panel_radius_ratio: float = Field(default=0, ge=0, le=0.5)
    font_family: str | None = Field(default=None, max_length=120)
    font_size_px: float | None = Field(default=None, ge=8, le=512)
    font_weight: Literal[400, 500, 600, 700, 800, 900] | None = None
    letter_spacing: float | None = Field(default=None, ge=-20, le=80)
    shadow: bool | None = None
    shadow_color: str | None = None
    shadow_blur: float | None = Field(default=None, ge=0, le=80)
    shadow_offset_x: float | None = Field(default=None, ge=-100, le=100)
    shadow_offset_y: float | None = Field(default=None, ge=-100, le=100)
    editor_x: float | None = Field(default=None, ge=-4096, le=8192)
    editor_y: float | None = Field(default=None, ge=-4096, le=8192)
    editor_width: float | None = Field(default=None, gt=0, le=8192)
    editor_height: float | None = Field(default=None, gt=0, le=8192)


class TemplateTextSlot(BaseModel):
    id: str
    role: Literal["eyebrow", "title", "subtitle", "tag", "slogan", "other"]
    source_text: str
    box: NormalizedBox
    style: TemplateTextStyle
    max_chars: int = Field(ge=1, le=120)
    max_lines: int = Field(ge=1, le=4)
    confidence: float | None = Field(default=None, ge=0, le=1)
    candidate_count: int = Field(default=0, ge=0)
    consensus_count: int = Field(default=0, ge=0)
    source_variant: str | None = Field(default=None, max_length=240)
    alternatives: list[str] = Field(default_factory=list, max_length=5)


class TemplateAnalysis(BaseModel):
    processing_version: str
    canvas_width: int = Field(gt=0)
    canvas_height: int = Field(gt=0)
    text_slots: list[TemplateTextSlot] = Field(default_factory=list)
    decoration_regions: list[NormalizedBox] = Field(default_factory=list)
    editable_regions: list[NormalizedBox] = Field(default_factory=list)
    ocr_raw_layers: list[dict[str, Any]] = Field(default_factory=list)
    recognition_metrics: dict[str, Any] = Field(default_factory=dict)
    layout_fingerprint: str


class CopySlotPlan(BaseModel):
    slot_id: str
    role: str
    source_text: str
    text: str
    max_chars: int
    max_lines: int
    changed: bool


class CopyPlan(BaseModel):
    processing_version: str
    source: Literal["template", "content_asset", "manual"]
    slots: list[CopySlotPlan] = Field(default_factory=list)


class RenderPlan(BaseModel):
    processing_version: str
    target_width: int
    target_height: int
    source_fit: Literal["cover"] = "cover"
    image2_mode: Literal["edit"] = "edit"
    locked_regions: list[NormalizedBox] = Field(default_factory=list)
    editable_regions: list[NormalizedBox] = Field(default_factory=list)


class QualityReport(BaseModel):
    processing_version: str
    passed: bool
    output_width: int
    output_height: int
    output_format: str
    locked_ssim: float = Field(ge=0, le=1)
    layout_deviation: float = Field(ge=0)
    ocr_accuracy: float = Field(ge=0, le=1)
    mosaic_count: int = Field(ge=0)
    residual_text_count: int = Field(ge=0)
    overflow_count: int = Field(ge=0)
    failures: list[str] = Field(default_factory=list)


class TemplateReplicatePlanCreate(BaseModel):
    template_asset_id: str
    source_asset_id: str
    content_task_id: str | None = None
    title: str = Field(default="", max_length=60)
    copy_overrides: dict[str, str] = Field(default_factory=dict)
    layout_overrides: dict[str, SlotLayoutOverride] = Field(default_factory=dict)
    size: str = "1080x1440"

    @field_validator("size")
    @classmethod
    def supported_size(cls, value: str) -> str:
        if value != "1080x1440":
            raise ValueError("模板复刻 V2 当前固定输出 1080×1440 PNG")
        return value


class PosterTextSlot(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    role: Literal["eyebrow", "title", "subtitle", "tag", "slogan", "product", "other"] = "other"
    source_text: str = Field(default="", max_length=240)
    editable: bool = True
    box: NormalizedBox
    style: TemplateTextStyle
    max_chars: int = Field(default=24, ge=1, le=120)
    max_lines: int = Field(default=2, ge=1, le=4)
    confidence: float | None = Field(default=None, ge=0, le=1)
    candidate_count: int = Field(default=0, ge=0)
    consensus_count: int = Field(default=0, ge=0)
    source_variant: str | None = Field(default=None, max_length=240)
    alternatives: list[str] = Field(default_factory=list, max_length=5)
    review_state: Literal["recognized", "user_edited", "user_added"] = "recognized"


class PosterProductTransform(BaseModel):
    fit: Literal["cover", "contain"] = "cover"
    scale: float = Field(default=1, ge=0.5, le=2)
    focal_x: float = Field(default=0.5, ge=0, le=1)
    focal_y: float = Field(default=0.5, ge=0, le=1)
    x_offset: float = Field(default=0, ge=-0.5, le=0.5)
    y_offset: float = Field(default=0, ge=-0.5, le=0.5)


class PosterTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    product_box: NormalizedBox | None = None
    safe_area: NormalizedBox | None = None
    text_slots: list[PosterTextSlot] | None = Field(default=None, max_length=20)
    fixed_regions: list[NormalizedBox] | None = Field(default=None, max_length=40)
    editable_regions: list[NormalizedBox] | None = Field(default=None, max_length=40)
    status: Literal["ready", "disabled"] | None = None


class PosterTemplateReviewUpdate(BaseModel):
    version: int = Field(ge=1)
    text_slots: list[PosterTextSlot] = Field(default_factory=list, max_length=60)
    product_box: NormalizedBox
    confirm: bool = False

    @model_validator(mode="after")
    def unique_layer_ids(self):
        layer_ids = [item.id for item in self.text_slots]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError("文字图层 ID 不能重复")
        return self


class PosterPreviewCreate(BaseModel):
    poster_template_id: str
    product_asset_id: str
    content_task_id: str | None = None
    title: str = Field(default="", max_length=60)
    copy_overrides: dict[str, str] = Field(default_factory=dict)
    transform: PosterProductTransform = Field(default_factory=PosterProductTransform)


class PosterGenerateCreate(PosterPreviewCreate):
    enhance_with_image2: bool = False
    enhancement_prompt: str = Field(default="", max_length=2000)
    negative_prompt: str | None = Field(default=None, max_length=2000)
    n: int = Field(default=1, ge=1, le=4)
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def deterministic_generation_is_single(self):
        if not self.enhance_with_image2 and self.n != 1:
            raise ValueError("关闭 image2 美化时确定性大字报仅生成 1 张")
        return self


HexColor = str


class CoverEditorCanvas(BaseModel):
    width: int = Field(ge=320, le=4096)
    height: int = Field(ge=320, le=4096)
    background_asset_id: str = Field(min_length=1, max_length=64)
    safe_area: dict[str, float] = Field(default_factory=dict)


class CoverEditorTextLayer(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    layer_type: Literal["text"] = "text"
    name: str = Field(default="文字", min_length=1, max_length=80)
    text: str = Field(default="", max_length=500)
    x: float = Field(ge=-4096, le=8192)
    y: float = Field(ge=-4096, le=8192)
    width: float = Field(gt=0, le=8192)
    height: float = Field(gt=0, le=8192)
    rotation: float = Field(default=0, ge=-180, le=180)
    opacity: float = Field(default=1, ge=0, le=1)
    visible: bool = True
    locked: bool = False
    order: int = Field(default=0, ge=0, le=1000)
    font_family: str = Field(default="Noto Sans CJK SC", min_length=1, max_length=120)
    font_size: float = Field(default=64, ge=8, le=512)
    font_weight: Literal[400, 500, 600, 700, 800, 900] = 700
    font_style: Literal["normal", "italic"] = "normal"
    fill: HexColor = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    fill_runs: list[TemplateTextFillRun] = Field(default_factory=list, max_length=24)
    align: Literal["left", "center", "right"] = "center"
    line_height: float = Field(default=1.2, ge=0.8, le=3)
    letter_spacing: float = Field(default=0, ge=-20, le=80)
    stroke: bool = False
    stroke_color: HexColor = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke_width: float = Field(default=0, ge=0, le=40)
    shadow: bool = False
    shadow_color: HexColor = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    shadow_blur: float = Field(default=0, ge=0, le=80)
    shadow_offset_x: float = Field(default=0, ge=-100, le=100)
    shadow_offset_y: float = Field(default=8, ge=-100, le=100)
    background_fill: HexColor | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    background_opacity: float = Field(default=1, ge=0, le=1)
    background_radius: float = Field(default=0, ge=0, le=200)
    background_padding: float = Field(default=0, ge=0, le=200)


class CoverEditorScene(BaseModel):
    version: Literal[1] = 1
    recovery_version: Literal["all-text-v2", "precise-text-v3", "template-scene-v1"] = "template-scene-v1"
    canvas: CoverEditorCanvas
    layers: list[CoverEditorTextLayer] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_layer_ids(self):
        layer_ids = [item.id for item in self.layers]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError("文字图层 ID 不能重复")
        return self


class CoverEditorProjectCreate(BaseModel):
    asset_id: str = Field(min_length=1, max_length=64)
    artifact_id: str | None = Field(default=None, max_length=64)


class CoverEditorSceneUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    scene: CoverEditorScene


class CoverEditorRenderCreate(BaseModel):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)


class CoverRetryCreate(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)


class CoverSetCurrentCreate(BaseModel):
    asset_id: str | None = None


class CoverAssetRole(str):
    SOURCE = "source"
    TEMPLATE = "template"
    MASK = "mask"
    POSTER_TEMPLATE = "poster_template"
    OUTPUT = "output"


class Image2Input(BaseModel):
    data: bytes
    content_type: str
    file_name: str


class Image2Request(BaseModel):
    mode: Literal["text_to_image", "image_to_image", "multi_reference", "mask"]
    prompt: str
    negative_prompt: str | None = None
    size: str
    n: int = 1
    source_images: list[Image2Input] = Field(default_factory=list)
    template_image: Image2Input | None = None
    mask_image: Image2Input | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Image2Output(BaseModel):
    url: str | None = None
    b64_data: str | None = None
    content_type: str | None = None


class Image2Submission(BaseModel):
    provider_task_id: str | None = None
    status: Literal["pending", "completed", "failed"]
    images: list[Image2Output] = Field(default_factory=list)
    error_message: str | None = None
