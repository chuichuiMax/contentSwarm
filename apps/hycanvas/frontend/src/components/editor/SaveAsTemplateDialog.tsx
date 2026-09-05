// Save-as-template dialog. Captures a title, category, and
// visibility, then saves the current design as a reusable template via the SDK.
// Uses the design's id when available (server loads its latest snapshot) and
// otherwise sends the in-memory file so unsaved work can still be templatized.

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

function textNodes(file: ReturnType<typeof useEditor.getState>["doc"]): Array<{ id: string; text: string }> {
  const result: Array<{ id: string; text: string }> = [];
  const visit = (node: Node) => {
    if (node.type === "text") {
      const content = (node as unknown as { content?: { runs?: { text?: string }[] }[] }).content ?? [];
      const text = content.map((p) => (p.runs ?? []).map((r) => r.text ?? "").join("")).join(" ").trim();
      result.push({ id: node.id, text: text || node.name || "未命名文字" });
    }
    for (const child of childrenOf(node)) visit(child);
  };
  for (const page of file.pages) for (const node of page.children) visit(node);
  return result;
}

export function SaveAsTemplateDialog({
  open,
  onClose,
  onSaved,
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
  const [visibility, setVisibility] = useState<TemplateVisibility>("workspace");
  const [busy, setBusy] = useState(false);
  const doc = useEditor((s) => s.doc);
  const availableTextNodes = useMemo(() => textNodes(doc), [doc]);
  const [fillableFields, setFillableFields] = useState<FillableFieldSummary[]>(() =>
    (doc.meta as { brandEditableFields?: FillableFieldSummary[] } | undefined)?.brandEditableFields ?? [],
  );
  const fieldsValid = fillableFields.length > 0 && fillableFields.every((field) =>
    Boolean(field.label.trim() && field.key?.trim() && field.semanticRole && (field.constraints?.maxChars ?? 0) > 0),
  ) && new Set(fillableFields.map((field) => field.key)).size === fillableFields.length;

  function toggleField(nodeId: string, text: string) {
    setFillableFields((current) => current.some((field) => field.nodeId === nodeId)
      ? current.filter((field) => field.nodeId !== nodeId)
      : [...current, {
          nodeId,
          kind: "text",
          key: `field_${current.length + 1}`,
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
    if (!workspaceId || !title.trim() || !fieldsValid) {
      if (!fieldsValid) toast.error("请至少选择一个文字字段，并完整填写字段名称、唯一编码、语义和最大字数。");
      return;
    }
    setBusy(true);
    try {
      const file = useEditor.getState().doc;
      await oc.saveAsTemplate({
        workspaceId,
        // Loading by designId can race autosave and capture an older style.
        file,
        title: title.trim(),
        category: category.trim() || undefined,
        tags: zoneTag ? [zoneTag] : undefined,
        visibility,
        fillableFields,
        thumbnail: createDesignThumbnail(file),
      });
      useEditor.getState().setDocMeta({ brandEditableFields: fillableFields });
      await onSaved?.(fillableFields);
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
          <span className="text-sm font-medium text-neutral-700">{tr("editor.category_optional")}</span>
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder={tr("editor.e_g_social_poster_resume")}
            className="h-11 rounded-xl border border-neutral-200 bg-surface px-3.5 text-sm text-neutral-900 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
        </label>
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
          <Button type="submit" size="sm" disabled={busy || !title.trim() || !workspaceId || !fieldsValid}>
            {busy ? tr("editor.saving") : tr("editor.save_template")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
