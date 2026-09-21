// Save-as-template dialog. Captures a title, category, and
// visibility, then saves the current design as a reusable template via the SDK.
// Sends the design id as the stable template identity and the in-memory file as
// the current snapshot. Unsaved designs fall back to the inline file alone.

import { useMemo, useState } from "react";
import { childrenOf, type Node } from "@hc/schema";
import type { FillableFieldSummary, TemplateVisibility } from "@hc/sdk";
import { oc } from "@/lib/sdk";
import { useEditor } from "@/store/editor";
import { useToast } from "@/components/ui/Toast";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { tr } from "@/lib/i18n";
import { templateTagForZone, templateZoneFromMeta } from "@/lib/templateZones";
import { createDesignThumbnail } from "@/lib/designThumbnail";
import { TemplateCollectionPicker } from "@/components/dashboard/TemplateCollectionPicker";

const visibilities = (): { value: TemplateVisibility; label: string; hint: string }[] => [
  { value: "private", label: tr("editor.only_me"), hint: tr("editor.visible_only_to_you") },
  { value: "workspace", label: tr("editor.my_team"), hint: tr("editor.visible_to_workspace_members") },
  { value: "public", label: tr("editor.everyone"), hint: tr("editor.public_template_free_to_all") },
];

const semanticRoles = [
  ["title", "内容标题"],
  ["subtitle", "内容副标题"],
  ["project_name", "项目名称"],
  ["project_name_en", "项目英文名"],
  ["project_area", "项目面积"],
  ["designer", "设计师"],
  ["completion_year", "完成年份"],
  ["brand_name", "品牌名称"],
  ["label", "标签（保留原文）"],
  ["body_excerpt", "正文摘要"],
] as const;

type TextNodeSummary = { id: string; text: string; fontSize?: number; width?: number; height?: number };

function textNodes(file: ReturnType<typeof useEditor.getState>["doc"]): TextNodeSummary[] {
  const result: TextNodeSummary[] = [];
  const visit = (node: Node) => {
    if (node.type === "text") {
      const content = (node as unknown as { content?: { runs?: { text?: string }[] }[] }).content ?? [];
      const text = content.map((p) => (p.runs ?? []).map((r) => r.text ?? "").join("")).join(" ").trim();
      const firstRun = (node as unknown as {
        content?: { runs?: { style?: { fontSize?: number } }[] }[];
      }).content?.[0]?.runs?.[0];
      const box = (node as unknown as { box?: { width?: number; height?: number } }).box;
      const size = (node as unknown as { size?: { width?: number; height?: number } }).size;
      result.push({
        id: node.id,
        text: text || node.name || "未命名文字",
        fontSize: firstRun?.style?.fontSize,
        width: box?.width ?? size?.width,
        height: box?.height ?? size?.height,
      });
    }
    for (const child of childrenOf(node)) visit(child);
  };
  for (const page of file.pages) for (const node of page.children) visit(node);
  return result;
}

function nextFieldKey(usedKeys: Set<string>, startAt: number): string {
  let index = Math.max(1, startAt);
  while (usedKeys.has(`field_${index}`)) index += 1;
  return `field_${index}`;
}

