import { strict as assert } from 'node:assert'
import { describe, it } from 'node:test'

import {
  DEFAULT_IMAGE_DESIGN_KEYWORDS,
  DEFAULT_IMAGE_DESIGN_PROMPT,
  LOCKED_IMAGE_DESIGN_PROMPT_LINE,
  composeImageDesignPrompt
} from '../imageDesignPromptKeywords.js'

describe('composeImageDesignPrompt', () => {
  it('keeps the six default keywords in the generation prompt', () => {
    const prompt = composeImageDesignPrompt('暖色灯光', DEFAULT_IMAGE_DESIGN_KEYWORDS)
    assert.equal(prompt.startsWith(`${LOCKED_IMAGE_DESIGN_PROMPT_LINE}\n暖色灯光\n关键词：`), true)
    for (const keyword of DEFAULT_IMAGE_DESIGN_KEYWORDS) {
      assert.match(prompt, new RegExp(keyword))
    }
  })

  it('places the default prompt and keywords on separate lines', () => {
    const prompt = composeImageDesignPrompt(DEFAULT_IMAGE_DESIGN_PROMPT, DEFAULT_IMAGE_DESIGN_KEYWORDS)
    assert.equal(
      prompt.startsWith(`${LOCKED_IMAGE_DESIGN_PROMPT_LINE}\n${DEFAULT_IMAGE_DESIGN_PROMPT}\n关键词：`),
      true
    )
    assert.match(prompt, /顶面地面墙面也需要装修/)
    assert.match(prompt, /移除毛坯图中的临时裸灯泡/)
  })

  it('uses only keywords when the free text is empty', () => {
    assert.equal(
      composeImageDesignPrompt('  ', ['空间合理', ' 高级质感 ', '空间合理']),
      `${LOCKED_IMAGE_DESIGN_PROMPT_LINE}\n关键词：空间合理；高级质感`
    )
  })
})
