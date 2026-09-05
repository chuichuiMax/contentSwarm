import { createScene, renderScene, type CanvasLike, type Viewport } from "@hc/engine";
import type { DesignFile } from "@hc/schema";
import { imageAssets } from "@/lib/assetProvider";

const WIDTH = 320;
const HEIGHT = 240;

/** Render the first page into a compact dashboard preview. */
export function createDesignThumbnail(file: DesignFile): string | undefined {
  const page = file.pages[0];
  if (!page || typeof document === "undefined") return undefined;
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const ctx = canvas.getContext("2d");
  if (!ctx) return undefined;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
  const zoom = Math.min(WIDTH / page.width, HEIGHT / page.height);
  const viewport: Viewport = {
    zoom,
    panX: -((WIDTH - page.width * zoom) / 2) / zoom,
    panY: -((HEIGHT - page.height * zoom) / 2) / zoom,
    dpr: 1,
    width: WIDTH,
    height: HEIGHT,
  };
  try {
    imageAssets.registerAll(file.assets ?? []);
    renderScene(createScene(file), ctx as unknown as CanvasLike, viewport, { assets: imageAssets });
    return canvas.toDataURL("image/jpeg", 0.68);
  } catch {
    return undefined;
  }
}
