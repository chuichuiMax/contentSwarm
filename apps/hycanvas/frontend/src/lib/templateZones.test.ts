import { describe, expect, it } from "vitest";

import {
  isDesignInZone,
  isTemplateInZone,
  templateTagForZone,
  templateZoneForFormat,
  templateZoneFromMeta,
} from "./templateZones";

describe("template zone metadata", () => {
  it("identifies the Xiaohongshu create-menu entry", () => {
    expect(templateZoneForFormat({ templateZone: "xiaohongshu" })).toBe("xiaohongshu");
  });

  it("keeps regular canvas formats on the design creation path", () => {
    expect(templateZoneForFormat({})).toBeNull();
  });

  it("recovers the zone and catalog tag from a saved design", () => {
    const zone = templateZoneFromMeta({ templateZone: "xiaohongshu" });
    expect(zone).toBe("xiaohongshu");
    expect(templateTagForZone(zone)).toBe("小红书");
  });
});

describe("template zone membership", () => {
  it("includes design drafts carrying the Xiaohongshu zone marker", () => {
    expect(isDesignInZone({ templateZone: "xiaohongshu" }, "xiaohongshu")).toBe(true);
    expect(isDesignInZone({}, "xiaohongshu")).toBe(false);
  });

  it("includes a user-created UUID template carrying the Xiaohongshu tag", () => {
    expect(isTemplateInZone({ tags: ["小红书", "干货"] }, "xiaohongshu")).toBe(true);
  });

  it("does not classify a template by its id or title alone", () => {
    expect(isTemplateInZone({ tags: ["Social"] }, "xiaohongshu")).toBe(false);
  });
});
