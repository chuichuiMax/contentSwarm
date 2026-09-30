const QUOTE_CONTENT_TYPES = ['CT02', 'CT03', 'CT04', 'CT05']

export const CREATIVE_STYLE_OPTIONS = [
  {
    value: '老工长掏心窝',
    label: '老工长掏心窝',
    description: '用真诚的语气表达价格透明、个人或团队优势',
    contentTypes: ['CT01', ...QUOTE_CONTENT_TYPES, 'CT06', 'CT07']
  },
  {
    value: '本地信任型',
    label: '本地信任型',
    description: '强调本地施工团队、本地价格、本地服务，建立地域信任',
    contentTypes: ['CT01', ...QUOTE_CONTENT_TYPES, 'CT06', 'CT07']
  },
  {
    value: '工长故事型',
    label: '工长故事型',
    description: '用工长个人经历和透明价格体系打动用户',
    contentTypes: ['CT01', ...QUOTE_CONTENT_TYPES, 'CT06']
  },
  {
    value: '兴奋真相型',
    label: '兴奋真相型',
    description: '用真相、闭眼入等词强调性价比',
    contentTypes: ['CT01', ...QUOTE_CONTENT_TYPES, 'CT06']
  },
  {
    value: '案例证明型',
    label: '案例证明型',
    description: '用真实案例和落地实际费用建立用户信任',
    contentTypes: ['CT01', ...QUOTE_CONTENT_TYPES, 'CT06', 'CT07']
  },
  {
    value: '反差价值型',
    label: '反差价值型',
    description: '有真实对比报价时用别人报 xx 万、我们 xx 万制造反差，否则突出已有优势',
    contentTypes: [...QUOTE_CONTENT_TYPES, 'CT06']
  },
  {
    value: '痛点共鸣型',
    label: '痛点共鸣型',
    description: '先说明装修会遇到的坑和痛点，再给解决方案',
    contentTypes: ['CT01', ...QUOTE_CONTENT_TYPES, 'CT06', 'CT07']
  }
]

export const creativeStylesForContentType = (contentTypeCode) =>
  CREATIVE_STYLE_OPTIONS.filter((style) => style.contentTypes.includes(contentTypeCode))
