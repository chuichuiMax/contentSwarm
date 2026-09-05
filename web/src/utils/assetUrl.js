/** 将 MinIO 绝对 URL 转为 /public/... 相对路径 */
function normalizeMediaPath(path) {
  if (!path || !/^https?:\/\//.test(path)) return path
  try {
    const { pathname } = new URL(path)
    if (pathname.startsWith('/public/')) return pathname
  } catch {
    return path
  }
  return path
}

/** 子路径部署时，为静态资源路径补上 BASE_URL 前缀 */
export function assetUrl(path) {
  if (!path) return path
  path = normalizeMediaPath(path)
  if (/^https?:\/\//.test(path)) return path
  const base = import.meta.env.BASE_URL || '/'
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  if (base === '/') return normalizedPath
  const normalizedBase = base.endsWith('/') ? base.slice(0, -1) : base
  if (normalizedPath.startsWith(`${normalizedBase}/`)) return normalizedPath
  return `${normalizedBase}${normalizedPath}`
}