function normalizeFillableFields(
  file: ReturnType<typeof useEditor.getState>["doc"],
  fields?: Partial<FillableFieldSummary>[],
): FillableFieldSummary[] {
  const nodes = textNodes(file);
  const textByNodeId = new Map(nodes.map((node) => [node.id, node.text]));
  const declared = fields ?? (file.meta as { brandEditableFields?: Partial<FillableFieldSummary>[] } | undefined)
    ?.brandEditableFields;
  if (!Array.isArray(declared)) return [];

  const resolvedNodeIds = new Map<number, string>();
  const usedNodeIds = new Set<string>();
  declared.forEach((field, index) => {
    if (typeof field.nodeId === "string" && textByNodeId.has(field.nodeId)) {
      resolvedNodeIds.set(index, field.nodeId);
      usedNodeIds.add(field.nodeId);
    }
  });

  // Older template copies regenerated scene node ids without updating the
  // field protocol stored in document metadata. Typography survives the copy,
  // so it provides a stable way to reconnect those declarations. Exact/direct
  // ids above always win; unmatched declarations are paired greedily by font
  // size with box dimensions as a tie-breaker.
  const candidates: Array<{ fieldIndex: number; nodeId: string; score: number }> = [];
  declared.forEach((field, fieldIndex) => {
    if (resolvedNodeIds.has(fieldIndex)) return;
    const typography = field.typography;
    const fieldFontSize = typography?.runs?.[0]?.fontSize;
    if (typeof fieldFontSize !== "number") return;
    const fieldWidth = typeof typography?.box?.width === "number" ? typography.box.width : undefined;
    const fieldHeight = typeof typography?.box?.height === "number" ? typography.box.height : undefined;
    for (const node of nodes) {
      if (usedNodeIds.has(node.id) || typeof node.fontSize !== "number") continue;
      const fontDelta = Math.abs(fieldFontSize - node.fontSize);
      if (fontDelta > 0.5) continue;
      const widthDelta = fieldWidth && node.width ? Math.abs(fieldWidth - node.width) / Math.max(fieldWidth, node.width) : 0;
      const heightDelta = fieldHeight && node.height ? Math.abs(fieldHeight - node.height) / Math.max(fieldHeight, node.height) : 0;
      candidates.push({ fieldIndex, nodeId: node.id, score: fontDelta + widthDelta * 0.1 + heightDelta * 0.1 });
    }
  });
  candidates.sort((left, right) => left.score - right.score || left.fieldIndex - right.fieldIndex);
  const matchedFields = new Set<number>();
  for (const candidate of candidates) {
    if (matchedFields.has(candidate.fieldIndex) || usedNodeIds.has(candidate.nodeId)) continue;
    resolvedNodeIds.set(candidate.fieldIndex, candidate.nodeId);
    matchedFields.add(candidate.fieldIndex);
    usedNodeIds.add(candidate.nodeId);
  }

  const usedKeys = new Set<string>();
  const normalized: FillableFieldSummary[] = [];
  for (const [index, field] of declared.entries()) {
    const nodeId = resolvedNodeIds.get(index) ?? "";
    const nodeText = textByNodeId.get(nodeId);
    if (!nodeText) continue;

    const existingKey = typeof field.key === "string" ? field.key.trim() : "";
    const key = existingKey && !usedKeys.has(existingKey)
      ? existingKey
      : nextFieldKey(usedKeys, normalized.length + 1);
    usedKeys.add(key);

    const semanticRole = semanticRoles.some(([value]) => value === field.semanticRole)
      ? field.semanticRole as FillableFieldSummary["semanticRole"]
      : "title";
    const configuredMaxChars = Number(field.constraints?.maxChars);
    const maxChars = Number.isFinite(configuredMaxChars) && configuredMaxChars > 0
      ? Math.min(500, Math.floor(configuredMaxChars))
      : Math.max(4, Math.min(120, nodeText.length || 20));

    normalized.push({
      ...field,
      nodeId,
      kind: "text",
      key,
      label: typeof field.label === "string" && field.label.trim()
        ? field.label.trim()
        : nodeText.slice(0, 30),
      semanticRole,
      constraints: {
        ...field.constraints,
        required: semanticRole === "label" ? false : (field.constraints?.required ?? true),
        maxChars,
      },
    });
  }
  return normalized;
}

