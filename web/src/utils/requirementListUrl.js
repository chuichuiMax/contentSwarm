const REQUIREMENT_LIST_FORM = 'http://portal.hi-run.net/edc/formview/z66223'
const REQUIREMENT_LIST_KEY = '37n795'

export function requirementListUrl(userCode, mode = 'detail') {
  const code = String(userCode || '').trim()
  if (!code) return ''
  const url = new URL(REQUIREMENT_LIST_FORM)
  url.searchParams.set('usercode', code)
  url.hash = mode === 'fill' ? '/?' : `/?_key=${REQUIREMENT_LIST_KEY}`
  return url.href
}

const SHARE_TYPES = new Set(['毛坯房', '精装房', '旧房焕新'])

export function requirementListShareUrl(userCode, type, origin) {
  const code = String(userCode || '').trim()
  const kind = String(type || '').trim()
  const base = String(origin || '').replace(/\/$/, '')
  if (!code || !SHARE_TYPES.has(kind) || !base) return ''
  const url = new URL('/requirement-list/fill', base)
  url.searchParams.set('type', kind)
  url.searchParams.set('usercode', code)
  return url.href
}
