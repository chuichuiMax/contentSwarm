// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({
  uploadAsset: vi.fn(),
  saveAsTemplate: vi.fn(),
}));

vi.mock("@/lib/sdk", () => ({ oc: sdk }));

import { uploadImageTemplates } from "./featuredCovers";

class LoadedImage {
  naturalWidth = 1260;
  naturalHeight = 2720;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;

  set src(_value: string) {
    queueMicrotask(() => this.onload?.());
  }
}

class LoadedFileReader {
  result: string | ArrayBuffer | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;

  readAsDataURL() {
    this.result = "data:image/png;base64,aW1hZ2U=";
    queueMicrotask(() => this.onload?.());
  }
}

describe("image template upload", () => {
  beforeEach(() => {
    sdk.uploadAsset.mockReset().mockResolvedValue({
      id: "asset-1",
      url: "/api/assets/asset-1",
      mimeType: "image/png",
    });
    sdk.saveAsTemplate.mockReset().mockResolvedValue({ id: "template-1" });
    vi.stubGlobal("Image", LoadedImage);
    vi.stubGlobal("FileReader", LoadedFileReader);
    URL.createObjectURL = vi.fn(() => "blob:example");
    URL.revokeObjectURL = vi.fn();
  });

  it("saves an uploaded image in the selected workspace collection and zone", async () => {
    const result = await uploadImageTemplates(
      "workspace-1",
      [new File(["image"], "手写报价例图.png", { type: "image/png" })],
      {
        collectionId: "collection-handwritten",
        category: "小红书",
        tags: ["小红书"],
        visibility: "workspace",
      },
    );

    expect(result).toEqual({ uploaded: 1, failed: 0 });
    expect(sdk.uploadAsset).toHaveBeenCalledWith("workspace-1", {
      filename: "手写报价例图.png",
      dataBase64: "aW1hZ2U=",
    });
    expect(sdk.saveAsTemplate).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        title: "手写报价例图",
        collectionId: "collection-handwritten",
        category: "小红书",
        tags: ["小红书"],
        visibility: "workspace",
      }),
    );
    const request = sdk.saveAsTemplate.mock.calls[0][0];
    expect(request.file.pages[0].children[0]).toMatchObject({
      type: "image",
      fit: "cover",
      size: { width: 1080, height: 1440 },
      source: { assetId: "asset-1", naturalWidth: 1260, naturalHeight: 2720 },
    });
  });
});
