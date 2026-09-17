import { useEffect, useMemo, useRef } from "react";
import { createScene, renderScene, type CanvasLike } from "@hc/engine";
import type { DesignFile } from "@hc/schema";
import { buildCoverLayout, coverLayouts } from "@/lib/coverElements";
import { useEditor } from "@/store/editor";
import { useToast } from "@/components/ui/Toast";

function LayoutPreview({ file }: { file: DesignFile }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const context = ref.current?.getContext("2d");
    if (!context) return;
    renderScene(createScene(file), context as unknown as CanvasLike, {
      zoom: 0.2, panX: 0, panY: 0, dpr: 1, width: 216, height: 288,
    });
  }, [file]);
  return <canvas ref={ref} width={216} height={288} className="mx-auto h-auto w-full max-w-40 rounded-md" aria-label={`${file.title}预览`} />;
}

export function CoverLayoutsPanel() {
  const toast = useToast();
  const options = useMemo(() => coverLayouts.map((layout) => ({ ...layout, file: buildCoverLayout(layout.id) })), []);
  return (
    <div className="space-y-3">
      <p className="text-xs leading-5 text-neutral-500">自动组合元素，在新页面生成 1080 × 1440 封面。文字、数字、标签均可编辑；完成后可通过顶部菜单“保存为模板”重复使用。</p>
      {options.map((layout) => (
        <button key={layout.id} type="button" aria-label={`生成${layout.name}`} className="block w-full rounded-xl border border-neutral-200 bg-neutral-50 p-3 text-start hover:border-brand-400 focus-visible:outline-2 focus-visible:outline-brand-400" onClick={() => {
          const count = useEditor.getState().importPagesFrom(layout.file, [0], { matchTheme: false, preservePageSize: true });
          if (count) {
            useEditor.getState().fitToScreen();
            toast.success(`已生成${layout.name}，可直接编辑并保存为模板`);
          }
          else toast.error("当前设计不可编辑，无法生成封面");
        }}>
          <LayoutPreview file={layout.file} />
          <div className="mt-3 text-sm font-semibold text-neutral-800">{layout.name}</div>
          <p className="mt-1 text-xs leading-5 text-neutral-500">{layout.description}</p>
        </button>
      ))}
      <p className="text-xs leading-5 text-neutral-400">使用示例内容排版，请将价格、面积和服务信息替换为真实资料。</p>
    </div>
  );
}
