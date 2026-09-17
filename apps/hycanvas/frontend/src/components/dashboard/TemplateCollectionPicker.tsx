import { useEffect, useState } from "react";
import type { TemplateCollectionSummary } from "@hc/sdk";
import { oc } from "@/lib/sdk";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";

export function TemplateCollectionPicker({
  workspaceId,
  value,
  onChange,
  required = true,
}: {
  workspaceId: string | null;
  value: string;
  onChange: (collectionId: string, collection: TemplateCollectionSummary) => void;
  required?: boolean;
}) {
  const toast = useToast();
  const [collections, setCollections] = useState<TemplateCollectionSummary[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingCollections, setLoadingCollections] = useState(false);

  useEffect(() => {
    if (!workspaceId || typeof oc.listTemplateCollections !== "function") return;
    let cancelled = false;
    setLoadingCollections(true);
    void oc.listTemplateCollections(workspaceId).then((items) => {
      if (!cancelled) setCollections(items);
    }).catch(() => {
      if (!cancelled) setCollections([]);
    }).finally(() => {
      if (!cancelled) setLoadingCollections(false);
    });
    return () => { cancelled = true; };
  }, [workspaceId]);

  async function create() {
    const trimmed = name.trim();
    if (!workspaceId || !trimmed || loading || typeof oc.createTemplateCollection !== "function") return;
    setLoading(true);
    try {
      const collection = await oc.createTemplateCollection(workspaceId, trimmed);
      setCollections((current) => [...current, collection]);
      onChange(collection.id, collection);
      setName("");
      setCreating(false);
    } catch {
      toast.error("创建模板分类失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }

  const selected = collections.find((item) => item.id === value);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium text-neutral-700">
          模板分类{required && <span className="ms-1 text-red-500">*</span>}
        </label>
        <button type="button" className="text-xs font-medium text-brand-600 hover:underline" onClick={() => setCreating((open) => !open)}>
          {creating ? "取消" : "+ 新建分类"}
        </button>
      </div>
      <select
        value={value}
        required={required && !loadingCollections}
        disabled={loadingCollections}
        onChange={(event) => {
          const collection = collections.find((item) => item.id === event.target.value);
          if (collection) onChange(collection.id, collection);
        }}
        className="h-11 rounded-xl border border-neutral-200 bg-surface px-3.5 text-sm text-neutral-900 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
      >
        <option value="">{loadingCollections ? "正在加载分类…" : "请选择模板分类"}</option>
        {collections.map((collection) => <option key={collection.id} value={collection.id}>{collection.name}</option>)}
      </select>
      {selected && <span className="text-xs text-neutral-500">保存后可在“模板”页面按分类查看。</span>}
      {creating && (
        <div className="flex gap-2 rounded-xl border border-brand-100 bg-brand-50 p-2">
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void create(); } }}
            placeholder="例如：小红书模板专区"
            autoFocus
            className="h-9 min-w-0 flex-1 rounded-lg border border-neutral-200 bg-white px-2.5 text-sm outline-none focus:border-brand-500"
          />
          <Button size="sm" disabled={!name.trim() || loading} onClick={() => void create()}>{loading ? "创建中…" : "创建"}</Button>
        </div>
      )}
      {required && collections.length === 0 && !loadingCollections && !creating && (
        <p className="text-xs text-amber-600">当前还没有分类，请点击“+ 新建分类”。</p>
      )}
    </div>
  );
}
