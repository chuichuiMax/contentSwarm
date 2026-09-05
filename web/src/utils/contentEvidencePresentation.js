const DEFAULT_FIELD_LABELS = {
  brand_name: '品牌名称',
  audience: '目标客户',
  pain: '客户痛点',
  advantage: '核心优势',
  project_type: '项目类型',
  area: '项目面积',
  budget: '项目预算',
  duration: '项目周期',
  craft_and_materials: '工艺与材料',
  owner_pain: '业主痛点',
  project_result: '项目成果',
  product_profile: '产品介绍',
  price: '产品价格',
  case_proof: '案例证明',
  brand: '品牌信息',
  viral_example: '爆款样例',
  product_name: '产品名称',
  standard_package: '标准套餐',
  effective_at: '生效时间',
  scope: '适用范围',
  result: '案例结果',
  source: '资料来源'
}

const SOURCE_LABELS = {
  manual_input: '人工确认事实',
  business_record: '业务资料',
  media: '素材事实',
  knowledge_base: '知识库资料',
  human_confirmation: '人工确认事实'
}

const clipText = (value, limit = 96) => {
  const characters = Array.from(String(value).replace(/\s+/g, ' ').trim())
  return characters.length > limit ? `${characters.slice(0, limit).join('')}…` : characters.join('')
}

const fieldLabel = (code, fieldLabels) => fieldLabels[code] || DEFAULT_FIELD_LABELS[code] || ''

const formatValue = (value, fieldLabels) => {
  if (value === undefined || value === null || value === '') return ''
  if (Array.isArray(value)) return clipText(value.map((item) => formatValue(item, fieldLabels)).join('、'))
  if (typeof value === 'object') {
    return clipText(
      Object.entries(value)
        .map(([key, item]) => {
          const label = fieldLabel(key, fieldLabels)
          const text = formatValue(item, fieldLabels)
          return label && text ? `${label}：${text}` : text
        })
        .filter(Boolean)
        .join('；')
    )
  }
  return clipText(value)
}

export const formatEvidenceReference = (evidence, fieldLabels = {}, fallbackIndex = 0) => {
  if (!evidence) return `已确认事实 ${fallbackIndex + 1}`

  const codes = evidence.variable_codes || (evidence.key ? [evidence.key] : [])
  const labels = codes.map((code) => fieldLabel(code, fieldLabels)).filter(Boolean)
  const materialLabel = fieldLabel(evidence.metadata?.material_type, fieldLabels)
  const label =
    [...new Set(labels)].join('、') ||
    materialLabel ||
    SOURCE_LABELS[evidence.source_type] ||
    `已确认事实 ${fallbackIndex + 1}`
  const value = formatValue(evidence.value ?? evidence.values ?? evidence.content, fieldLabels)

  return value ? `${label}：${value}` : label
}

export const hasSelectedViralReference = (artifact) =>
  artifact?.runtime_config_snapshot?.creation_mode === 'viral_rewrite' &&
  (artifact?.evidence_snapshot?.items || []).some((item) => item.metadata?.selected_reference === true)
