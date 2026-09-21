import { createBlankDesign, createNode, type Fill, type PathContour, type TextNode } from "@hc/schema";
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
  outlineColor?: string;
  outlineWidth?: number;
  unit?: string;
  background?: string;
  decoration?: "brush" | "marker" | "dry-brush";
  brushProfile?: "wide" | "taper" | "double" | "flat" | "fine" | "sweep" | "triple" | "short" | "ragged" | "slim";
  align?: "left" | "center" | "right";
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
  ...([
    ["black", "黑色", "#111827"], ["navy", "深蓝", "#244ba2"],
    ["coral", "珊瑚", "#d94f38"], ["violet", "紫色", "#7045a4"],
    ["teal", "青绿", "#14685b"], ["gold", "金色", "#a66a12"],
  ] as const).flatMap(([id, name, outlineColor]): CoverElement[] => [
    { id: `outline-title-${id}`, category: "标题", name: `${name}粗描边标题`,
      lines: ["装修避坑指南"], width: 900, fontSize: 96, weight: 900,
      color: "#ffffff", outlineColor, outlineWidth: 12 },
    { id: `outline-subtitle-${id}`, category: "副标题", name: `${name}粗描边副标题`,
      lines: ["把每一步做扎实"], width: 720, fontSize: 60, weight: 800,
      color: "#ffffff", outlineColor, outlineWidth: 10 },
  ]),
  ...([
    ["yellow", "明黄强调副标题", "先看报价再开工", "#ffe45e", "#18232f"],
    ["mint", "薄荷绿副标题", "把预算花在刀刃上", "#c5ebd8", "#174d40"],
    ["blue", "雾蓝副标题", "施工细节看这里", "#d7e9ff", "#244ba2"],
    ["coral", "珊瑚橙副标题", "装修少走弯路", "#ffb995", "#69341d"],
    ["lavender", "浅紫副标题", "好工艺藏在细节里", "#e6d9fa", "#503477"],
    ["dark", "深色反白副标题", "每一项费用都讲清楚", "#18232f", "#ffffff"],
    ["brush-yellow", "金黄笔刷副标题", "先看报价再开工", "#ffd449", "#18232f", "brush"],
    ["brush-mint", "青绿笔刷副标题", "施工细节看这里", "#9ed9c0", "#174d40", "brush"],
    ["brush-blue", "蓝色笔刷副标题", "把每一步做扎实", "#4569b6", "#ffffff", "brush"],
    ["brush-rose", "玫瑰粉笔刷副标题", "家的样子慢慢实现", "#f3b7c9", "#703149", "brush"],
    ["brush-orange", "暖橙笔刷副标题", "每一笔都算清楚", "#f16a32", "#382117", "brush"],
    ["brush-violet", "紫罗兰笔刷副标题", "细节决定家的质感", "#8656b6", "#ffffff", "brush"],
    ["brush-teal", "湖蓝笔刷副标题", "施工进度看这里", "#197d81", "#ffffff", "brush"],
    ["brush-red", "砖红笔刷副标题", "旧房改造有方法", "#c64b5a", "#ffffff", "brush"],
    ["brush-lime", "青柠笔刷副标题", "把预算花在重点", "#bfd958", "#263614", "brush"],
    ["brush-slate", "深灰笔刷副标题", "好工艺经得起看", "#334155", "#ffffff", "brush"],
    ["marker-yellow", "黄色荧光副标题", "装修少走弯路", "#ffe45e", "#18232f", "marker"],
    ["marker-green", "绿色荧光副标题", "把预算花在刀刃上", "#a8e3ad", "#174d40", "marker"],
    ["marker-orange", "橙色荧光副标题", "每一项费用都讲清楚", "#ffbe85", "#69341d", "marker"],
    ["marker-purple", "紫色荧光副标题", "好工艺藏在细节里", "#d7bdf1", "#503477", "marker"],
  ] as const).map(([id, name, text, background, color, decoration]): CoverElement => ({
    id: `subtitle-${id}`, category: "副标题", name, lines: [text], width: text.length * 52 + 80,
    fontSize: 52, weight: 800, color, background, decoration,
  })),
  ...([
    ["wide", "颗粒宽涂干刷"], ["taper", "渐细拖尾干刷"], ["double", "双道留白干刷"],
    ["flat", "平头粗纹干刷"], ["fine", "纤细划线干刷"], ["sweep", "斜扫飞白干刷"],
    ["triple", "三道条纹干刷"], ["short", "短尾擦涂干刷"],
    ["ragged", "毛边颗粒干刷"], ["slim", "窄幅细涂干刷"],
  ] as const).map(([brushProfile, name]): CoverElement => ({
    id: `subtitle-dry-${brushProfile}`, category: "副标题", name,
    lines: ["先看报价再开工"], width: 720, fontSize: 52, weight: 800,
    color: "#18232f", background: "#f16a32", decoration: "dry-brush", brushProfile,
  })),
];

