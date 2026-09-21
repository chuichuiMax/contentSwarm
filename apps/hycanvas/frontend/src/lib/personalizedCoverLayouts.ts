import { fromHex } from "@hc/color";
import { createBlankDesign, createNode, type DesignFile, type TextNode } from "@hc/schema";

export type PersonalizedCoverFamily = "template-1" | "template-3" | "template-4";
export type PersonalizedCoverPlacement = "top-left" | "top-right" | "bottom-left" | "bottom-right" | "center";
type PersonalizedCoverComposition = PersonalizedCoverPlacement | "staircase" | "center-focus" | "diagonal";

export type PersonalizedCoverLayout = {
  id: string;
  name: string;
  description: string;
  family: PersonalizedCoverFamily;
  composition: PersonalizedCoverComposition;
};

type TextSpec = {
  id: string;
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  fontSize: number;
  weight: number;
  binding: "title" | "subtitle" | "project_name_en" | "footnote";
  align?: "left" | "center" | "right";
  fixed?: boolean;
};

type FamilySpec = {
  name: string;
  contentWidth: number;
  contentHeight: number;
  nodes: TextSpec[];
};

const shadow = {
  kind: "shadow" as const,
  dx: 4,
  dy: 4,
  blur: 6,
  color: { type: "solid" as const, color: fromHex("#000000")! },
  opacity: 0.4,
};

const familySpecs: Record<PersonalizedCoverFamily, FamilySpec> = {
  "template-1": {
    name: "模板1",
    contentWidth: 949.5,
    contentHeight: 422.4,
    nodes: [
      { id: "title", text: "这是主标题文案", x: 0, y: 0, width: 949.5, height: 320, fontSize: 126.6, weight: 800, binding: "title" },
      { id: "subtitle", text: "这是副标题文案", x: 0, y: 184.7, width: 702.2, height: 157.4, fontSize: 92.9, weight: 800, binding: "subtitle" },
      { id: "english", text: "zheshifubiaotiyingwen", x: 0, y: 329.4, width: 580, height: 93, fontSize: 45.9, weight: 500, binding: "project_name_en" },
    ],
  },
  "template-3": {
    name: "模板3",
    contentWidth: 580,
    contentHeight: 372,
    nodes: [
      { id: "title", text: "洋湖天序", x: 0, y: 0, width: 539.9, height: 184.7, fontSize: 126.6, weight: 800, binding: "title" },
      { id: "subtitle", text: "复古多巴胺", x: 0, y: 172.7, width: 395.3, height: 116.3, fontSize: 70, weight: 800, binding: "subtitle" },
      { id: "english", text: "zheshifubiaotiyingwen", x: 0, y: 278.9, width: 580, height: 93, fontSize: 45.9, weight: 500, binding: "project_name_en" },
      { id: "footnote", text: "长沙 | 宁乡 | 湘潭 | 湘乡 | 株洲 | 攸县 | 娄底 | 怀化 | 衡阳 | 郴州 | 常德 | 岳阳", x: 122.8, y: 1365.5, width: 834.4, height: 46, fontSize: 25, weight: 500, binding: "footnote", fixed: true },
    ],
  },
  "template-4": {
    name: "模板4",
    contentWidth: 552,
    contentHeight: 375,
    nodes: [
      { id: "title", text: "洋湖天序", x: 12.1, y: 0, width: 539.9, height: 184.7, fontSize: 126.6, weight: 800, binding: "title" },
      { id: "subtitle", text: "复古多巴胺", x: 156.7, y: 172.7, width: 395.3, height: 116.3, fontSize: 70, weight: 800, binding: "subtitle" },
      { id: "english", text: "zheshifubiaotiyingwen", x: 0, y: 281.9, width: 539.9, height: 93, fontSize: 45.9, weight: 500, binding: "project_name_en" },
    ],
  },
};

const placements: Array<{ id: PersonalizedCoverPlacement; name: string }> = [
  { id: "top-left", name: "左上" },
  { id: "top-right", name: "右上" },
  { id: "bottom-left", name: "左下" },
  { id: "bottom-right", name: "右下" },
  { id: "center", name: "居中" },
];

