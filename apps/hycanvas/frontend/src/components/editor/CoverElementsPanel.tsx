import { useState } from "react";
import { coverElements, coverElementText } from "@/lib/coverElements";
import { useEditor } from "@/store/editor";
import { useToast } from "@/components/ui/Toast";

import { CoverLayoutsPanel } from "./CoverLayoutsPanel";

const categories = ["全部", "标题", "副标题", "标签", "数字"] as const;

export function CoverElementsPanel() {
  const [mode, setMode] = useState<"elements" | "layouts">("elements");
  const [category, setCategory] = useState<string>("全部");
  const toast = useToast();
  return (
    <div>
      <div className="mb-3 grid grid-cols-2 gap-1 rounded-lg bg-neutral-100 p-1">
        <button type="button" aria-pressed={mode === "elements"} onClick={() => setMode("elements")} className={`rounded-md py-2 text-xs font-medium ${mode === "elements" ? "bg-surface text-brand-ink shadow-sm" : "text-neutral-600"}`}>单个元素</button>
        <button type="button" aria-pressed={mode === "layouts"} onClick={() => setMode("layouts")} className={`rounded-md py-2 text-xs font-medium ${mode === "layouts" ? "bg-surface text-brand-ink shadow-sm" : "text-neutral-600"}`}>自动排版</button>
      </div>
      {mode === "layouts" ? <CoverLayoutsPanel /> : <>
      <p className="mb-3 text-xs leading-5 text-neutral-500">适配 1080 × 1440 封面。点击添加，双击文字编辑；数字与单位可分别设置样式。</p>
      <div className="mb-4 flex flex-wrap gap-1" aria-label="元素分类">
        {categories.map((item) => (
          <button key={item} type="button" aria-pressed={category === item} onClick={() => setCategory(item)} className={`rounded-full border px-3 py-1 text-xs ${category === item ? "border-brand-400 bg-brand-50 text-brand-ink" : "border-neutral-200 text-neutral-600"}`}>{item}</button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        {coverElements.filter((item) => category === "全部" || item.category === category).map((item) => (
          <button key={item.id} type="button" aria-label={`添加${item.name}`} className="overflow-hidden rounded-xl border border-neutral-200 bg-surface text-start hover:border-brand-400 focus-visible:outline-2 focus-visible:outline-brand-400" onClick={() => {
            const editor = useEditor.getState();
            editor.addNode("text", coverElementText(item, editor.doc.pages[editor.activePage]));
            toast.success(`已添加${item.name}，双击文字即可编辑`);
          }}>
            <div className="flex h-24 items-center justify-center bg-neutral-50 px-2" aria-hidden="true">
              <span style={{ color: item.color, background: item.background, fontWeight: item.weight, borderRadius: item.category === "标签" ? 5 : 2, padding: item.background ? "4px 6px" : 0, fontSize: item.category === "数字" ? 26 : item.category === "标题" ? 12 : 10, whiteSpace: "pre-line", lineHeight: 1.5 }}>
                {item.lines.join("\n")}<span style={{ fontSize: "0.4em" }}>{item.unit}</span>
              </span>
            </div>
            <div className="px-3 py-2 text-xs font-medium text-neutral-700">{item.name}</div>
          </button>
        ))}
      </div>
      <p className="mt-3 text-xs leading-5 text-neutral-400">示例文字和数字可自由替换，价格仅为占位示例。</p>
      </>}
    </div>
  );
}