export function coverElementText(preset: CoverElement, page: { width: number; height: number }): Partial<TextNode> {
  const scale = Math.min(page.width / 1080, page.height / 1440, 1);
  const fontSize = preset.fontSize * scale;
  const padding = Math.max(preset.background ? 24 : 8, (preset.outlineWidth ?? 0) / 2 + 4) * scale;
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
      style: { align: preset.align ?? "left", direction: "auto" },
    })),
    textEffects: preset.outlineColor
      ? [{ kind: "outline", width: preset.outlineWidth! * scale, color: { type: "solid", color: fromHex(preset.outlineColor)! }, join: "round" }]
      : preset.background ? [{ kind: "highlight", color: { type: "solid", color: fromHex(preset.background)! }, padding: 12 * scale, radius: (preset.category === "标签" ? 18 : 4) * scale }] : [],
    data: { coverElementId: preset.id },
  };
}

/** Brush backdrops are vector paths grouped with editable text, never bitmaps. */
export function buildCoverElement(preset: CoverElement, page: { width: number; height: number }) {
  const text = createNode("text", coverElementText(preset, page));
  if (!preset.decoration) return text;
  text.textEffects = [];
  const { width, height } = text.size;
  if (preset.decoration === "brush") {
    let seed = 271828;
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
    const outline = [[0.02, 0.28], [0.08, 0.19], [0.22, 0.17], [0.38, 0.2], [0.53, 0.15],
      [0.7, 0.2], [0.87, 0.17], [0.98, 0.21], [0.94, 0.38], [0.99, 0.49],
      [0.96, 0.68], [0.98, 0.81], [0.8, 0.77], [0.64, 0.83], [0.47, 0.78],
      [0.28, 0.81], [0.13, 0.77], [0.01, 0.82], [0.05, 0.61], [0.01, 0.48]];
    const contours: PathContour[] = [];
    for (let i = 0; i < 900; i++) {
      const x = 0.04 + random() * 0.9;
      const y = 0.24 + random() * 0.48;
      const length = 0.008 + random() * 0.016;
      const grain = 0.012 + random() * 0.016;
      contours.push({ closed: true, segments: [
        { x: x * width, y: y * height },
        { x: (x + length) * width, y: y * height },
        { x: (x + length) * width, y: (y + grain) * height },
        { x: x * width, y: (y + grain) * height },
      ] });
    }
    const backdrop = createNode("path", {
      name: "颗粒笔刷底色 · 可改色", size: { width, height }, closed: true,
      segments: outline.map(([x, y]) => ({ x: x * width, y: y * height })), contours,
      fills: [{ type: "solid", color: fromHex(preset.background!)! }],
    });
    return createNode("group", { name: preset.name, size: { width, height }, children: [backdrop, text],
      data: { coverElementId: preset.id } });
  }
  if (preset.decoration === "dry-brush") {
    // One compound path: transparent gaps and granular bristles, with one editable fill.
    // A fixed seed keeps previews, insertion and serialization visually consistent.
    let seed = 271828;
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
    const contours: PathContour[] = [];
    for (let i = 0; i < 1800; i++) {
      const x = 0.025 + random() * (preset.brushProfile === "short" ? 0.76 : 0.92);
      const strand = random() * 2 - 1;
      let thickness = 0.34;
      let center = 0.5;
      if (preset.brushProfile === "taper") thickness *= 1 - x * 0.88;
      if (preset.brushProfile === "fine") { thickness = 0.1; center = 0.82; }
      if (preset.brushProfile === "sweep") { thickness *= 1 - x * 0.6; center = 0.62 - x * 0.2; }
      if (preset.brushProfile === "short") thickness *= 0.8 - x * 0.4;
      if (preset.brushProfile === "ragged") thickness *= 0.7 + random() * 0.6;
      if (preset.brushProfile === "slim") { thickness = 0.15; center = 0.56; }
      if (preset.brushProfile === "wide") thickness *= 0.8 + Math.sin(x * Math.PI) * 0.2;
      if (preset.brushProfile === "double" && Math.abs(strand) < 0.18) continue;
      if (preset.brushProfile === "triple" && (Math.abs(strand - 0.35) < 0.12 || Math.abs(strand + 0.35) < 0.12)) continue;
      // Bristle rows retain small irregular gaps instead of a smooth filled slab.
      const y = center + strand * thickness + (random() - 0.5) * 0.02;
      const length = 0.004 + random() * (preset.brushProfile === "flat" || preset.brushProfile === "ragged" ? 0.035 : 0.023);
      const grain = 0.004 + random() * 0.013;
      contours.push({ closed: true, segments: [
        { x: x * width, y: y * height },
        { x: (x + length * 0.8) * width, y: (y - grain * 0.25) * height },
        { x: (x + length) * width, y: (y + grain * 0.5) * height },
        { x: (x + length * 0.2) * width, y: (y + grain) * height },
      ] });
    }
    const [first, ...rest] = contours;
    const backdrop = createNode("path", { name: "笔刷颗粒 · 可改色", size: { width, height },
      closed: true, segments: first.segments, contours: rest,
      fills: [{ type: "solid", color: fromHex(preset.background!)! }],
    });
    return createNode("group", { name: preset.name, size: { width, height }, children: [backdrop, text],
      data: { coverElementId: preset.id } });
  }
  const points = [[0.025, 0.56], [1, 0.5], [0.975, 0.91], [0, 0.96]];
  const backdrop = createNode("path", {
    name: "荧光标记 · 可改色",
    size: { width, height }, closed: true,
    segments: points.map(([x, y]) => ({ x: x * width, y: y * height })),
    fills: [{ type: "solid", color: fromHex(preset.background!)! }],
  });
  return createNode("group", { name: preset.name, size: { width, height }, children: [backdrop, text],
    data: { coverElementId: preset.id } });
}

