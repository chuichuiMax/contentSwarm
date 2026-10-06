const STYLE_CATALOG = {
  项目经理掏心窝: {
    value: '项目经理掏心窝',
    label: '项目经理掏心窝',
    description: '用真诚的语气表达价格透明、团队优势'
  },
  本地信任型: {
    value: '本地信任型',
    label: '本地信任型',
    description:
      '强调本地服务，0元量房与房屋初步评估，0元户型与空间规划，建立地域信任'
  },
  案例证明型: {
    value: '案例证明型',
    label: '案例证明型',
    description: '用真实案例和落地实际费用建立用户信任'
  },
  专业干货型: {
    value: '专业干货型',
    label: '专业干货型',
    description: '工艺讲解、材料对比、验收标准（比如水电、防水、瓷砖铺贴）'
  },
  极简美学设计型: {
    value: '极简美学设计型',
    label: '极简美学设计型',
    description: '用色彩搭配、软装、灯光设计、收纳设计，温柔舒缓，偏审美科普，不讲硬工艺'
  },
  实景案例拆解型: {
    value: '实景案例拆解型',
    label: '实景案例拆解型',
    description: '讲故事，结合真实房子案例，代入感强'
  },
  靠谱项目经理: {
    value: '靠谱项目经理',
    label: '靠谱项目经理',
    description: '务实真诚，深耕家装行业多年的项目经理，不玩套路，只讲落地'
  },
  理性设计师: {
    value: '理性设计师',
    label: '理性设计师',
    description: '专业设计，不只为好看，兼顾实用，收纳，动线的设计师'
  }
}

const CRAFT_AND_QUOTE_STYLES = [
  '项目经理掏心窝',
  '本地信任型',
  '案例证明型',
  '专业干货型',
  '极简美学设计型'
]

const CASE_SHARE_STYLES = ['项目经理掏心窝', '本地信任型', '案例证明型', '实景案例拆解型']

const KNOWLEDGE_STYLES = ['专业干货型', '实景案例拆解型', '极简美学设计型']

const PERSONA_STYLES = ['靠谱项目经理', '理性设计师']

/** @type {Record<string, string[]>} */
const STYLES_BY_TYPE_NAME = {
  工艺施工展示: CRAFT_AND_QUOTE_STYLES,
  工艺展示: CRAFT_AND_QUOTE_STYLES,
  装修报价清单: CRAFT_AND_QUOTE_STYLES,
  报价清单: CRAFT_AND_QUOTE_STYLES,
  装修案例分享: CASE_SHARE_STYLES,
  案例分享: CASE_SHARE_STYLES,
  装修知识科普: KNOWLEDGE_STYLES,
  知识科普: KNOWLEDGE_STYLES,
  人设自荐: PERSONA_STYLES,
  装修人设自荐: PERSONA_STYLES
}

const stylesFromKeys = (keys = []) =>
  keys.map((key) => STYLE_CATALOG[key]).filter(Boolean)

/** @deprecated 保留导出供旧引用；请改用 creativeStylesForContentType */
export const CREATIVE_STYLE_OPTIONS = Object.values(STYLE_CATALOG)

/**
 * @param {string | undefined} contentTypeCode
 * @param {string | undefined} contentTypeName 管理端内容类型名称（CT06 需靠名称区分工艺展示与知识科普）
 */
export const creativeStylesForContentType = (contentTypeCode, contentTypeName = '') => {
  const name = String(contentTypeName || '').trim()
  if (name && STYLES_BY_TYPE_NAME[name]) {
    return stylesFromKeys(STYLES_BY_TYPE_NAME[name])
  }
  const codeFallbackName = {
    CT01: '装修案例分享',
    CT02: '装修报价清单',
    CT07: '人设自荐'
  }[contentTypeCode]
  if (codeFallbackName) {
    return stylesFromKeys(STYLES_BY_TYPE_NAME[codeFallbackName])
  }
  return []
}
