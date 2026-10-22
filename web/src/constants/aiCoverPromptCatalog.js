/** 创作台 AI 封面：智能随机（不对应具体 pN 生图变体）。 */
export const AI_COVER_SMART_RANDOM_VARIANT = 'smart-random'

/** 在 API 返回的 prompts 列表中查找指定变体。 */
export function findAiCoverPromptOption(variant, prompts = []) {
  return prompts.find((item) => item.variant === variant) || null
}

export function isAiCoverSmartRandomVariant(variant) {
  return !variant || variant === AI_COVER_SMART_RANDOM_VARIANT
}