export const coverLayouts = [
  { id: "quote", name: "报价封面", description: "透明底 · 标题、副标题与说明文字可编辑" },
  { id: "craft", name: "工艺清单", description: "透明底 · 双行标题与施工说明可编辑" },
  { id: "service", name: "服务介绍", description: "透明底 · 身份标题与服务说明可编辑" },
] as const;

export const coverStyleTemplates = coverElements.filter((item) => item.category === "标题" || item.category === "副标题");

type CoverSlot = { element: string; x: number; y: number; lines?: string[]; fontSize?: number; width?: number; align?: CoverElement["align"] };
const layoutSlots: Record<(typeof coverLayouts)[number]["id"], CoverSlot[]> = {
  quote: [
    { element: "two-line", x: 72, y: 264, lines: ["旧房改造", "人工费用参考"] },
    { element: "description", x: 72, y: 640, lines: ["拆清项目，看懂每一笔人工费用"] },
    { element: "supplement", x: 72, y: 1280 },
  ],
  craft: [
    { element: "two-line", x: 72, y: 264 },
    { element: "description", x: 72, y: 944, lines: ["施工顺序、工艺细节、验收要点"] },
    { element: "supplement", x: 72, y: 1280, lines: ["具体工艺与材料，按现场情况确认"] },
  ],
  service: [
    { element: "headline", x: 72, y: 280, lines: ["长沙装修工长"] },
    { element: "description", x: 72, y: 512, lines: ["把施工需求讲清楚，把每一步做扎实"] },
    { element: "supplement", x: 72, y: 1320, lines: ["城市、面积及服务内容均为示例，请按实际修改"] },
  ],
};

