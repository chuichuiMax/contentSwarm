import { strict as assert } from 'node:assert'
import { describe, it } from 'node:test'

import { creativeStylesForContentType } from '../creativeStyles.js'

const labels = (code, name) => creativeStylesForContentType(code, name).map((item) => item.label)

describe('creativeStylesForContentType', () => {
  it('maps each content type to the styles on the style sheet', () => {
    assert.deepEqual(labels('CT06', '工艺施工展示'), ['信息流', '工艺介绍', '专业干货型'])
    assert.deepEqual(labels('CT02', '装修报价清单'), [
      '项目经理掏心窝',
      '本地信任型',
      '案例证明型',
      '痛点共鸣型'
    ])
    assert.deepEqual(labels('CT01', '装修案例分享'), [
      '信息流类型',
      '重点楼盘案例介绍',
      '设计风格亮点介绍',
      '设计风格亮点介绍（2）',
      '实景案例拆解型'
    ])
    assert.deepEqual(labels('CT06', '装修知识科普'), ['实景案例拆解型', '极简美学设计型'])
    assert.deepEqual(labels('CT07', '人设自荐'), ['理性设计师'])
    assert.deepEqual(labels('CT03', '装修避坑分享'), [
      '项目经理掏心窝',
      '本地信任型',
      '案例证明型',
      '痛点共鸣型'
    ])
    assert.deepEqual(labels('CT04', ''), [
      '项目经理掏心窝',
      '本地信任型',
      '案例证明型',
      '痛点共鸣型'
    ])
  })

  it('uses file prompts for craft and case, and 价格透明 for quotes', () => {
    const craft = creativeStylesForContentType('CT06', '工艺施工展示')
    const caseShare = creativeStylesForContentType('CT01', '案例分享')
    assert.equal(craft[0].description, '强调公司工艺')
    assert.match(craft[0].prompt, /根据我提供的图片/)
    assert.equal(caseShare.find((item) => item.label === '实景案例拆解型').description, '讲故事，结合真实房子案例，代入感强')
    assert.match(caseShare[1].prompt, /根据我提供的图片/)
    const quote = creativeStylesForContentType('CT02', '装修报价清单').find((item) => item.label === '项目经理掏心窝')
    assert.equal(quote.description, '用真诚的语气表达价格透明、团队优势')
    assert.equal(quote.prompt, undefined)
  })
})
