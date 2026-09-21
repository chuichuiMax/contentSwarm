import { describe, expect, it } from "vitest";
import type { Node } from "@hc/schema";

import { buildBeforeAfterCover, buildCoverLayout, coverLayouts } from "./coverElements";
import { buildXhsEditorialCover, xhsEditorialDefault } from "./xhsEditorialCover";

const excludedElementIds = new Set(["city", "layout", "trade", "service", "price", "area", "number"]);

function flattenNodes(nodes: Node[]): Node[] {
  return nodes.flatMap((node) => [node, ...(node.type === "group" ? flattenNodes(node.children) : [])]);
}

describe("system cover layout backgrounds", () => {
  it("keeps every standard cover layout transparent", () => {
    for (const layout of coverLayouts) {
      expect(buildCoverLayout(layout.id).pages[0].background).toBeUndefined();
    }
  });

  it("keeps the before-and-after cover transparent behind its editable content", () => {
    expect(buildBeforeAfterCover().pages[0].background).toBeUndefined();
  });

  it("keeps the editorial cover transparent", () => {
    const result = buildXhsEditorialCover(
      xhsEditorialDefault,
      (text, size) => text.length * size,
    );
    expect(result.success).toBe(true);
    if (result.success) expect(result.file.pages[0].background).toBeUndefined();
  });

  it("omits numeric and colored tag elements from automatic cover templates", () => {
    const files = coverLayouts.map((layout) => buildCoverLayout(layout.id));
    const editorial = buildXhsEditorialCover(xhsEditorialDefault, (text, size) => text.length * size);
    expect(editorial.success).toBe(true);
    if (editorial.success) files.push(editorial.file);

    for (const file of files) {
      const nodes = flattenNodes(file.pages[0].children);
      expect(nodes.some((node) => excludedElementIds.has(node.data?.coverElementId))).toBe(false);
      expect(nodes.some((node) => ["tag", "number"].includes(node.data?.elementType))).toBe(false);
      expect(nodes.some((node) => node.name === "数字与标签分隔线")).toBe(false);
    }
  });
});
