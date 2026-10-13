export const LOCKED_IMAGE_DESIGN_PROMPT_LINE = '描述你希望生成的空间风格、材质、色彩和氛围…'

export const DEFAULT_IMAGE_DESIGN_PROMPT =
  '以毛坯图为唯一结构底图，保持原机位、构图、透视和画幅比例；墙、柱、梁、门窗洞口的位置、数量、尺寸、轮廓及空间遮挡关系全部不变，不增删、移动或重塑结构。参考图仅用于风格、配色、材质和灯光。所有饰面贴合原结构，全面替换毛坯材质，消除水泥、砖块、孔洞及施工纹理，覆盖远近景、侧面、转角和洞口内侧，无毛坯遗漏。结构一致优先于装饰造型，主要结构边缘与原图对齐，生成完整、自然、高清的写实装修效果图。\n移除毛坯图中的临时裸灯泡、悬吊电线及临时施工灯具，改用符合装修风格的正式照明。临时施工物品不属于需要保留的空间结构。'

export const DEFAULT_IMAGE_DESIGN_KEYWORDS = [
  '顶面地面墙面也需要装修',
  '原房杂物不要显示',
  '符合实景装修后实景图',
  '专业空间摄影构图',
  '空间合理',
  '高级质感'
]

export function composeImageDesignPrompt(text, keywords) {
  const body = String(text || '').trim()
  const selected = []
  for (const item of keywords || []) {
    const keyword = String(item || '').trim()
    if (keyword && !selected.includes(keyword)) selected.push(keyword)
  }
  const keywordLine = selected.length ? `关键词：${selected.join('；')}` : ''
  return [LOCKED_IMAGE_DESIGN_PROMPT_LINE, body, keywordLine].filter(Boolean).join('\n')
}
