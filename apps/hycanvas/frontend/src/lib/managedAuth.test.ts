import { describe, expect, it } from "vitest";
import { normalizeManagedLoopbackURL, resolveContentSwarmOrigin } from "./managedAuth";

describe("normalizeManagedLoopbackURL", () => {
  it("does not add a trailing slash to a configured origin", () => {
    expect(normalizeManagedLoopbackURL("http://127.0.0.1:5173", "localhost")).toBe(
      "http://localhost:5173",
    );
  });

  it("uses the current loopback hostname without changing the configured port", () => {
    expect(normalizeManagedLoopbackURL("http://127.0.0.1:5173/hycanvas", "localhost")).toBe(
      "http://localhost:5173/hycanvas",
    );
  });

  it("does not rewrite non-loopback hosts", () => {
    expect(normalizeManagedLoopbackURL("https://content.example/hycanvas", "localhost")).toBe(
      "https://content.example/hycanvas",
    );
  });
});

describe("resolveContentSwarmOrigin", () => {
  it("uses the real iframe parent instead of a different configured host", () => {
    expect(resolveContentSwarmOrigin(
      "https://bydf.openswarm.run/boyun",
      "http://47.111.188.85:18081/boyun/hycanvas",
      "47.111.188.85",
    )).toBe("http://47.111.188.85:18081");
  });

  it("falls back to the configured URL outside an iframe", () => {
    expect(resolveContentSwarmOrigin("https://bydf.openswarm.run/boyun/", "", "hycanvas.example"))
      .toBe("https://bydf.openswarm.run/boyun");
  });
});
