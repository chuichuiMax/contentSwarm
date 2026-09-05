// Poll bind-mounted sources so macOS/Windows Docker development does not depend
// on host filesystem events. Shared packages compile to dist/ for Next.js;
// template specs compile to the Go-embedded seed watched by air.

import { spawn } from "node:child_process";
import { readdir, stat } from "node:fs/promises";
import { extname, join } from "node:path";

const pollMs = Number(process.env.HYCANVAS_DEV_WATCH_INTERVAL_MS || 1000);
const groups = [
  {
    name: "shared packages",
    root: "packages",
    accepts: (path) => path.includes("/src/") && [".ts", ".tsx", ".js", ".mjs"].includes(extname(path)),
    command: ["npm", ["run", "build:packages"]],
  },
  {
    name: "template catalog",
    root: "scripts/templates",
    accepts: (path) => extname(path) === ".json",
    command: ["node", ["scripts/build-templates.mjs"]],
  },
];

async function fingerprint(root, accepts) {
  const rows = [];
  async function walk(dir) {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      if (entry.name === "dist" || entry.name === "node_modules") continue;
      const path = join(dir, entry.name);
      if (entry.isDirectory()) await walk(path);
      else if (accepts(path)) {
        const info = await stat(path);
        rows.push(`${path}:${info.size}:${info.mtimeMs}`);
      }
    }
  }
  await walk(root);
  return rows.sort().join("\n");
}

function run(command, args) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { stdio: "inherit", env: process.env });
    child.on("exit", (code) => resolve(code ?? 1));
  });
}

for (const group of groups) {
  group.last = await fingerprint(group.root, group.accepts);
}
console.log(`[dev-watch] watching shared packages and template specs every ${pollMs}ms`);

let checking = false;
setInterval(async () => {
  if (checking) return;
  checking = true;
  try {
    for (const group of groups) {
      const current = await fingerprint(group.root, group.accepts);
      if (current === group.last) continue;
      group.last = current;
      console.log(`[dev-watch] ${group.name} changed; rebuilding`);
      const code = await run(...group.command);
      if (code !== 0) console.error(`[dev-watch] ${group.name} rebuild failed with exit code ${code}`);
    }
  } catch (error) {
    console.error("[dev-watch] scan failed", error);
  } finally {
    checking = false;
  }
}, pollMs);
