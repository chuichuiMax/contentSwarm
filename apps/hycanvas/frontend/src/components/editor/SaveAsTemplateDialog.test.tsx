// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  saveAsTemplate: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/lib/sdk", () => ({ oc: { saveAsTemplate: mocks.saveAsTemplate } }));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: mocks.success, error: mocks.error }),
}));
vi.mock("@/store/editor", () => {
  const doc = {
    title: "我的小红书模板",
    meta: {
      templateZone: "xiaohongshu",
      brandEditableFields: [{
        nodeId: "title-1", kind: "text", key: "field_1", label: "主标题",
        semanticRole: "title", constraints: { required: true, maxChars: 20 },
      }],
    },
    pages: [{ children: [{
      id: "title-1", type: "text", name: "主标题",
      content: [{ runs: [{ text: "主标题" }] }],
    }] }],
  };
  const useEditor = Object.assign(
    (selector: (state: { doc: typeof doc }) => unknown) => selector({ doc }),
    { getState: () => ({ doc }) },
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

    render(
      <SaveAsTemplateDialog
        open
        onClose={() => {}}
        designId="design-1"
        workspaceId="workspace-1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "保存模板" }));

    await waitFor(() => expect(mocks.saveAsTemplate).toHaveBeenCalledWith(expect.objectContaining({
      file: expect.objectContaining({ title: "我的小红书模板" }),
      workspaceId: "workspace-1",
      category: "小红书",
      tags: ["小红书"],
    })));
    expect(mocks.saveAsTemplate.mock.calls[0][0]).not.toHaveProperty("designId");
  });
});
