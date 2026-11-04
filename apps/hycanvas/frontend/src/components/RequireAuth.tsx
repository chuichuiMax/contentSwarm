// Client-side route guard. Bootstraps the session on first mount and
// redirects to /login when anonymous. Compatible with static export: all auth
// resolution happens in the browser.

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/router";
import { useAuth } from "@/store/auth";
import { FullScreenLoader } from "@/components/ui/BrandLoader";
import { returnToContentSwarm } from "@/lib/managedAuth";

export function RequireAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const status = useAuth((s) => s.status);
  const bootstrap = useAuth((s) => s.bootstrap);

  useEffect(() => {
    if (status === "loading") void bootstrap();
  }, [status, bootstrap]);

  useEffect(() => {
    if (status !== "anon") return;
    if (returnToContentSwarm()) return;
    // Carry the current location as a return path so a deep link (e.g.
    // /dashboard/templates) survives the login round-trip. AuthForm validates
    // `next` as a same-origin relative path before honoring it.
    const path = router.asPath;
    const next = path && path !== "/" ? `?next=${encodeURIComponent(path)}` : "";
    void router.replace(`/login${next}`);
  }, [status, router]);

  if (status !== "authed") {
    if (status === "anon" && typeof window !== "undefined" && window.parent !== window) {
      return (
        <div className="grid min-h-screen place-items-center p-8 text-center">
          <p className="max-w-sm text-sm text-neutral-600">
            HyCanvas 登录态未建立。请返回 ContentSwarm 后重新打开「视觉创作」，或使用下方按钮刷新整页（不要只刷新 iframe）。
          </p>
          <button
            type="button"
            className="mt-4 rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600"
            onClick={() => window.parent.location.reload()}
          >
            刷新页面
          </button>
        </div>
      );
    }
    return <FullScreenLoader />;
  }
  return <>{children}</>;
}
