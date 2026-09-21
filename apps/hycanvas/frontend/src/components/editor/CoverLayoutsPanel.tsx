import { useEffect, useMemo, useRef, useState } from "react";
import { createScene, renderScene, type CanvasLike } from "@hc/engine";
import type { DesignFile } from "@hc/schema";
import { buildBeforeAfterCover, buildCoverLayout, coverLayouts, coverStyleTemplates, coverTitleCompositions, type CoverTitleComposition, type CoverBackground } from "@/lib/coverElements";
import { useEditor } from "@/store/editor";
import { useToast } from "@/components/ui/Toast";

import { requestContentSwarmMaterials, type ContentSwarmGallery, type ContentSwarmMaterial } from "@/lib/managedAuth";
import { imageAssets } from "@/lib/assetProvider";
import { browserEditorialMeasure, buildXhsEditorialCover, readXhsEditorialContent, xhsEditorialDefault } from "@/lib/xhsEditorialCover";
import { buildPersonalizedCoverLayout, personalizedCoverLayouts } from "@/lib/personalizedCoverLayouts";

function BackgroundTile({ item, selected, disabled, onSelect }: { item: ContentSwarmMaterial; selected: boolean; disabled: boolean; onSelect: () => void }) {
  const [preview, setPreview] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    let cancelled = false;
    requestContentSwarmMaterials<{ data_url: string }>("get-file", { item_id: item.id, purpose: "preview" })
      .then((file) => { if (!cancelled) setPreview(file.data_url); })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [item.id]);
  return <button type="button" disabled={disabled} aria-label={`选择底图：${item.name}`} aria-pressed={selected} onClick={onSelect} className={`overflow-hidden rounded-lg border text-start disabled:opacity-50 ${selected ? "border-brand-500 ring-2 ring-brand-200" : "border-neutral-200"}`}>
    {preview ? (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={preview} alt="" className="aspect-square w-full object-cover" />
    ) : <span className="grid aspect-square place-items-center text-xs text-neutral-400">{error ? "预览失败" : "加载中"}</span>}
    <span className="block truncate p-1 text-xs text-neutral-600">{item.name}</span>
  </button>;
}

function LayoutPreview({ file, transparent = false }: { file: DesignFile; transparent?: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const context = ref.current?.getContext("2d");
    if (!context) return;
    const draw = () => renderScene(createScene(file), context as unknown as CanvasLike, {
      zoom: 0.2, panX: 0, panY: 0, dpr: 1, width: 216, height: 288,
    }, { assets: imageAssets });
    const unsubscribe = imageAssets.onChange(draw);
    imageAssets.registerAll(file.assets);
    draw();
    return unsubscribe;
  }, [file]);
  return <div className="mx-auto w-full max-w-40 overflow-hidden rounded-md" style={transparent ? {
    backgroundColor: "#64748b",
    backgroundImage: "linear-gradient(45deg, #475569 25%, transparent 25%), linear-gradient(-45deg, #475569 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #475569 75%), linear-gradient(-45deg, transparent 75%, #475569 75%)",
    backgroundPosition: "0 0, 0 8px, 8px -8px, -8px 0",
    backgroundSize: "16px 16px",
  } : undefined}>
    <canvas ref={ref} width={216} height={288} className="block h-auto w-full" aria-label={`${file.title}预览`} />
  </div>;
}

