// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  saveAsTemplate: vi.fn(),
  createTemplateCollection: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/lib/sdk", () => ({ oc: {
  saveAsTemplate: mocks.saveAsTemplate,
  createTemplateCollection: mocks.createTemplateCollection,
} }));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: mocks.success, error: mocks.error }),
}));
vi.mock("@/store/editor", () => {
  const doc = {
    title: "我的小红书模板",
    meta: {
      templateZone: "xiaohongshu",
      brandEditableFields: [{
        nodeId: "old-title", kind: "text", key: "field_1", label: "",
        typography: { runs: [{ fontFamily: "Noto Sans SC", fontStyle: "Regular", fontWeight: 800, fontSize: 72 }] },
      }, {
        nodeId: "old-subtitle", kind: "text", key: "field_1", label: "副标题",
        typography: { runs: [{ fontFamily: "Noto Sans SC", fontStyle: "Regular", fontWeight: 500, fontSize: 36 }] },
      }],
    },
    pages: [{ children: [
      {
        id: "title-1", type: "text", name: "主标题",
        content: [{ runs: [{ text: "主标题", style: { fontSize: 72 } }] }],
      },
      {
        id: "subtitle-1", type: "text", name: "副标题",
        content: [{ runs: [{ text: "副标题文案", style: { fontSize: 36 } }] }],
      },
    ] }],
  };
  const useEditor = Object.assign(
    (selector: (state: { doc: typeof doc }) => unknown) => selector({ doc }),
    { getState: () => ({ doc, setDocMeta: vi.fn() }) },
  );
  return { useEditor };
});
vi.mock("@/components/ui/Modal", () => ({
  Modal: ({ open, title, children }: { open: boolean; title: string; children: React.ReactNode }) =>
    open ? <div role="dialog" aria-label={title}>{children}</div> : null,
}));
vi.mock("@/components/ui/Button", () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string; size?: string }) =>
    <button type={props.type} disabled={props.disabled} onClick={props.onClick}>{children}</button>,
}));
vi.mock("@/components/dashboard/TemplateCollectionPicker", () => ({
  TemplateCollectionPicker: ({ onChange }: { onChange: (id: string) => void }) =>
    <button type="button" onClick={() => onChange("collection-1")}>选择分类</button>,
}));
vi.mock("@/lib/designThumbnail", () => ({ createDesignThumbnail: () => "thumbnail" }));
vi.mock("@/lib/i18n", () => ({
  tr: (key: string) => ({
    "editor.save_as_template": "保存为模板",
    "editor.save_template": "保存模板",
  }[key] ?? key),
}));

const { SaveAsTemplateDialog } = await import("./SaveAsTemplateDialog");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Xiaohongshu template drafts", () => {
  it("keeps their zone tag when they are saved as reusable templates", async () => {
    mocks.saveAsTemplate.mockResolvedValue({});
    mocks.createTemplateCollection.mockResolvedValue({ id: "collection-1" });

    render(
      <SaveAsTemplateDialog
        open
        onClose={() => {}}
        designId="design-1"
        workspaceId="workspace-1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "选择分类" }));
    fireEvent.click(screen.getByRole("button", { name: "保存模板" }));

    await waitFor(() => expect(mocks.saveAsTemplate).toHaveBeenCalledWith(expect.objectContaining({
      file: expect.objectContaining({ title: "我的小红书模板" }),
      designId: "design-1",
      workspaceId: "workspace-1",
      category: "小红书",
      tags: ["小红书"],
      fillableFields: [
        expect.objectContaining({
          nodeId: "title-1",
          key: "field_1",
          label: "主标题",
          semanticRole: "title",
          constraints: expect.objectContaining({ required: true, maxChars: 4 }),
        }),
        expect.objectContaining({
          nodeId: "subtitle-1",
          key: "field_2",
          label: "副标题",
          semanticRole: "title",
          constraints: expect.objectContaining({ required: true, maxChars: 5 }),
        }),
      ],
    })));
    expect(mocks.error).not.toHaveBeenCalledWith("请完整填写已选择的文字字段名称、唯一编码、语义和最大字数。");
  });
});
