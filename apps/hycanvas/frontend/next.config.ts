import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  deploymentId: process.env.HYCANVAS_DEPLOYMENT_ID,
  reactStrictMode: true,
  output: "export",
  trailingSlash: true,
  // ContentSwarm embeds the dev frontend from :5173. Allow its localhost
  // origins to keep the HMR connection alive instead of falling back to full
  // iframe reloads, which would discard open modal state and unsaved form data.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // yjs must load as ONE module instance (two copies break instanceof checks
  // inside the CRDT bridge: yjs issue #438). @hc/realtime is built as ESM so
  // every consumer resolves the same yjs.mjs; no resolve alias needed (a
  // previous yjs -> yjs/src alias hung Turbopack's dev compile of any chunk
  // importing yjs, wedging the dashboard on its loading screen).
  // Transpile the workspace packages so Next bundles them cleanly.
  transpilePackages: [
    "@hc/schema",
    "@hc/engine",
    "@hc/editor",
    "@hc/sdk",
    "@hc/authz",
    "@hc/export",
    "@hc/commandmenu",
  ],
  images: {
    unoptimized: true,
  },
  env: {
    NEXT_PUBLIC_BACKEND_URL:
      process.env.BUILD_DIST === "true"
        ? "/api"
        : process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8005/api",
    NEXT_PUBLIC_HYCANVAS_AUTH_MODE:
      process.env.HYCANVAS_AUTH_MODE || process.env.NEXT_PUBLIC_HYCANVAS_AUTH_MODE || "standalone",
    NEXT_PUBLIC_CONTENTSWARM_URL:
      process.env.CONTENTSWARM_URL || process.env.NEXT_PUBLIC_CONTENTSWARM_URL || "",
  },
  // Pretty editor URLs (/editor/<id>) are a SERVER rewrite to the exported
  // editor page: the Go static server does it in production, and this mirrors
  // it for `next dev`. Dev-only because rewrites are incompatible with (and
  // unnecessary for) the static export build.
  ...(process.env.NODE_ENV === "development"
    ? {
        async rewrites() {
          return [
            // ContentFlow opens the managed-auth redemption URL on the public
            // frontend origin. Proxy that one same-origin /api path to the Go
            // dev server; normal SDK traffic still uses NEXT_PUBLIC_BACKEND_URL.
            { source: "/api/:path*", destination: `${process.env.HYCANVAS_DEV_BACKEND_URL || "http://127.0.0.1:8005"}/api/:path*` },
            { source: "/editor/:id", destination: "/editor" },
            { source: "/shared/:token", destination: "/shared" },
            { source: "/present/:id", destination: "/present" },
          ];
        },
      }
    : {}),
};

export default nextConfig;