export function CoverLayoutsPanel() {
  const toast = useToast();
  const [titleComposition, setTitleComposition] = useState<CoverTitleComposition>();
  const [scope, setScope] = useState("private");
  const [gallery, setGallery] = useState("");
  const [page, setPage] = useState(1);
  const [galleries, setGalleries] = useState<ContentSwarmGallery[]>([]);
  const [galleryError, setGalleryError] = useState("");
  const [state, setState] = useState<{ key: string; items: ContentSwarmMaterial[]; total: number; error: string }>({ key: "", items: [], total: 0, error: "" });
  const [background, setBackground] = useState<CoverBackground>();
  const [bottomBackground, setBottomBackground] = useState<CoverBackground>();
  const [photoTarget, setPhotoTarget] = useState<"top" | "bottom">("top");
  const [styleCategory, setStyleCategory] = useState<"全部" | "标题" | "副标题">("全部");
  const [stylePage, setStylePage] = useState(1);
  const [personalizedGroup, setPersonalizedGroup] = useState<"template-1" | "template-3" | "template-4" | "curated">("template-1");
  const [selecting, setSelecting] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [editorialPreview, setEditorialPreview] = useState<DesignFile | null>(null);
  const [editorialPreviewError, setEditorialPreviewError] = useState("");
  const key = `${scope}/${gallery}/${page}/${refresh}`;
  const loading = state.key !== key;
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      const result = buildXhsEditorialCover(xhsEditorialDefault, browserEditorialMeasure());
      if (result.success) setEditorialPreview(result.file);
      else setEditorialPreviewError(result.errors[0].message);
    });
    return () => { cancelled = true; };
  }, []);
  async function applyEditorialTemplate() {
    await document.fonts.load("900 120px system-ui");
    const editor = useEditor.getState();
    const page = editor.doc.pages[editor.activePage];
    if (!page || page.width !== 1080 || page.height !== 1440) {
      toast.error("请先选择 1080 × 1440 的封面页面");
      return;
    }
    const content = readXhsEditorialContent(page.children) ?? xhsEditorialDefault;
    const result = buildXhsEditorialCover(content, browserEditorialMeasure());
    if (!result.success) { toast.error(result.errors[0].message); return; }
    if (editor.applyXhsEditorialTemplate(result.file)) toast.success("已应用左对齐大标题模板，可编辑文字并重新排版");
    else toast.error("当前设计不可编辑");
  }
  useEffect(() => {
    let cancelled = false;
    requestContentSwarmMaterials<{ galleries: ContentSwarmGallery[] }>("list-galleries")
      .then((result) => { if (!cancelled) { setGalleries(result.galleries); setGalleryError(""); } })
      .catch((error: Error) => { if (!cancelled) setGalleryError(error.message); });
    return () => { cancelled = true; };
  }, [refresh]);
  useEffect(() => {
    let cancelled = false;
    requestContentSwarmMaterials<{ items: ContentSwarmMaterial[]; total: number }>("list-items", { scope, category: gallery || undefined, page, page_size: 6 })
      .then((result) => { if (!cancelled) setState({ key, ...result, error: "" }); })
      .catch((error: Error) => { if (!cancelled) setState({ key, items: [], total: 0, error: error.message }); });
    return () => { cancelled = true; };
  }, [scope, gallery, page, key]);

  async function selectBackground(item: ContentSwarmMaterial) {
    setSelecting(true);
    try {
      const file = await requestContentSwarmMaterials<{ data_url: string; content_type: string }>("get-file", { item_id: item.id, purpose: "insert" });
      const image = new Image();
      image.src = file.data_url;
      await image.decode();
      const selected = { id: item.id, name: item.name, url: file.data_url, mime: file.content_type, width: image.naturalWidth, height: image.naturalHeight };
      if (photoTarget === "top") setBackground(selected);
      else setBottomBackground(selected);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "底图加载失败，请重新选择");
    } finally { setSelecting(false); }
  }
  const styles = useMemo(() => coverStyleTemplates.filter((item) => styleCategory === "全部" || item.category === styleCategory), [styleCategory]);
  const stylePageCount = Math.ceil(styles.length / 8);
  const visibleStyles = useMemo(() => styles.slice((stylePage - 1) * 8, stylePage * 8), [styles, stylePage]);
  const beforeAfterFile = useMemo(() => buildBeforeAfterCover(background, bottomBackground), [background, bottomBackground]);
  const options = useMemo(() => [
    ...visibleStyles.map((style) => ({ id: style.id, name: style.name, description: `透明底 · ${style.category}与装饰可编辑`, file: buildCoverLayout("quote", background, 0.95, titleComposition, "photo-center", style.id) })),
    ...coverLayouts.map((layout) => ({ ...layout, file: buildCoverLayout(layout.id, background, 0.95, titleComposition, "photo-center") })),
  ], [background, titleComposition, visibleStyles]);
  const personalizedOptions = useMemo(() => personalizedCoverLayouts
    .filter((layout) => {
      const curated = ["staircase", "center-focus", "diagonal"].includes(layout.composition);
      return personalizedGroup === "curated" ? curated : !curated && layout.family === personalizedGroup;
    })
    .map((layout) => ({ ...layout, file: buildPersonalizedCoverLayout(layout) })), [personalizedGroup]);
  return (
    <div className="space-y-3">
      <p className="text-xs leading-5 text-neutral-500">自动组合可编辑元素。左对齐大标题模板应用在当前封面，其他模板生成新页面；完成后可通过顶部菜单“保存为模板”重复使用。</p>
      <div className="space-y-2 rounded-lg border border-neutral-200 p-2">
        <div className="flex items-center justify-between"><span className="text-sm font-semibold">素材库底图</span><button type="button" onClick={() => setRefresh((value) => value + 1)} className="text-xs text-brand-ink">刷新图库</button></div>
        <div className="grid grid-cols-2 gap-2">{(["top", "bottom"] as const).map((target) => <button key={target} type="button" aria-pressed={photoTarget === target} onClick={() => setPhotoTarget(target)} className={`min-w-0 truncate rounded-md border p-2 text-xs ${photoTarget === target ? "border-brand-500 bg-brand-50 text-brand-ink" : "border-neutral-200 text-neutral-600"}`}>{target === "top" ? "选上图" : "选下图"}：{(target === "top" ? background : bottomBackground)?.name ?? "未选择"}</button>)}</div>
        <div className="flex gap-2">
          {[{ value: "private", label: "我的素材" }, { value: "enterprise", label: "企业共享" }].map((option) => <button key={option.value} type="button" aria-pressed={scope === option.value} onClick={() => { setScope(option.value); setGallery(""); setPage(1); }} className={`rounded-md px-2 py-1 text-xs ${scope === option.value ? "bg-brand-50 text-brand-ink" : "text-neutral-600"}`}>{option.label}</button>)}
        </div>
        <select aria-label="选择底图图库" value={gallery} onChange={(event) => { setGallery(event.target.value); setPage(1); }} className="w-full rounded-md border border-neutral-200 bg-surface p-2 text-xs">
          <option value="">全部图库</option>
          {galleries.filter((item) => (item.visibility || "private") === scope).map((item) => <option key={item.id} value={item.id}>{item.parent_id ? "└ " : ""}{item.name}（{item.count}）</option>)}
        </select>
        {galleryError && <p role="alert" className="text-xs text-red-500">{galleryError}</p>}
        {loading ? <p role="status" className="py-4 text-center text-xs text-neutral-500">正在读取素材库…</p> : state.error ? <p role="alert" className="text-xs text-red-500">{state.error}</p> : state.items.length ? <>
          <div className="grid grid-cols-3 gap-1">{state.items.map((item) => <BackgroundTile key={item.id} item={item} selected={(photoTarget === "top" ? background : bottomBackground)?.id === item.id} disabled={selecting} onSelect={() => void selectBackground(item)} />)}</div>
          <div className="flex justify-between text-xs"><button type="button" disabled={page === 1} onClick={() => setPage(page - 1)} className="disabled:opacity-40">上一页</button><span>{page} / {Math.ceil(state.total / 6)}</span><button type="button" disabled={page * 6 >= state.total} onClick={() => setPage(page + 1)} className="disabled:opacity-40">下一页</button></div>
        </> : <p className="py-3 text-xs text-neutral-500">当前图库暂无图片，可在素材库上传或切换图库。</p>}
        {selecting && <p role="status" className="text-xs text-brand-ink">正在加载底图原图…</p>}
        {background && <>
          <div className="flex gap-2 text-xs"><span className="min-w-0 flex-1 truncate">上图／其他模板底图：{background.name}</span><button type="button" disabled={selecting} onClick={() => setBackground(undefined)} className="text-brand-ink">移除上图</button></div>
          <p className="text-xs leading-5 text-neutral-500">标题放在图片中央，不添加上下信息区底色。</p>
        </>}
        {bottomBackground && <div className="flex gap-2 text-xs"><span className="min-w-0 flex-1 truncate">下图：{bottomBackground.name}</span><button type="button" disabled={selecting} onClick={() => setBottomBackground(undefined)} className="text-brand-ink">移除下图</button></div>}
      </div>
      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold">主标题与副标题组合</legend>
        <div className="grid grid-cols-2 gap-2">
          {coverTitleCompositions.map((item) => <button key={item.id} type="button" aria-pressed={titleComposition === item.id} onClick={() => setTitleComposition(item.id)} className={`rounded-lg border p-2 text-center ${titleComposition === item.id ? "border-brand-500 bg-brand-50 text-brand-ink" : "border-neutral-200 text-neutral-600"}`}>
            <span aria-hidden="true" className="flex h-12 items-center justify-center whitespace-pre-line text-sm font-semibold">{item.symbol}</span>
            <span className="text-xs">{item.name}</span>
          </button>)}
        </div>
      </fieldset>
      <div className="space-y-2">
        <div className="text-sm font-semibold">双图施工封面</div>
        <button type="button" disabled={selecting || !background || !bottomBackground} aria-label="生成装修前后对比" className="block w-full rounded-xl border border-neutral-200 bg-neutral-50 p-3 text-start hover:border-brand-400 disabled:opacity-60" onClick={() => {
          const count = useEditor.getState().importPagesFrom(beforeAfterFile, [0], { matchTheme: false, preservePageSize: true });
          if (count) { useEditor.getState().fitToScreen(); toast.success("已生成装修前后对比封面，可替换图片并编辑文字"); }
          else toast.error("当前设计不可编辑，无法生成封面");
        }}>
          <LayoutPreview file={beforeAfterFile} transparent />
          <div className="mt-2 text-sm font-semibold text-neutral-800">装修前后对比</div>
          <p className="mt-1 text-xs leading-5 text-neutral-500">上下双图、斜向黄色笔刷、白字黑色粗描边。先选上图和下图，再生成封面。</p>
        </button>
      </div>
      <div className="space-y-2">
        <div className="flex items-center justify-between"><span className="text-sm font-semibold">封面模板</span><span className="text-xs text-neutral-500">{styles.length} 种样式 · 4 种版式</span></div>
        <div className="flex gap-2">{(["全部", "标题", "副标题"] as const).map((category) => <button key={category} type="button" aria-pressed={styleCategory === category} onClick={() => { setStyleCategory(category); setStylePage(1); }} className={`rounded-md px-2 py-1 text-xs ${styleCategory === category ? "bg-brand-50 text-brand-ink" : "text-neutral-600"}`}>{category}</button>)}</div>
      </div>
      {editorialPreview && <div className="rounded-xl border border-brand-300 bg-brand-50 p-2">
        <button type="button" className="w-full text-start" onClick={() => void applyEditorialTemplate()} aria-label="应用左对齐大标题模板">
          <LayoutPreview file={editorialPreview} transparent />
          <span className="mt-2 block text-sm font-semibold">左对齐大标题</span>
          <span className="block text-xs text-neutral-600">在当前封面排版，保留底图和独立素材</span>
        </button>
        <button type="button" className="mt-2 rounded-md border border-brand-300 px-2 py-1 text-xs text-brand-ink" onClick={() => void applyEditorialTemplate()}>重新排版（保留文案，恢复默认位置和字号）</button>
      </div>}
      {editorialPreviewError && <p role="alert" className="text-xs text-red-600">模板预览失败：{editorialPreviewError}</p>}
      <div className="space-y-2 rounded-xl border border-brand-200 bg-brand-50/40 p-2">
        <div className="flex items-center justify-between"><span className="text-sm font-semibold">基于你的模板</span><span className="text-xs text-neutral-500">15 个位置 · 3 个精选</span></div>
        <p className="text-xs leading-5 text-neutral-500">透明底，可叠加在任意底图上；主体文字保持可编辑，模板3的底部城市说明固定在原位。</p>
        <div className="grid grid-cols-4 gap-1" aria-label="个性化模板分类">
          {([[
            "template-1", "模板1",
          ], ["template-3", "模板3"], ["template-4", "模板4"], ["curated", "精选"]] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={personalizedGroup === value} onClick={() => setPersonalizedGroup(value)} className={`rounded-md px-1 py-1.5 text-xs ${personalizedGroup === value ? "bg-surface text-brand-ink shadow-sm" : "text-neutral-600"}`}>{label}</button>)}
        </div>
        <div className="grid grid-cols-2 gap-2">{personalizedOptions.map((layout) => (
          <button key={layout.id} type="button" aria-label={`生成${layout.name}`} className="min-w-0 rounded-xl border border-neutral-200 bg-surface p-2 text-start hover:border-brand-400 focus-visible:outline-2 focus-visible:outline-brand-400" onClick={() => {
            const count = useEditor.getState().importPagesFrom(layout.file, [0], { matchTheme: false, preservePageSize: true });
            if (count) {
              useEditor.getState().fitToScreen();
              toast.success(`已生成${layout.name}，透明底和文字均可继续编辑`);
            } else toast.error("当前设计不可编辑，无法生成封面");
          }}>
            <LayoutPreview file={layout.file} transparent />
            <div className="mt-2 text-xs font-semibold text-neutral-800">{layout.name}</div>
            <p className="mt-1 text-xs leading-4 text-neutral-500">{layout.description}</p>
          </button>
        ))}</div>
      </div>
      <div className="grid grid-cols-2 gap-2">{options.map((layout) => (
        <button key={layout.id} type="button" disabled={selecting} aria-label={`生成${layout.name}`} className="min-w-0 rounded-xl border border-neutral-200 bg-neutral-50 p-2 text-start hover:border-brand-400 focus-visible:outline-2 focus-visible:outline-brand-400" onClick={() => {
          const count = useEditor.getState().importPagesFrom(layout.file, [0], { matchTheme: false, preservePageSize: true });
          if (count) {
            useEditor.getState().fitToScreen();
            toast.success(`已生成${layout.name}，可直接编辑并保存为模板`);
          }
          else toast.error("当前设计不可编辑，无法生成封面");
        }}>
          <LayoutPreview file={layout.file} transparent />
          <div className="mt-2 text-xs font-semibold text-neutral-800">{layout.name}</div>
          <p className="mt-1 text-xs leading-4 text-neutral-500">{layout.description}</p>
        </button>
      ))}</div>
      <div className="flex items-center justify-between text-xs"><button type="button" disabled={stylePage === 1} onClick={() => setStylePage(stylePage - 1)} className="disabled:opacity-40">上一页样式</button><span>{stylePage} / {stylePageCount}</span><button type="button" disabled={stylePage >= stylePageCount} onClick={() => setStylePage(stylePage + 1)} className="disabled:opacity-40">下一页样式</button></div>
      <p className="text-xs leading-5 text-neutral-400">模板中的文字均可继续编辑。</p>
    </div>
  );
}
