import { strict as assert } from 'node:assert'
import { describe, it } from 'node:test'

import { creativeStylesForContentType } from '../creativeStyles.js'

describe('creativeStylesForContentType', () => {
  it('maps craft and quote types to five shared styles', () => {
    const labels = creativeStylesForContentType('CT06', '工艺施工展示').map((item) => item.label)
    assert.deepEqual(labels, [
      '项目经理掏心窝',
      '本地信任型',
      '案例证明型',
      '专业干货型',
      '极简美学设计型'
    ])
    assert.deepEqual(
      creativeStylesForContentType('CT02', '装修报价清单').map((item) => item.label),
      labels
    )
  })

  it('distinguishes knowledge from craft under CT06 by type name', () => {
    const knowledge = creativeStylesForContentType('CT06', '装修知识科普').map((item) => item.label)
    assert.deepEqual(knowledge, ['专业干货型', '实景案例拆解型', '极简美学设计型'])
    const craft = creativeStylesForContentType('CT06', '工艺施工展示').map((item) => item.label)
    assert.ok(!craft.includes('实景案例拆解型') || knowledge.length !== craft.length)
  })

  it('maps persona type to two role styles', () => {
    assert.deepEqual(creativeStylesForContentType('CT07', '人设自荐').map((item) => item.label), [
      '靠谱项目经理',
      '理性设计师'
    ])
  })
})
