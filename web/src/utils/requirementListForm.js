import {
  DECORATED_FORM_INTRO_TITLE,
  DECORATED_QUESTIONS,
} from './requirementListFormDecorated.js'
import {
  RENOVATION_FORM_INTRO_TITLE,
  RENOVATION_QUESTIONS,
} from './requirementListFormRenovation.js'

export const FORM_TITLE = '一体化平面规划需求清单'

export const QUESTIONS = [
  { key: 'q1', title: '1、房屋类型：', kind: 'radio', options: ['平层', '复式', '多层/自建房', '别墅', '叠墅'] },
  { key: 'q2', title: '2、房屋面积：', kind: 'area' },
  { key: 'q3', title: '3、房屋定位：', kind: 'radio', other: true, options: ['婚房', '改善房', '养老房', '学位房', '休闲偶住', '其他'] },
  { key: 'q4', title: '4、期望开工时间：', kind: 'radio', other: true, options: ['1个月内', '1-3个月内', '交房就装', '先来考察，时间待定', '其他'] },
  { key: 'q5', title: '5、装修硬装造价估算：(按实用面积算)', kind: 'radio', options: ['15万以内', '15-20万', '20-30万', '30-40万', '40-50万', '50-60万', '60-80万', '80-100万', '100万以上'] },
  { key: 'q6', title: '6、您希望接待您的设计师是：', kind: 'radio', other: true, options: ['男性', '女性', '其他'] },
  { key: 'q7', title: '7、选择你喜欢的装修风格元素方向，最好提供图片资料(设计讨论群)，可多选', kind: 'check', options: ['简约现代', '现代极简', '意式极简', '意式轻奢', '法式奶油', '法式复古', '法式轻奢', '法式田园', '原木奶油', '自然风', '新中式', '宋式美学', '中古风', '原木风', '侘寂风', '多巴胺风', '美式简约', '美式田园', '美式复古'] },
  { key: 'q8', title: '8、关于您家的房间，需要保留：', kind: 'radio', other: true, options: ['全部保留', '一间', '两间', '三间', '其他'] },
  { key: 'q9', title: '9、家庭常住人口的数量、成员', kind: 'check', other: true, options: ['夫(妻)', '单身自住', '父母', '女儿', '儿子', '孙子', '孙女', '保姆', '其他'] },
  { key: 'q10', title: '10、您现在所从事的职业是？', kind: 'radio', other: true, options: ['医生', '教师', '个体', '公务员', '律师', '其他'] },
  { key: 'q11', title: '11、您希望您家的客厅吊顶是：', kind: 'radio', other: true, options: ['满顶', '边顶', '不需要', '其他'] },
  { key: 'q12', title: '12、您和家人在常规功能之外的居住需求多选题', kind: 'check', options: ['西厨', '储物间', '书房', '多功能房', '茶室', '健身房', '乐器房', '专业酒窖', '专业影音室', '洗衣房', '保姆房', '其他专业工作间', '其他特殊功能房'] },
  { key: 'q13', title: '13、您希望您家的厨房是：', kind: 'radio', other: true, options: ['封闭式', '开放式', '其他'] },
  { key: 'q14', title: '14、您和家人是否有养宠物，对此有无特殊要求?', kind: 'radio', other: true, options: ['未养宠', '猫', '狗', '鸟', '水族鱼缸', '其他'] },
  { key: 'q15', title: '15、您和家人有无信仰嗜好，对家中环境有无禁忌?', kind: 'check', other: true, options: ['风水', '宗教信仰', '喝茶', '抽烟', '喝酒', '麻将', '其他'] },
  { key: 'q16', title: '16、对于新房空调的要求？', kind: 'radio', options: ['全屋中央空调（水/氟）', '全屋分体空调', '客餐厅风管机，房间分体或者暂时不装'] },
  {
    key: 'q17',
    title: '17、关于新家，您对设备的需求有哪些？',
    kind: 'groups',
    groups: [
      { label: '清洁方式：', options: ['洗地机', '手拖一体机'] },
      { label: '饮水方式：', options: ['管线机', '嵌饮机'] },
      { label: '智能家居：', options: ['全屋智能', '智能家电'] },
      { label: '网络覆盖：', options: ['无线路由器', 'AP'] },
    ],
  },
  { key: 'q18', title: '18、您认为风水在设计中是否占比很重？', kind: 'radio', options: ['非常重要', '一般（主要一些基本的风水规律即可，在设计和风水冲突时，首先保证设计合理）', '不重要（只考虑空间合理和视觉效果）'] },
]

export const FORM_PACKS = {
  rough: { id: 'rough', introTitle: FORM_TITLE, questions: QUESTIONS },
  decorated: { id: 'decorated', introTitle: DECORATED_FORM_INTRO_TITLE, questions: DECORATED_QUESTIONS },
  renovation: { id: 'renovation', introTitle: RENOVATION_FORM_INTRO_TITLE, questions: RENOVATION_QUESTIONS },
}

export function requirementFormPack(typeId = 'rough') {
  return FORM_PACKS[typeId] || FORM_PACKS.rough
}

export function requirementFormPackByTypeName(typeName = '') {
  const text = String(typeName || '').trim()
  if (text === '精装房') return FORM_PACKS.decorated
  if (text === '旧房焕新') return FORM_PACKS.renovation
  return FORM_PACKS.rough
}

function hasText(value) {
  if (Array.isArray(value)) return value.some((item) => String(item || '').trim())
  return Boolean(String(value || '').trim())
}

export function completionOf({
  answers = {},
  customerName = '',
  phone = '',
  address = '',
  questions = QUESTIONS,
} = {}) {
  const slots = [customerName, phone, address]
  for (const question of questions) {
    if (question.kind === 'area') {
      slots.push(answers.q2_area, answers.q2_layout)
    } else if (question.kind === 'groups') {
      question.groups.forEach((_, index) => slots.push(answers[`${question.key}_${index}`]))
    } else if (question.other && hasText(answers[`${question.key}_other`])) {
      slots.push('filled')
    } else {
      slots.push(answers[question.key])
    }
  }
  const total = slots.length
  const filled = slots.filter((item) => hasText(item)).length
  return { filled, total, percent: total ? Math.round((filled * 100) / total) : 0 }
}

export function blankAnswers(questionList = QUESTIONS) {
  const answers = {}
  for (const question of questionList) {
    if (question.kind === 'area') {
      answers.q2_area = ''
      answers.q2_layout = ''
      continue
    }
    if (question.kind === 'groups') {
      question.groups.forEach((_, index) => {
        answers[`${question.key}_${index}`] = ''
      })
    } else if (question.kind === 'check') {
      answers[question.key] = []
      if (question.other) answers[`${question.key}_other`] = ''
    } else if (question.kind === 'radio' || question.kind === 'text') {
      answers[question.key] = ''
      if (question.other) answers[`${question.key}_other`] = ''
    }
  }
  return answers
}
