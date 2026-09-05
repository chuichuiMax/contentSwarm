export type TemplateZone = "xiaohongshu";

const zoneTags: Record<TemplateZone, string> = {
  xiaohongshu: "小红书",
};

export function templateZoneForFormat(format: { templateZone?: TemplateZone }): TemplateZone | null {
  return format.templateZone ?? null;
}

export function templateZoneFromMeta(meta: Record<string, unknown>): TemplateZone | null {
  return meta.templateZone === "xiaohongshu" ? "xiaohongshu" : null;
}

export function templateTagForZone(zone: TemplateZone | null): string | null {
  return zone ? zoneTags[zone] : null;
}

export function isTemplateInZone(
  template: { tags: string[] },
  zone: TemplateZone,
): boolean {
  return template.tags.includes(zoneTags[zone]);
}

export function isDesignInZone(
  design: { templateZone?: TemplateZone | null },
  zone: TemplateZone,
): boolean {
  return design.templateZone === zone;
}
