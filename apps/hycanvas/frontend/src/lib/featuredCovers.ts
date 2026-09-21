// Batch upload for the featured-cover zone: each image becomes one public
// 1080x1440 template whose single image node fills the canvas with
// fit: "cover" (center-crop, the schema's native behavior), so non-3:4
// sources stay full-bleed without distortion.

import { createBlankDesign, createNode, type DesignFile, type ImageNode } from "@hc/schema";
import { oc } from "@/lib/sdk";

export const FEATURED_COVER_TAG = "精选封面";
export const COVER_WIDTH = 1080;
export const COVER_HEIGHT = 1440;

function readImageSize(file: Blob): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve({ width: img.naturalWidth, height: img.naturalHeight });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("unreadable image"));
    };
    img.src = url;
  });
}

function fileToBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.onerror = () => reject(new Error("unreadable file"));
    reader.readAsDataURL(file);
  });
}

/** A cover template file: one full-bleed image on a 3:4 canvas. The asset id
 *  is the real workspace upload id: server-side rendering (render.png) resolves
 *  image bytes through the uploads service by that id, so anything else draws
 *  a blank canvas. */
export function buildCoverDesign(
  title: string,
  asset: { id: string; url: string; mime: string },
  naturalWidth: number,
  naturalHeight: number,
): DesignFile {
  const file = createBlankDesign({ title, width: COVER_WIDTH, height: COVER_HEIGHT });
  file.assets = [{ id: asset.id, kind: "image", url: asset.url, mime: asset.mime, width: naturalWidth, height: naturalHeight }];
  const image = createNode("image", {
    source: { assetId: asset.id, naturalWidth, naturalHeight },
    fit: "cover",
  }) as ImageNode;
  image.size = { width: COVER_WIDTH, height: COVER_HEIGHT };
  image.transform = { x: 0, y: 0, scaleX: 1, scaleY: 1, rotation: 0 };
  file.pages[0].children = [image];
  return file;
}

/** Upload each image as an asset, wrap it in a cover design, and save it as a
 *  public featured-cover template. Sequential on purpose: covers arrive in a
 *  handful at a time and a serial loop keeps asset-quota failures ordered.
 *  Returns the number of files that failed. */
export async function uploadFeaturedCovers(
  workspaceId: string,
  files: File[],
  onProgress?: (done: number, total: number) => void,
): Promise<{ uploaded: number; failed: number }> {
  let uploaded = 0;
  let failed = 0;
  for (const file of files) {
    try {
      const [{ width, height }, dataBase64] = await Promise.all([readImageSize(file), fileToBase64(file)]);
      const asset = await oc.uploadAsset(workspaceId, { filename: file.name, dataBase64 });
      const title = file.name.replace(/\.[^.]+$/, "") || "Cover";
      const design = buildCoverDesign(title, { id: asset.id, url: asset.url, mime: asset.mimeType ?? file.type }, width, height);
      await oc.saveAsTemplate({
        workspaceId,
        file: design,
        title,
        category: FEATURED_COVER_TAG,
        tags: [FEATURED_COVER_TAG],
        visibility: "public",
      });
      uploaded += 1;
    } catch {
      failed += 1;
    }
    onProgress?.(uploaded + failed, files.length);
  }
  return { uploaded, failed };
}
