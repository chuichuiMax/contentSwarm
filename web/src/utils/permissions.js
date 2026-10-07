export const ROUGH_UPLOAD_PERMISSION = 'material_rough.upload'

const HOME_CANDIDATES = [
  [ROUGH_UPLOAD_PERMISSION, '/materials/images'],
  ['chat.access', '/agent'],
  ['content.generate', '/content/new'],
  ['content.copy', '/content/new'],
  ['workspace.view_list', '/workspace'],
  ['extensions.view_list', '/extensions'],
  ['agent.view_list', '/model-manage'],
  ['account.view_list', '/model-manage/accounts'],
  ['persona.view_list', '/config-manage/personas'],
  ['permission.view_list', '/config-manage/permissions'],
  ['content_type.view_list', '/config-manage/content-types'],
  ['target_audience.view_list', '/config-manage/target-audiences'],
  ['resident_population.view_list', '/config-manage/resident-populations'],
  ['variable.view_list', '/config-manage/variables'],
  ['business_variable.view_list', '/config-manage/business-variables'],
  ['process_standard.view_list', '/config-manage/process-standards']
]

const SCOPED_PATH_RULES = [
  { prefix: '/materials', any: [ROUGH_UPLOAD_PERMISSION] },
  { prefix: '/content/image-design', any: [] },
  { prefix: '/hycanvas', any: [] },
  { prefix: '/agent', any: ['chat.access'] },
  { prefix: '/content', any: ['content.generate', 'content.copy'] },
  { prefix: '/workspace', any: ['workspace.view_list'] },
  { prefix: '/knowledge', any: [] },
  { prefix: '/extensions', any: ['extensions.view_list'] },
  { prefix: '/model-manage/accounts', any: ['account.view_list', 'account.create', 'account.view', 'account.delete'] },
  { prefix: '/model-manage/employees', any: [] },
  { prefix: '/model-manage', any: ['agent.view_list', 'agent.create', 'agent.view', 'agent.delete'] },
  { prefix: '/config-manage/personas', any: ['persona.view_list', 'persona.create', 'persona.view', 'persona.delete'] },
  { prefix: '/config-manage/permissions', any: ['permission.view_list', 'permission.create', 'permission.view', 'permission.delete'] },
  { prefix: '/config-manage/content-types', any: ['content_type.view_list', 'content_type.create', 'content_type.view', 'content_type.delete'] },
  { prefix: '/config-manage/target-audiences', any: ['target_audience.view_list', 'target_audience.create', 'target_audience.view', 'target_audience.delete'] },
  { prefix: '/config-manage/resident-populations', any: ['resident_population.view_list', 'resident_population.create', 'resident_population.view', 'resident_population.delete'] },
  { prefix: '/config-manage/variables', any: ['variable.view_list', 'variable.create', 'variable.view', 'variable.delete'] },
  { prefix: '/config-manage/business-variables', any: ['business_variable.view_list', 'business_variable.create', 'business_variable.view', 'business_variable.delete'] },
  { prefix: '/config-manage/process-standards', any: ['process_standard.view_list', 'process_standard.create', 'process_standard.view', 'process_standard.delete'] },
  { prefix: '/config-manage', any: [] },
  { prefix: '/dashboard', any: [] }
]

export function hasAnyPermission(grants, keys) {
  return keys.some((key) => grants.includes(key))
}

export function homePathForGrants(permissionScoped, grants) {
  if (!permissionScoped) return '/agent'
  const match = HOME_CANDIDATES.find(([key]) => grants.includes(key))
  return match ? match[1] : '/materials/images'
}

export function canAccessScopedPath(permissionScoped, grants, path) {
  if (!permissionScoped) return true
  const rule = SCOPED_PATH_RULES.find((item) => path === item.prefix || path.startsWith(`${item.prefix}/`))
  if (!rule) return false
  return hasAnyPermission(grants, rule.any)
}
