export const TRAFFIC_COLUMN_DEFAULTS = [
  { key: 'index', title: '序号', visible: true, width: 'auto', sortable: false, fixed: false },
  { key: 'name', title: '姓名', visible: true, width: 'auto', sortable: false, fixed: false },
  { key: 'login_account', title: '手机号码', visible: true, width: 'auto', sortable: false, fixed: false },
  { key: 'employee_code', title: '员工编码', visible: true, width: 'auto', sortable: false, fixed: false },
  { key: 'decoration_count', title: '装修家居', visible: true, width: 'auto', sortable: false, fixed: false },
  { key: 'review_notes_count', title: '好评笔记', visible: true, width: 'auto', sortable: false, fixed: false },
  { key: 'personal_generated_count', title: '生图（个人素材）', visible: true, width: 'auto', sortable: false, fixed: false },
  { key: 'enterprise_generated_count', title: '生图（企业共享）', visible: true, width: 'auto', sortable: false, fixed: false },
  { key: 'case_share_count', title: '案例分享', visible: true, width: 'auto', sortable: false, fixed: false },
  { key: 'current_branch', title: '所属门店', visible: true, width: 'auto', sortable: false, fixed: false },
  { key: 'current_department', title: '所属部门', visible: true, width: 'auto', sortable: false, fixed: false }
]

export const cloneColumnSettings = (columns) => columns.map((item) => ({ ...item }))

const columnWidth = (value) => {
  if (value === 'auto' || value == null || String(value).trim() === '') return 'auto'
  const width = Number(value)
  if (!Number.isInteger(width) || width < 48 || width > 800) return 'auto'
  return String(width)
}

export const normalizeColumnSettings = (saved) => {
  const defaults = cloneColumnSettings(TRAFFIC_COLUMN_DEFAULTS)
  const known = new Map(defaults.map((item) => [item.key, item]))
  const used = new Set()
  const ordered = []
  for (const item of Array.isArray(saved) ? saved : []) {
    const base = known.get(item?.key)
    if (!base || used.has(base.key)) continue
    used.add(base.key)
    ordered.push({
      ...base,
      visible: item.visible !== false,
      width: columnWidth(item.width),
      sortable: Boolean(item.sortable),
      fixed: Boolean(item.fixed)
    })
  }
  for (const item of defaults) {
    if (!used.has(item.key)) ordered.push(item)
  }
  return ordered
}

export const moveColumnSetting = (columns, fromIndex, toIndex) => {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) return columns
  if (fromIndex >= columns.length || toIndex >= columns.length) return columns
  const next = cloneColumnSettings(columns)
  const [moved] = next.splice(fromIndex, 1)
  next.splice(toIndex, 0, moved)
  return next
}