export const personalizedCoverLayouts: PersonalizedCoverLayout[] = [
  ...(["template-1", "template-3", "template-4"] as const).flatMap((family) => placements.map((placement) => ({
    id: `${family}-${placement.id}`,
    name: `${familySpecs[family].name} · ${placement.name}`,
    description: `透明底 · 主体内容整体${placement.name}排版`,
    family,
    composition: placement.id,
  }))),
  { id: "template-1-staircase", name: "精选 · 阶梯节奏", description: "透明底 · 主副标题形成递进层级", family: "template-1", composition: "staircase" },
  { id: "template-3-center-focus", name: "精选 · 中轴聚焦", description: "透明底 · 中轴对齐，适合主体清晰的底图", family: "template-3", composition: "center-focus" },
  { id: "template-4-diagonal", name: "精选 · 对角留白", description: "透明底 · 错位对角构图，保留大面积视觉留白", family: "template-4", composition: "diagonal" },
];

function placementOrigin(family: FamilySpec, placement: PersonalizedCoverPlacement) {
  const margin = 72;
  const bottom = family.nodes.some((node) => node.fixed) ? 1308 : 1368;
  if (placement === "top-left") return { x: margin, y: margin };
  if (placement === "top-right") return { x: 1080 - margin - family.contentWidth, y: margin };
  if (placement === "bottom-left") return { x: margin, y: bottom - family.contentHeight };
  if (placement === "bottom-right") return { x: 1080 - margin - family.contentWidth, y: bottom - family.contentHeight };
  return { x: (1080 - family.contentWidth) / 2, y: (1440 - family.contentHeight) / 2 };
}

function makeText(spec: TextSpec, x: number, y: number, layoutId: string) {
  const fill = { type: "solid" as const, color: fromHex("#ffffff")! };
  const node: Partial<TextNode> = {
    name: spec.binding === "project_name_en" ? "项目英文名" : spec.binding === "footnote" ? "底部说明" : spec.binding === "title" ? "主标题" : "副标题",
    size: { width: spec.width, height: spec.height },
    transform: { x, y, scaleX: 1, scaleY: 1, rotation: 0 },
    box: { mode: "fixed", width: spec.width, height: spec.height, padding: { t: 8, r: 8, b: 8, l: 8 }, verticalAlign: "top" },
    content: [{
      runs: [{ text: spec.text, style: { fontFamily: "Noto Sans SC", fontStyle: "Regular", fontSize: spec.fontSize, axes: { wght: spec.weight }, fill } }],
      style: { align: spec.align ?? "left", direction: "auto" },
    }],
    textEffects: [shadow],
    data: {
      coverElementId: spec.id,
      layoutInstanceId: layoutId,
      layoutTemplateId: layoutId,
      slotId: spec.id,
      elementType: spec.binding === "footnote" ? "subtitle" : spec.binding,
      binding: spec.binding,
      layoutMode: "auto",
      contentBlock: !spec.fixed,
    },
  };
  return createNode("text", node);
}

function curatedSpecs(layout: PersonalizedCoverLayout): TextSpec[] {
  if (layout.composition === "staircase") return [
    { ...familySpecs["template-1"].nodes[0], x: 72, y: 300, width: 920, height: 190 },
    { ...familySpecs["template-1"].nodes[1], x: 160, y: 520, width: 760, height: 144 },
    { ...familySpecs["template-1"].nodes[2], x: 72, y: 704, width: 620 },
  ];
  if (layout.composition === "center-focus") return [
    { ...familySpecs["template-3"].nodes[0], x: 120, y: 442, width: 840, align: "center" },
    { ...familySpecs["template-3"].nodes[1], x: 180, y: 660, width: 720, align: "center" },
    { ...familySpecs["template-3"].nodes[2], x: 200, y: 816, width: 680, align: "center" },
    familySpecs["template-3"].nodes[3],
  ];
  return [
    { ...familySpecs["template-4"].nodes[0], x: 72, y: 250 },
    { ...familySpecs["template-4"].nodes[1], x: 612, y: 510 },
    { ...familySpecs["template-4"].nodes[2], x: 84, y: 770 },
  ];
}

export function buildPersonalizedCoverLayout(layout: PersonalizedCoverLayout): DesignFile {
  const file = createBlankDesign({ width: 1080, height: 1440 });
  file.title = layout.name;
  const page = file.pages[0];
  page.name = layout.name;
  delete page.background;
  const family = familySpecs[layout.family];
  if (layout.composition === "staircase" || layout.composition === "center-focus" || layout.composition === "diagonal") {
    page.children = curatedSpecs(layout).map((spec) => makeText(spec, spec.x, spec.y, layout.id));
    return file;
  }
  const origin = placementOrigin(family, layout.composition);
  page.children = family.nodes.map((spec) => makeText(spec, spec.fixed ? spec.x : origin.x + spec.x, spec.fixed ? spec.y : origin.y + spec.y, layout.id));
  return file;
}