// Keep the central 800 px free of text and masks so the photo stays visible.
const photoSlots: Record<(typeof coverLayouts)[number]["id"], CoverSlot[]> = {
  quote: [
    { element: "headline", x: 64, y: 132, fontSize: 64, lines: ["旧房改造 · 人工费用参考"] },
    { element: "description", x: 64, y: 252, fontSize: 28, lines: ["拆清项目，看懂每一笔人工费用"] },
    { element: "supplement", x: 64, y: 1336, fontSize: 28 },
  ],
  craft: [
    { element: "headline", x: 64, y: 132, fontSize: 64, lines: ["旧房改造，这些细节要注意"] },
    { element: "description", x: 64, y: 252, fontSize: 28, lines: ["施工顺序、工艺细节、验收要点"] },
    { element: "supplement", x: 64, y: 1336, fontSize: 28, lines: ["具体工艺与材料，按现场情况确认"] },
  ],
  service: [
    { element: "headline", x: 64, y: 132, fontSize: 64, lines: ["长沙装修工长"] },
    { element: "description", x: 64, y: 252, fontSize: 28, lines: ["把施工需求讲清楚，把每一步做扎实"] },
    { element: "supplement", x: 64, y: 1370, fontSize: 24, lines: ["城市、面积及服务内容均为示例，请按实际修改"] },
  ],
};

export const coverTitleCompositions = [
  { id: "stacked", name: "主上副下", symbol: "主标题\n副标题" },
  { id: "centered", name: "居中组合", symbol: "— 主标题 —\n副标题" },
  { id: "subtitle-first", name: "副上主下", symbol: "副标题\n主标题" },
  { id: "side-by-side", name: "主左副右", symbol: "主标题 │ 副标题" },
] as const;
export type CoverTitleComposition = (typeof coverTitleCompositions)[number]["id"];
export type CoverTitlePlacement = "top" | "photo-center";

export type CoverBackground = { id: string; name: string; url: string; mime: string; width: number; height: number };

