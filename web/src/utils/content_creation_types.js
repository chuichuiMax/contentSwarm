export const CREATION_TYPE_NAMES = {
  CT01: '自我介绍',
  CT02: '项目单价',
  CT03: '单价+面积',
  CT04: '工种总价',
  CT05: '人工+辅材',
  CT06: '工艺展示',
  CT07: '日常工作'
}

export const CREATION_TYPE_OPTIONS = Object.entries(CREATION_TYPE_NAMES).map(([value, label]) => ({ value, label }))
