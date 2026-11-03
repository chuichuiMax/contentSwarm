// Fonts shipped with the project and registered by the server renderer.
// Template creation can only offer these families. The browser catalog has
// thousands of other faces, and the renderer substitutes them.

export const PACKAGED_FONTS = [
  { family: "Noto Sans SC", labelKey: "editor.packaged_font_sans" },
  { family: "Noto Serif SC", labelKey: "editor.packaged_font_serif" },
] as const;

export function isPackagedFont(family: string | undefined): boolean {
  const name = (family ?? "").trim().toLowerCase();
  return PACKAGED_FONTS.some((font) => font.family.toLowerCase() === name);
}
