import { describe, expect, it } from "vitest";

import { buildPersonalizedCoverLayout, personalizedCoverLayouts } from "./personalizedCoverLayouts";

describe("personalized cover layouts", () => {
  it("provides five placements for each source template plus three curated compositions", () => {
    expect(personalizedCoverLayouts).toHaveLength(18);
    for (const family of ["template-1", "template-3", "template-4"] as const) {
      expect(personalizedCoverLayouts.filter((layout) => layout.family === family)).toHaveLength(6);
    }
  });

  it("keeps every layout transparent and inside the cover canvas", () => {
    for (const layout of personalizedCoverLayouts) {
      const page = buildPersonalizedCoverLayout(layout).pages[0];
      expect(page.background).toBeUndefined();
      for (const node of page.children) {
        expect(node.transform.x).toBeGreaterThanOrEqual(0);
        expect(node.transform.y).toBeGreaterThanOrEqual(0);
        expect(node.transform.x + node.size.width).toBeLessThanOrEqual(page.width);
        expect(node.transform.y + node.size.height).toBeLessThanOrEqual(page.height);
      }
    }
  });

  it("omits numeric fields from every personalized automatic layout", () => {
    for (const layout of personalizedCoverLayouts) {
      const nodes = buildPersonalizedCoverLayout(layout).pages[0].children;
      expect(nodes.some((node) => node.data?.binding === "project_area")).toBe(false);
      expect(nodes.some((node) => node.data?.elementType === "number")).toBe(false);
    }
  });

  it("moves the template 3 content block while keeping its footer fixed", () => {
    const top = personalizedCoverLayouts.find((layout) => layout.id === "template-3-top-left")!;
    const bottom = personalizedCoverLayouts.find((layout) => layout.id === "template-3-bottom-right")!;
    const topPage = buildPersonalizedCoverLayout(top).pages[0];
    const bottomPage = buildPersonalizedCoverLayout(bottom).pages[0];
    const topFooter = topPage.children.find((node) => node.data?.binding === "footnote")!;
    const bottomFooter = bottomPage.children.find((node) => node.data?.binding === "footnote")!;
    expect(bottomFooter.transform).toEqual(topFooter.transform);
    expect(bottomPage.children.find((node) => node.data?.binding === "title")!.transform.x)
      .toBeGreaterThan(topPage.children.find((node) => node.data?.binding === "title")!.transform.x);
  });
});
