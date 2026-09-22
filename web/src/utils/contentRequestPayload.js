const GENDER_LABEL = { male: '男', female: '女' }
const BRAND_NAME = '鸿扬家装'
const CONSTRUCTION_BRAND = '鸿扬家装'
const BRAND_POSITIONING = '定制化家装'
const QUOTE_KEYS = ['基础', '木制品', '主材']
const CONTENT_TYPE_TO_CODE = {
  工艺施工展示: 'CT05',
  工艺展示: 'CT05',
  装修报价清单: 'CT02',
  报价清单: 'CT02',
  装修避坑分享: 'CT03',
  避坑分享: 'CT03',
  装修省钱攻略: 'CT04',
  省钱攻略: 'CT04',
  装修案例分享: 'CT01',
  案例分享: 'CT01',
  装修知识科普: 'CT06',
  知识科普: 'CT06',
  人设自荐: 'CT07',
  装修人设自荐: 'CT07'
}
const STAGE_CRAFT_TOPICS = {
  水电阶段: '水电施工与隐蔽验收',
  拆改阶段: '拆改施工',
  泥木阶段: '泥瓦施工',
  油漆阶段: '油漆施工',
  竣工交付: '竣工验收'
}

const text = (value) => String(value ?? '').trim()

const compactList = (...values) => {
  const items = values.flatMap((value) => (Array.isArray(value) ? value : [value])).map(text).filter(Boolean)
  return items.length ? [...new Set(items)] : undefined
}

const compactFacts = (facts) =>
  Object.fromEntries(Object.entries(facts).filter(([, value]) => value !== undefined && value !== ''))

const lockFrameArea = (raw, seed) => {
  const value = text(raw)
  if (!value) return undefined
  const ranged = value.match(/^(\d+)\s*[-~～到至]\s*(\d+)/)
  if (ranged) {
    const low = Number(ranged[1])
    const high = Number(ranged[2])
    const key = `${seed || ''}|${low}-${high}`
    let hash = 0
    for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
    return `${low + (hash % (high - low + 1))}㎡`
  }
  const plus = value.match(/^(\d+)\s*(?:㎡|m²|m2|平米|平)?\s*(?:以上|起)$/i)
  if (plus) {
    const floor = Number(plus[1]) + 1
    return `${floor}㎡`
  }
  const concrete = value.match(/^(\d+)\s*(?:㎡|m²|m2|平米|平)?$/)
  return concrete ? `${concrete[1]}㎡` : value
}

const buildPersona = (employee, user) => ({
  name: employee?.name || user.username || '',
  employeeCode: employee?.employee_code || '',
  loginAccount: employee?.login_account || user.phoneNumber || '',
  gender: GENDER_LABEL[employee?.gender] || employee?.gender || '',
  age: employee?.age == null || employee?.age === '' ? '' : String(employee.age),
  role: employee?.role || user.role || '',
  currentBranch: employee?.current_branch || '',
  currentDepartment: employee?.current_department || user.departmentName || ''
})

const formatServiceYears = (raw) => {
  const value = text(raw)
  if (!value) return ''
  return /年/.test(value) ? value : `${value}年`
}

const buildPersonaFact = (persona, { job, years, region } = {}) => {
  const org = [persona.currentBranch, persona.currentDepartment].filter(Boolean).join('')
  return [
    persona.name,
    persona.age ? `${persona.age}岁` : '',
    job || persona.role,
    years ? `从业${years}` : '',
    region ? `服务${region}` : '',
    org,
    persona.employeeCode ? `工号${persona.employeeCode}` : ''
  ]
    .filter(Boolean)
    .join('，')
}

