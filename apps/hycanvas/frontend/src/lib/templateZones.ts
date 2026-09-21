export type TemplateZone = "xiaohongshu" | "featured";

const zoneTags: Record<TemplateZone, string> = {
  xiaohongshu: "小红书",
  featured: "精选封面",
};

const zoneLabelKeys: Record<TemplateZone, string> = {
  xiaohongshu: "dashboard.xiaohongshu_template_zone",
  featured: "dashboard.featured_cover_zone",
};

export function templateZoneForFormat(format: { templateZone?: TemplateZone }): TemplateZone | null {
  return format.templateZone ?? null;
}

export function templateZoneFromMeta(meta: Record<string, unknown>): TemplateZone | null {
  return meta.templateZone === "xiaohongshu" || meta.templateZone === "featured"
    ? meta.templateZone
    : null;
}

export function templateZoneFromQuery(value: unknown): TemplateZone | null {
  return value === "xiaohongshu" || value === "featured" ? value : null;
}

export function templateZoneLabelKey(zone: TemplateZone): string {
  return zoneLabelKeys[zone];
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
