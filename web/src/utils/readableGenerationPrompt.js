const MARKERS = [
  { marker: '用户生成提示词：', title: '用户生成提示词' },
  { marker: '业务数据只用来核对事实。', title: '业务数据' },
  { marker: '已命中的知识库用来写文案。', title: '参考知识库' },
  { marker: '用好评笔记知识库里的业主口吻', title: '好评笔记' },
  { marker: '封禁词替换表（成品中不要解释替换）：', title: '封禁词' },
  { marker: '自我查验：', title: '自我查验' }
]

const FIELD_LABELS = {
  serialNo: '编号',
  contentType: '内容类型',
  typeName: '名称',
  contentTypeId: '类型编号',
  contentTypeCode: '类型编码',
  persona: '人员',
  name: '姓名',
  employeeCode: '工号',
  loginAccount: '登录账号',
  gender: '性别',
  age: '年龄',
  role: '岗位',
  currentBranch: '分公司',
  currentDepartment: '部门',
  businessVariables: '业务信息',
  facts: '事实',
  persona_fact: '人员事实',
  process: '过程',
  result: '结果',
  product: '项目',
  location: '地点',
  case_background: '案例背景',
  scene: '场景',
  audience: '受众',
  quantity: '数量',
  price: '费用',
  quote_type: '报价类型'
}

const scalarText = (value) => {
  if (value == null || value === '') return ''
  if (Array.isArray(value)) return value.map(scalarText).filter(Boolean).join('、')
  if (typeof value === 'object') return ''
  return String(value)
}

const formatValue = (value, indent = 0) => {
  const pad = '  '.repeat(indent)
  if (Array.isArray(value)) {
    const line = scalarText(value)
    return line ? `${pad}${line}` : ''
  }
  if (value && typeof value === 'object') {
    return Object.entries(value)
      .map(([key, item]) => {
        const label = FIELD_LABELS[key] || key
        if (item && typeof item === 'object' && !Array.isArray(item)) {
          const nested = formatValue(item, indent + 1)
          return nested ? `${pad}${label}\n${nested}` : ''
        }
        const text = scalarText(item)
        return text ? `${pad}${label}：${text}` : ''
      })
      .filter(Boolean)
      .join('\n')
  }
  const text = scalarText(value)
  return text ? `${pad}${text}` : ''
}

const jsonObject = (text) => {
  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start < 0 || end <= start) return null
  try {
    const parsed = JSON.parse(text.slice(start, end + 1))
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

const readableBusiness = (body) => {
  const note = '只用来核对事实。提示词和内容类型知识库没有要求的金额、小区、人名和经历不要补写。'
  const formatted = formatValue(jsonObject(body))
  return formatted ? `${note}\n\n${formatted}` : body.replace(/^业务数据只用来核对事实。/, '').trim()
}

const readableForbidden = (body) => {
  const parsed = jsonObject(body)
  if (!parsed) return body.replace(/^封禁词替换表（成品中不要解释替换）：/, '').trim()
  const lines = Object.entries(parsed).map(([term, alternatives]) => {
    const next = Array.isArray(alternatives) ? alternatives.filter(Boolean).join('、') : scalarText(alternatives)
    return next ? `${term} → ${next}` : term
  })
  return lines.length ? lines.join('\n') : '这次没有封禁词替换。'
}

const formatSection = (title, body) => {
  if (title === '业务数据') return readableBusiness(body)
  if (title === '封禁词') return readableForbidden(body)
  if (title === '用户生成提示词') return body.replace(/^用户生成提示词：/, '').trim()
  if (title === '自我查验') return body.replace(/^自我查验：/, '').trim()
  return body
}

export const readableGenerationSections = (prompt) => {
  const text = String(prompt || '').trim()
  if (!text) return []
  const found = MARKERS.map((item) => ({ ...item, index: text.indexOf(item.marker) }))
    .filter((item) => item.index >= 0)
    .sort((left, right) => left.index - right.index)
  if (!found.length) return [{ title: '发给模型的内容', text }]
  const sections = []
  const head = text.slice(0, found[0].index).trim()
  if (head) sections.push({ title: '生成要求', text: head })
  found.forEach((item, index) => {
    const end = found[index + 1]?.index ?? text.length
    const body = formatSection(item.title, text.slice(item.index, end).trim())
    if (body) sections.push({ title: item.title, text: body })
  })
  return sections
}
