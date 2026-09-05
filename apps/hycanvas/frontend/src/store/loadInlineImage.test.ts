import { describe, expect, it } from "vitest";
import { createBlankDesign, createNode, type ImageNode, type Node } from "@hc/schema";
import { useEditor } from "./editor";

describe("loading legacy inline images", () => {
  it("migrates nested inline image sources into reusable file assets", () => {
    const file = createBlankDesign({ width: 1080, height: 1440 });
    const dataUrl = "data:image/png;base64,cG5n";
    const background = createNode("image", {
      id: "legacy-background",
      source: { assetId: "", naturalWidth: 0, naturalHeight: 0 },
      fit: "cover",
    }) as ImageNode & { src?: string };
    background.src = dataUrl;
    const nested = createNode("image", {
      id: "legacy-nested",
      source: { assetId: "", naturalWidth: 640, naturalHeight: 480 },
      fit: "cover",
    }) as ImageNode & { src?: string };
    nested.src = dataUrl;
    file.pages[0].children = [
      background,
      createNode("group", { id: "group", children: [nested] } as Partial<Node>),
    ];

    useEditor.getState().loadDoc(file);

    const doc = useEditor.getState().doc;
    const loadedBackground = doc.pages[0].children[0] as ImageNode & { src?: string };
    const loadedNested = (doc.pages[0].children[1] as Node & { children: Node[] }).children[0] as ImageNode & { src?: string };
    expect(doc.assets).toHaveLength(1);
    expect(doc.assets[0]).toMatchObject({ kind: "image", url: dataUrl, mime: "image/png" });
    expect(loadedBackground.source.assetId).toBe(doc.assets[0].id);
    expect(loadedNested.source.assetId).toBe(doc.assets[0].id);
    expect(loadedNested.source.naturalWidth).toBe(640);
    expect(loadedBackground.src).toBeUndefined();
    expect(loadedNested.src).toBeUndefined();
  });
});