/** Two independently replaceable photos with native text and a yellow vector brush. */
export function buildBeforeAfterCover(top?: CoverBackground, bottom?: CoverBackground) {
  const file = createBlankDesign({ width: 1080, height: 1440 });
  file.title = file.pages[0].name = "装修前后对比 · 装修第一步";
  const page = file.pages[0];
  delete page.background;
  for (const [index, photo] of [top, bottom].entries()) {
    const y = index * 720;
    if (photo) {
      const assetId = `before-after-${index}-${photo.id}`;
      file.assets.push({ id: assetId, kind: "image", url: photo.url, mime: photo.mime, checksum: "" });
      page.children.push(createNode("image", {
        name: index === 0 ? "上方照片 · 可替换" : "下方照片 · 可替换",
        source: { assetId, naturalWidth: photo.width, naturalHeight: photo.height }, fit: "cover",
        size: { width: 1080, height: 720 },
        transform: { x: 0, y, scaleX: 1, scaleY: 1, rotation: 0 },
        data: { provenance: { origin: "contentswarm-material", materialItemId: photo.id } },
      }));
    } else page.children.push(createNode("shape", {
      name: index === 0 ? "上方照片占位" : "下方照片占位", shape: "rect",
      size: { width: 1080, height: 720 }, transform: { x: 0, y, scaleX: 1, scaleY: 1, rotation: 0 },
      fills: [{ type: "solid", color: fromHex(index === 0 ? "#a1a7a2" : "#4c514e")! }],
    }));
  }
  let seed = 72413;
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
  const width = 1080;
  const height = 170;
  const contours: PathContour[] = [];
  for (let i = 0; i < 650; i++) {
    const x = 20 + random() * 1040;
    const y = 120 - x * 0.085 + (random() - 0.5) * 76;
    const length = 4 + random() * 22;
    const grain = 2 + random() * 7;
    contours.push({ closed: true, segments: [
      { x, y }, { x: x + length, y: y - 2 },
      { x: x + length * 0.8, y: y + grain }, { x: x - 2, y: y + grain },
    ] });
  }
  page.children.push(createNode("path", {
    name: "黄色斜向笔刷 · 可改色", size: { width, height }, closed: true,
    segments: [
      { x: 12, y: 115 }, { x: 65, y: 100 }, { x: 1040, y: 19 }, { x: 1062, y: 30 },
      { x: 1020, y: 75 }, { x: 40, y: 160 }, { x: 0, y: 149 },
    ], contours, fills: [{ type: "solid", color: fromHex("#f5ef33")! }],
    transform: { x: 0, y: 660, scaleX: 1, scaleY: 1, rotation: 0 },
  }));
  for (const [index, label] of ["装修第一步", "拆除+回收"].entries()) {
    const text = coverElementText({ id: `before-after-title-${index}`, category: "标题", name: index === 0 ? "主标题 · 可编辑" : "副标题 · 可编辑",
      lines: [label], width: 960, fontSize: 124, weight: 900, color: "#ffffff", outlineColor: "#111111", outlineWidth: 22,
      align: "center" }, page);
    page.children.push(createNode("text", { ...text,
      transform: { x: 60, y: index === 0 ? 540 : 770, scaleX: 1, scaleY: 1, rotation: 0 },
    }));
  }
  return file;
}