export function SaveAsTemplateDialog({
  open,
  onClose,
  onSaved,
  designId,
  workspaceId,
}: {
  open: boolean;
  onClose: () => void;
  onSaved?: (fields: FillableFieldSummary[]) => void | Promise<void>;
  designId: string | null;
  workspaceId: string | null;
}) {
  const toast = useToast();
  const docTitle = useEditor((s) => s.doc.title);
  const templateZone = useEditor((s) => templateZoneFromMeta(s.doc.meta));
  const zoneTag = templateTagForZone(templateZone);
  const [title, setTitle] = useState(docTitle);
  const [category, setCategory] = useState(zoneTag ?? "");
  const [collectionId, setCollectionId] = useState("");
  const [visibility, setVisibility] = useState<TemplateVisibility>("workspace");
  const [busy, setBusy] = useState(false);
  const doc = useEditor((s) => s.doc);
  const availableTextNodes = useMemo(() => textNodes(doc), [doc]);
  const [fillableFields, setFillableFields] = useState<FillableFieldSummary[]>(() => normalizeFillableFields(doc));
  const availableNodeIds = useMemo(() => new Set(availableTextNodes.map((node) => node.id)), [availableTextNodes]);
  const selectedFields = fillableFields.filter((field) => availableNodeIds.has(field.nodeId));

  function toggleField(nodeId: string, text: string) {
    setFillableFields((current) => current.some((field) => field.nodeId === nodeId)
      ? current.filter((field) => field.nodeId !== nodeId)
      : [...current, {
          nodeId,
          kind: "text",
          key: nextFieldKey(new Set(current.map((field) => field.key?.trim() ?? "")), current.length + 1),
          label: text.slice(0, 30),
          semanticRole: "title",
          constraints: { required: true, maxChars: Math.max(4, Math.min(120, text.length || 20)) },
        }]);
  }

  function updateField(nodeId: string, patch: Partial<FillableFieldSummary>) {
    setFillableFields((current) => current.map((field) => field.nodeId === nodeId ? { ...field, ...patch } : field));
  }

  function updateSemanticRole(nodeId: string, semanticRole: FillableFieldSummary["semanticRole"]) {
    setFillableFields((current) => current.map((field) => field.nodeId === nodeId
      ? {
          ...field,
          semanticRole,
          constraints: { ...field.constraints, required: semanticRole === "label" ? false : field.constraints?.required },
        }
      : field));
  }

  async function save() {
    const file = useEditor.getState().doc;
    const preparedFields = normalizeFillableFields(file, selectedFields);
    if (!workspaceId || !title.trim()) {
      if (!workspaceId) toast.error("工作区信息尚未加载完成，请关闭弹窗后重新打开。");
      else if (!title.trim()) toast.error("请填写模板名称。");
      return;
    }
    setFillableFields(preparedFields);
    setBusy(true);
    try {
      let selectedCollectionId = collectionId;
      if (!selectedCollectionId && category.trim()) {
        const collection = await oc.createTemplateCollection(workspaceId, category.trim());
        selectedCollectionId = collection.id;
      }
      if (!selectedCollectionId) {
        toast.error("请选择模板分类，或先新建一个分类。");
        return;
      }
      await oc.saveAsTemplate({
        workspaceId,
        // designId gives the server a stable template identity; file keeps the
        // template snapshot in sync with the editor even if autosave is still
        // completing.
        designId: designId ?? undefined,
        file,
        title: title.trim(),
        category: category.trim() || undefined,
        tags: zoneTag ? [zoneTag] : undefined,
        visibility,
        collectionId: selectedCollectionId,
        fillableFields: preparedFields.length > 0 ? preparedFields : undefined,
        thumbnail: createDesignThumbnail(file),
      });
      useEditor.getState().setDocMeta({ brandEditableFields: preparedFields });
      await onSaved?.(preparedFields);
      toast.success(tr("editor.saved_as_template"));
      onClose();
    } catch {
      toast.error(tr("editor.could_not_save_template"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="设置模板字段并保存">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
        className="flex flex-col gap-4"
      >
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-neutral-700">{tr("editor.name")}</span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={tr("editor.template_name")}
            className="h-11 rounded-xl border border-neutral-200 bg-surface px-3.5 text-sm text-neutral-900 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-neutral-700">标签（可选）</span>
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder={tr("editor.e_g_social_poster_resume")}
            className="h-11 rounded-xl border border-neutral-200 bg-surface px-3.5 text-sm text-neutral-900 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
        </label>
        <TemplateCollectionPicker workspaceId={workspaceId} value={collectionId} onChange={(id) => setCollectionId(id)} />
        <fieldset className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-neutral-700">{tr("editor.who_can_use_it")}</span>
          <div className="flex flex-col gap-1.5">
            {visibilities().map((v) => (
              <label
                key={v.value}
                className={`flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2 text-sm ${
                  visibility === v.value ? "border-brand-500 bg-brand-50" : "border-neutral-200"
                }`}
              >
                <input
                  type="radio"
                  name="visibility"
                  checked={visibility === v.value}
                  onChange={() => setVisibility(v.value)}
                  className="accent-brand-600"
                />
                <span className="font-medium text-neutral-800">{v.label}</span>
                <span className="ms-auto text-xs text-neutral-400">{v.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="flex max-h-72 flex-col gap-2 overflow-auto rounded-xl border border-neutral-200 p-3">
          <span className="text-sm font-medium text-neutral-700">可填充文字字段</span>
          <span className="text-xs text-neutral-500">勾选内容生成完成后需要自动替换的文字，并声明它的含义。</span>
          {availableTextNodes.map((node) => {
            const field = fillableFields.find((item) => item.nodeId === node.id);
            return (
              <div key={node.id} className="rounded-lg border border-neutral-100 p-2">
                <label className="flex items-center gap-2 text-sm text-neutral-700">
                  <input type="checkbox" checked={Boolean(field)} onChange={() => toggleField(node.id, node.text)} />
                  <span className="truncate">{node.text}</span>
                </label>
                {field && (
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <input value={field.label} onChange={(e) => updateField(node.id, { label: e.target.value })} placeholder="字段名称" className="rounded-lg border border-neutral-200 px-2 py-1.5 text-xs" />
                    <input value={field.key ?? ""} onChange={(e) => updateField(node.id, { key: e.target.value })} placeholder="字段编码" className="rounded-lg border border-neutral-200 px-2 py-1.5 text-xs" />
                    <select value={field.semanticRole ?? "title"} onChange={(e) => updateSemanticRole(node.id, e.target.value as FillableFieldSummary["semanticRole"])} className="rounded-lg border border-neutral-200 px-2 py-1.5 text-xs">
                      {semanticRoles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                    <input type="number" min={1} max={500} value={field.constraints?.maxChars ?? 20} onChange={(e) => updateField(node.id, { constraints: { ...field.constraints, maxChars: Number(e.target.value) } })} aria-label="最大字数" className="rounded-lg border border-neutral-200 px-2 py-1.5 text-xs" />
                  </div>
                )}
              </div>
            );
          })}
        </fieldset>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            {tr("editor.cancel")}
          </Button>
          <Button type="submit" size="sm" disabled={busy}>
            {busy ? tr("editor.saving") : tr("editor.save_template")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