const buildFacts = (typeName, persona, businessVariables) => {
  const community = text(businessVariables['楼盘信息'])
  const frameArea = text(businessVariables['外框面积'])
  const style = text(businessVariables['设计风格'])
  const audienceTarget = text(businessVariables['目标人群'])
  const household = text(businessVariables['居住人口'])
  const processType = text(businessVariables['工艺类型'])
  const processName = text(businessVariables['工艺名称'])
  const projectStage = text(businessVariables['项目阶段'])
  const stageTopic = STAGE_CRAFT_TOPICS[projectStage] || projectStage
  const budgetText = QUOTE_KEYS.filter((key) => text(businessVariables[key]))
    .map((key) => `${key} ${text(businessVariables[key])}`)
    .join('；')
  const isCraft = typeName === '工艺施工展示' || typeName === '工艺展示'
  const isQuote = typeName === '装修报价清单' || typeName === '报价清单'
  const isCase = typeName === '装修案例分享' || typeName === '案例分享'
  const isKnowledge = typeName === '装修知识科普' || typeName === '知识科普'
  const isPersona = typeName === '人设自荐' || typeName === '装修人设自荐'
  const region = text(businessVariables['所在区域'])
  const job = text(businessVariables['岗位'])
  const years = formatServiceYears(businessVariables['从业年限'])
  const craftParts = compactList(processType, processName, processType ? '' : stageTopic)
  const craftText = (craftParts || []).join('；')
  const delivery = `项目施工${CONSTRUCTION_BRAND}`
  let process
  let advantage
  let pain
  let result
  const lockedArea = lockFrameArea(frameArea, persona.employeeCode || community)
  const priceItems = QUOTE_KEYS.filter((key) => text(businessVariables[key])).map(
    (key) => `${key} ${text(businessVariables[key])}`
  )
  if (isQuote) {
    process = compactList(`${BRAND_POSITIONING}交付`, style)
    advantage = compactList(
      `${BRAND_NAME}品牌与${BRAND_POSITIONING}交付`,
      delivery,
      '透明工艺与售后服务（费用数字仅作参考，不以低价作为卖点）'
    )
    pain = `${community || '业主'}关心装修预算怎么花、怕隐形增项，更需要看清品牌与交付是否靠谱`
    result = [community, lockedArea, style, `施工${CONSTRUCTION_BRAND}`].filter(Boolean).join(' ')
  } else if (isCase) {
    process = compactList(`${BRAND_POSITIONING}交付`)
    advantage = compactList(
      `${BRAND_NAME}品牌与${BRAND_POSITIONING}交付`,
      delivery,
      '真实项目落地与透明服务'
    )
    pain = `${household || audienceTarget || community || '业主'}关心${[community, lockedArea, style].filter(Boolean).join('') || '案例'}怎么从方案落到完工`
    result = [community, lockedArea, style, `施工${CONSTRUCTION_BRAND}`].filter(Boolean).join(' ')
  } else if (isKnowledge) {
    process = craftParts || compactList(`${BRAND_POSITIONING}交付`)
    advantage = compactList(craftText, delivery, `${BRAND_NAME}${BRAND_POSITIONING}与工艺标准说明`)
    pain = `${audienceTarget || '业主'}不清楚${processName || processType || '装修工艺'}该怎么判断、容易被话术带偏`
    result = processName
      ? `看懂${processName}的判断标准与验收要点`
      : `看懂${processType || style || '装修工艺'}该怎么判断`
  } else if (isPersona) {
    process = compactList(job ? `${job}服务` : `${BRAND_POSITIONING}服务`)
    advantage = compactList(
      [job, years].filter(Boolean).join('，') || undefined,
      `${BRAND_NAME}${BRAND_POSITIONING}交付`,
      '服务边界清晰，不夸口、不承诺做不到的结果'
    )
    pain = `${audienceTarget || '业主'}不知道该找谁、怕遇上不靠谱的${job || '服务人员'}`
    result = [region && job ? `${region}${job}` : job || region, years ? `从业${years}` : '', '可对接咨询']
      .filter(Boolean)
      .join('，')
  } else if (isCraft) {
    process = craftParts
    advantage = compactList(craftText, delivery, `${BRAND_NAME}${BRAND_POSITIONING}与透明施工`)
    pain = `业主关心${processType || stageTopic || '施工'}是否规范、细节是否到位、会不会走过场`
    result = [projectStage, processName ? `按工艺规范落实${processName}` : ''].filter(Boolean).join('')
  } else {
    process = compactList(style, frameArea) || compactList(`${BRAND_POSITIONING}交付`)
    advantage = compactList(style, delivery, `${BRAND_NAME}${BRAND_POSITIONING}与透明服务`)
    pain = `${community || '业主'}关注${lockedArea || frameArea || '户型'}装修落地`
    result = [community, lockedArea || frameArea, style, `施工${CONSTRUCTION_BRAND}`].filter(Boolean).join(' ')
  }
  const personaFact = buildPersonaFact(persona, isPersona ? { job, years, region } : {})
  return compactFacts({
    persona_fact: personaFact ? `${personaFact}。` : undefined,
    process,
    advantage,
    result: text(result) || undefined,
    product: isPersona
      ? `${BRAND_NAME}${job || BRAND_POSITIONING}服务`
      : isKnowledge
        ? processName || processType || (community ? `${community}${BRAND_POSITIONING}项目` : `${BRAND_POSITIONING}项目`)
        : community
          ? `${community}${BRAND_POSITIONING}项目`
          : `${BRAND_POSITIONING}项目`,
    location: [region, community].filter(Boolean).join(' · ') || undefined,
    scene: isCraft ? projectStage || style || undefined : style || projectStage || undefined,
    audience: compactList(household, audienceTarget) || compactList('装修业主'),
    quantity: lockedArea || frameArea || undefined,
    price: isQuote || isCase ? compactList(...priceItems) || compactList(budgetText) : undefined,
    quote_type: isQuote || (isCase && priceItems.length) ? 'budget' : undefined,
    pain
  })
}

export const buildContentRequestPayload = ({
  employee,
  user = {},
  contentType,
  businessVariables = {}
} = {}) => {
  const persona = buildPersona(employee, user)
  const typeName = contentType?.name || ''
  const filledVariables = Object.fromEntries(
    Object.entries(businessVariables).filter(([, value]) => {
      if (Array.isArray(value)) return value.some((item) => text(item))
      return text(value)
    })
  )
  return {
    serialNo: persona.employeeCode || user.uid || '',
    contentType: {
      typeName,
      contentTypeId: contentType?.id || '',
      contentTypeCode: CONTENT_TYPE_TO_CODE[typeName] || contentType?.content_type_code || ''
    },
    persona,
    businessVariables: filledVariables,
    facts: buildFacts(typeName, persona, filledVariables)
  }
}

export const formatContentRequestJson = (input) =>
  `${JSON.stringify(buildContentRequestPayload(input), null, 2)}\n`
