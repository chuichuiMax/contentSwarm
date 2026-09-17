import { createBlankDesign, createNode, type Fill, type TextNode } from "@hc/schema";
import { fromHex } from "@hc/color";

export type CoverElement = {
  id: string;
  category: "标题" | "副标题" | "标签" | "数字";
  name: string;
  lines: string[];
  width: number;
  fontSize: number;
  weight: number;
  color: string;
  unit?: string;
  background?: string;
};

// Sizes are authored for a 1080 × 1440 cover. Every preset remains native text.
export const coverElements: CoverElement[] = [
  { id: "headline", category: "标题", name: "大标题", lines: ["装修避坑指南"], width: 900, fontSize: 112, weight: 900, color: "#18232f" },
  { id: "two-line", category: "标题", name: "双行标题", lines: ["旧房改造", "这些细节要注意"], width: 900, fontSize: 104, weight: 800, color: "#18232f" },
  { id: "highlight", category: "标题", name: "强调标题", lines: ["先看报价再开工"], width: 900, fontSize: 96, weight: 900, color: "#18232f", background: "#ffe45e" },
  { id: "description", category: "副标题", name: "说明文字", lines: ["从拆除到入住，把每一步讲清楚"], width: 900, fontSize: 46, weight: 500, color: "#475569" },
  { id: "supplement", category: "副标题", name: "补充信息", lines: ["费用口径与施工范围，以实际方案为准"], width: 900, fontSize: 34, weight: 400, color: "#64748b" },
  { id: "city", category: "标签", name: "城市", lines: ["长沙装修"], width: 320, fontSize: 48, weight: 700, color: "#ffffff", background: "#244ba2" },
  { id: "layout", category: "标签", name: "户型", lines: ["三室两厅"], width: 320, fontSize: 48, weight: 700, color: "#244ba2", background: "#e9efff" },
  { id: "trade", category: "标签", name: "工种", lines: ["水电施工"], width: 320, fontSize: 48, weight: 700, color: "#14685b", background: "#e0f3ec" },
  { id: "service", category: "标签", name: "服务范围", lines: ["旧房翻新 · 局部改造"], width: 650, fontSize: 48, weight: 700, color: "#ffffff", background: "#18232f" },
  { id: "price", category: "数字", name: "价格", lines: ["33341"], unit: " 元", width: 860, fontSize: 156, weight: 900, color: "#e14b36" },
  { id: "area", category: "数字", name: "面积", lines: ["115"], unit: " ㎡", width: 650, fontSize: 156, weight: 900, color: "#244ba2" },
  { id: "number", category: "数字", name: "序号", lines: ["01"], unit: " / 步骤", width: 550, fontSize: 132, weight: 900, color: "#18232f" },
];

export function coverElementText(preset: CoverElement, page: { width: number; height: number }): Partial<TextNode> {
  const scale = Math.min(page.width / 1080, page.height / 1440, 1);
  const fontSize = preset.fontSize * scale;
  const padding = (preset.background ? 24 : 8) * scale;
  const width = preset.width * scale;
  const height = fontSize * 1.5 * preset.lines.length + padding * 2;
  const fill: Fill = { type: "solid", color: fromHex(preset.color)! };
  const style = { fontFamily: "system", fontStyle: "Regular", fontSize, axes: { wght: preset.weight }, fill };
  return {
    name: preset.name,
    size: { width, height },
    transform: { x: 0, y: 0, scaleX: 1, scaleY: 1, rotation: 0 },
    box: { mode: "autoHeight", width, height, padding: { t: padding, r: padding, b: padding, l: padding }, verticalAlign: "top" },
    content: preset.lines.map((text) => ({
      runs: [
        { text, style: { ...style } },
        ...(preset.unit ? [{ text: preset.unit, style: { ...style, fontSize: fontSize * 0.36, axes: { wght: 600 } } }] : []),
      ],
      style: { align: "left", direction: "auto" },
    })),
    textEffects: preset.background ? [{ kind: "highlight", color: { type: "solid", color: fromHex(preset.background)! }, padding: 12 * scale, radius: (preset.category === "标签" ? 18 : 4) * scale }] : [],
    data: { coverElementId: preset.id },
  };
}

export const coverLayouts = [
  { id: "quote", name: "报价封面", description: "大数字突出价格，户型与工种说明范围", background: "#fffaf2" },
  { id: "craft", name: "工艺清单", description: "双行标题配步骤序号，适合施工与避坑", background: "#f0f6f3" },
  { id: "service", name: "服务介绍", description: "突出身份、服务范围与面积信息", background: "#f1f4fc" },
] as const;

type CoverSlot = { element: string; x: number; y: number; lines?: string[] };
const layoutSlots: Record<(typeof coverLayouts)[number]["id"], CoverSlot[]> = {
  quote: [
    { element: "city", x: 72, y: 72 },
    { element: "two-line", x: 72, y: 264, lines: ["旧房改造", "人工费用参考"] },
    { element: "description", x: 72, y: 640, lines: ["拆清项目，看懂每一笔人工费用"] },
    { element: "price", x: 72, y: 800 },
    { element: "layout", x: 72, y: 1120 },
    { element: "trade", x: 432, y: 1120 },
    { element: "supplement", x: 72, y: 1280 },
  ],
  craft: [
    { element: "trade", x: 72, y: 72 },
    { element: "two-line", x: 72, y: 264 },
    { element: "number", x: 72, y: 680 },
    { element: "description", x: 72, y: 944, lines: ["施工顺序、工艺细节、验收要点"] },
    { element: "service", x: 72, y: 1104 },
    { element: "supplement", x: 72, y: 1280, lines: ["具体工艺与材料，按现场情况确认"] },
  ],
  service: [
    { element: "city", x: 72, y: 72 },
    { element: "headline", x: 72, y: 280, lines: ["长沙装修工长"] },
    { element: "description", x: 72, y: 512, lines: ["把施工需求讲清楚，把每一步做扎实"] },
    { element: "service", x: 72, y: 680 },
    { element: "area", x: 72, y: 864 },
    { element: "layout", x: 72, y: 1160 },
    { element: "supplement", x: 72, y: 1320, lines: ["城市、面积及服务内容均为示例，请按实际修改"] },
  ],
};

/** Native nodes with fixed layout slots; importing a page is one undoable action. */
export function buildCoverLayout(id: (typeof coverLayouts)[number]["id"]) {
  const layout = coverLayouts.find((item) => item.id === id)!;
  const file = createBlankDesign({ width: 1080, height: 1440 });
  file.title = layout.name;
  const page = file.pages[0];
  page.name = layout.name;
  page.background = { type: "solid", color: fromHex(layout.background)! };
  page.children = layoutSlots[id].map((slot) => {
    const preset = coverElements.find((item) => item.id === slot.element)!;
    const text = coverElementText({ ...preset, ...(slot.lines ? { lines: slot.lines } : {}) }, page);
    return createNode("text", {
      ...text,
      transform: { x: slot.x, y: slot.y, scaleX: 1, scaleY: 1, rotation: 0 },
    });
  });
  return file;
}
