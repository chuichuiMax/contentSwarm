export const isContentSwarmManaged = true;

function isLoopbackHostname(hostname: string): boolean {
  return hostname === "localhost" || hostname === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(hostname);
}

export function normalizeManagedLoopbackURL(rawURL: string, currentHostname: string): string {
  const target = new URL(rawURL);
  if (isLoopbackHostname(target.hostname) && isLoopbackHostname(currentHostname)) {
    target.hostname = currentHostname;
  }
  const normalized = target.toString();
  return rawURL.endsWith("/") ? normalized : normalized.replace(/\/$/, "");
}

export function resolveContentSwarmOrigin(configuredURL: string, referrer: string, currentHostname: string): string | null {
  // In an iframe the referrer is the actual parent page. It must take
  // precedence over the deployment's canonical URL because ContentSwarm can
  // also be reached through an IP address or another reverse-proxy hostname;
  // postMessage silently drops a message whose targetOrigin is not the real
  // parent origin.
  if (referrer) return new URL(referrer).origin;
  const configured = configuredURL.trim().replace(/\/$/, "");
  return configured ? normalizeManagedLoopbackURL(configured, currentHostname) : null;
}

function contentSwarmOrigin(): string | null {
  const configured = process.env.NEXT_PUBLIC_CONTENTSWARM_URL ?? "";
  if (typeof window === "undefined") return configured.trim().replace(/\/$/, "") || null;
  return resolveContentSwarmOrigin(configured, document.referrer, window.location.hostname);
}

export function contentSwarmHyCanvasURL(): string | null {
  const base = contentSwarmOrigin();
  return isContentSwarmManaged && base ? `${base}/hycanvas` : null;
}

export function returnToContentSwarm(): boolean {
  const target = contentSwarmHyCanvasURL();
  if (!target || typeof window === "undefined") return false;
  window.top?.location.replace(target);
  return true;
}

export type ContentSwarmGallery = {
  id: string;
  name: string;
  parent_id?: string | null;
  count: number;
  direct_count?: number;
};

export type ContentSwarmMaterial = {
  id: string;
  name: string;
  category: string;
  category_name: string;
  width: number;
  height: number;
  content_type: string;
};

type MaterialBridgeAction = "list-galleries" | "list-items" | "get-file" | "ensure-gallery" | "upload-image";

export function requestContentSwarmMaterials<T>(action: MaterialBridgeAction, payload: Record<string, unknown> = {}): Promise<T> {
  const parentOrigin = contentSwarmOrigin();
  if (!isContentSwarmManaged || !parentOrigin || typeof window === "undefined" || window.parent === window) {
    return Promise.reject(new Error("ContentSwarm 素材桥接不可用"));
  }
  const requestId = crypto.randomUUID();
  return new Promise<T>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      window.removeEventListener("message", onMessage);
      reject(new Error("ContentSwarm 素材库响应超时"));
    }, 20000);
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== parentOrigin || event.source !== window.parent) return;
      if (event.data?.type !== "contentswarm:materials:response" || event.data.requestId !== requestId) return;
      window.clearTimeout(timeout);
      window.removeEventListener("message", onMessage);
      if (event.data.ok) resolve(event.data.data as T);
      else reject(new Error(event.data.error || "ContentSwarm 素材库读取失败"));
    };
    window.addEventListener("message", onMessage);
    window.parent.postMessage({ type: "hycanvas:materials:request", requestId, action, payload }, parentOrigin);
  });
}
