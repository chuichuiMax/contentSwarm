// Fonts installed in the cover font directory. The editor reads the list from
// the server so a newly added font file can be selected without a code change.

import { useEffect, useState } from "react";

export type PackagedFontFile = { weight: number; url: string };

export type PackagedFont = {
  family: string;
  label: string;
  weights: number[];
  files: PackagedFontFile[];
};

const FALLBACK_FONTS: PackagedFont[] = [
  { family: "Noto Sans SC", label: "黑体", weights: [400, 700], files: [] },
  { family: "Noto Serif SC", label: "宋体", weights: [400, 700], files: [] },
];

let packagedFonts = FALLBACK_FONTS;
let loading: Promise<PackagedFont[]> | null = null;

export function packagedFontList(): PackagedFont[] {
  return packagedFonts;
}

export function packagedFontByFamily(family: string | undefined): PackagedFont | undefined {
  const name = (family ?? "").trim().toLowerCase();
  return packagedFonts.find((font) => font.family.toLowerCase() === name);
}

export function isPackagedFont(family: string | undefined): boolean {
  return packagedFontByFamily(family) !== undefined;
}

export function loadPackagedFonts(): Promise<PackagedFont[]> {
  if (typeof window === "undefined") return Promise.resolve(packagedFonts);
  if (!loading) {
    const base = (process.env.NEXT_PUBLIC_BACKEND_URL || "/api").replace(/\/$/, "");
    loading = fetch(`${base}/v1/fonts`, { credentials: "include" })
      .then(async (response) => {
        if (!response.ok) return packagedFonts;
        const body = (await response.json()) as PackagedFont[];
        if (!Array.isArray(body) || body.length === 0) return packagedFonts;
        packagedFonts = body.filter((font) => font && typeof font.family === "string" && font.family.trim());
        return packagedFonts;
      })
      .catch(() => packagedFonts);
  }
  return loading;
}

export function usePackagedFonts(): PackagedFont[] {
  const [fonts, setFonts] = useState(packagedFonts);
  useEffect(() => {
    let alive = true;
    void loadPackagedFonts().then((next) => {
      if (alive) setFonts(next);
    });
    return () => {
      alive = false;
    };
  }, []);
  return fonts;
}
