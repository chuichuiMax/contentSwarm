/** 子路径部署时，为 /api 请求补上 BASE_URL 前缀 */
export function apiUrl(path) {
  if (!path.startsWith('/')) return path
  const base = import.meta.env.BASE_URL || '/'
  if (base === '/') return path
  const prefix = base.endsWith('/') ? base.slice(0, -1) : base
  return `${prefix}${path}`
}