/** Native nodes with fixed layout slots; importing a page is one undoable action. */
export function buildCoverLayout(id: (typeof coverLayouts)[number]["id"], background?: CoverBackground, maskOpacity = 0.95, titleComposition?: CoverTitleComposition, titlePlacement: CoverTitlePlacement = "top", styleId?: string) {
  const layout = coverLayouts.find((item) => item.id === id)!;
  const file = createBlankDesign({ width: 1080, height: 1440 });
  file.title = layout.name;
  const page = file.pages[0];
  page.name = layout.name;
  delete page.background;
  let slots = (background ? photoSlots : layoutSlots)[id];
  if (titleComposition) {
    slots = slots.map((slot) => {
      const preset = coverElements.find((item) => item.id === slot.element)!;
      const main = preset.category === "标题";
      if (!main && slot.element !== "description") return slot;
      const text = (slot.lines ?? preset.lines).join(" · ");
      if (titleComposition === "side-by-side") {
        const middle = Math.ceil(text.length / 2);
        return { ...slot, x: main ? 64 : 704, y: main ? 172 : 156,
          width: main ? 608 : 312, fontSize: main ? 38 : 24,
          lines: main ? [text] : [text.slice(0, middle), text.slice(middle)], align: "left" as const };
      }
      const subtitleFirst = titleComposition === "subtitle-first";
      return { ...slot, x: 64, width: 952, fontSize: main ? 60 : 28, lines: [text],
        y: main ? (subtitleFirst ? 198 : 132) : (subtitleFirst ? 132 : 252),
        align: titleComposition === "centered" ? "center" as const : "left" as const };
    });
    const name = coverTitleCompositions.find((item) => item.id === titleComposition)!.name;
    file.title = page.name = `${file.title} · ${name}`;
  }
  const titleOnPhoto = Boolean(background && titlePlacement === "photo-center");
  if (titleOnPhoto) {
    slots = slots.map((slot) => {
      const main = coverElements.find((item) => item.id === slot.element)?.category === "标题";
      if (!main && slot.element !== "description") return slot;
      const sideBySide = titleComposition === "side-by-side";
      const subtitleFirst = titleComposition === "subtitle-first";
      return { ...slot, y: sideBySide ? (main ? 620 : 608) : main ? (subtitleFirst ? 684 : 568) : (subtitleFirst ? 568 : 704) };
    });
    file.title = page.name = `${file.title} · 图片中央无蒙版`;
  }
  const templateStyle = coverStyleTemplates.find((item) => item.id === styleId);
  page.children = slots.map((slot) => {
    const preset = coverElements.find((item) => item.id === slot.element)!;
    const onPhoto = titleOnPhoto && (preset.category === "标题" || slot.element === "description");
    const style = templateStyle && (templateStyle.category === "标题" ? preset.category === "标题" : slot.element === "description") ? templateStyle : undefined;
    const selected = style ?? preset;
    const sideSubtitle = Boolean(style && titleOnPhoto && style.category === "副标题" && titleComposition === "side-by-side");
    const subtitleLabel = selected.lines.join(" · ");
    const middle = Math.ceil(subtitleLabel.length / 2);
    const styled = {
      ...selected,
      lines: sideSubtitle ? [subtitleLabel.slice(0, middle), subtitleLabel.slice(middle)] :
        style && style.category === "标题" && (titleComposition || background) ? [selected.lines.join(" · ")] : style ? selected.lines : slot.lines ?? preset.lines,
      fontSize: style && background && titlePlacement === "top" && style.category === "副标题" ? 18 :
        style && style.category === "标题" && (background || (titleComposition && titleComposition !== "side-by-side")) ? 48 :
        style && titleComposition === "subtitle-first" && !background && style.category === "副标题" && style.background ? 32 :
        sideSubtitle ? Math.min(selected.fontSize, 36) :
        style && titleOnPhoto && style.category === "副标题" ? selected.fontSize :
        Math.min(slot.fontSize ?? selected.fontSize, selected.fontSize),
      width: Math.min(slot.width ?? selected.width, selected.width, 1080 - slot.x - 64),
      align: slot.align ?? selected.align,
      color: onPhoto && !style ? "#ffffff" : selected.color,
    };
    const node = buildCoverElement(styled, page);
    const y = style && background && titlePlacement === "top" && style.category === "副标题"
      ? titleComposition === "subtitle-first" ? 132 : 244
      : templateStyle?.category === "副标题" && titleOnPhoto && titleComposition === "subtitle-first" && preset.category === "标题"
        ? 710
      : templateStyle?.category === "副标题" && titleComposition === "subtitle-first" && preset.category === "标题" && titlePlacement === "top"
        ? background ? 214 : 240 : slot.y;
    node.transform = { x: slot.x, y, scaleX: 1, scaleY: 1, rotation: 0 };
    if (onPhoto && node.type === "text" && !style) node.textEffects = [{ kind: "shadow", dx: 0, dy: 3, blur: 8,
      color: { type: "solid", color: fromHex("#18232f")! }, opacity: 0.85 }];
    return node;
  });
  if (templateStyle) file.title = page.name = `${file.title} · ${templateStyle.name}`;
  if (background) {
    const assetId = `cover-background-${background.id}`;
    file.assets.push({ id: assetId, kind: "image", url: background.url, mime: background.mime, checksum: "" });
    page.children.unshift(createNode("image", {
        name: `底图 · ${background.name}`,
        source: { assetId, naturalWidth: background.width, naturalHeight: background.height },
        fit: "cover",
        size: { width: 1080, height: 1440 },
        transform: { x: 0, y: 0, scaleX: 1, scaleY: 1, rotation: 0 },
        data: { provenance: { origin: "contentswarm-material", materialItemId: background.id }, background: true },
      }));
    if (!titleOnPhoto) page.children.splice(1, 0, ...[0, 1120].map((y) => createNode("shape", {
        name: y === 0 ? "顶部信息区底色" : "底部信息区底色",
        shape: "rect",
        size: { width: 1080, height: 320 },
        transform: { x: 0, y, scaleX: 1, scaleY: 1, rotation: 0 },
        fills: [{ type: "solid", color: fromHex("#ffffff")! }],
        opacity: maskOpacity,
      })));
  }
  return file;
}
