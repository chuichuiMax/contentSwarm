import { strict as assert } from 'node:assert'
import { describe, it } from 'node:test'

import { creativeStylesForContentType } from '../creativeStyles.js'

const labels = (code, name) => creativeStylesForContentType(code, name).map((item) => item.label)

describe('creativeStylesForContentType', () => {
  it('maps each content type to the styles on the style sheet', () => {
    assert.deepEqual(labels('CT06', '工艺施工展示'), [
      '项目经理掏心窝',
      '本地信任型',
      '专业干货型',
      '极简美学设计型'
    ])
    assert.deepEqual(labels('CT02', '装修报价清单'), [
      '项目经理掏心窝',
      '本地信任型',
      '案例证明型',
      '痛点共鸣型'
    ])
    assert.deepEqual(labels('CT01', '装修案例分享'), [
      '项目经理掏心窝',
      '本地信任型',
      '案例证明型',
      '痛点共鸣型',
      '实景案例拆解型'
    ])
    assert.deepEqual(labels('CT06', '装修知识科普'), ['实景案例拆解型', '极简美学设计型'])
    assert.deepEqual(labels('CT07', '人设自荐'), ['理性设计师'])
  })

  it('uses 价格秀明 for craft and case, 价格透明 for quotes', () => {
    const description = (code, name) =>
      creativeStylesForContentType(code, name).find((item) => item.label === '项目经理掏心窝').description
    assert.equal(description('CT06', '工艺施工展示'), '用真诚的语气表达价格秀明、团队优势')
    assert.equal(description('CT01', '装修案例分享'), '用真诚的语气表达价格秀明、团队优势')
    assert.equal(description('CT02', '装修报价清单'), '用真诚的语气表达价格透明、团队优势')
  })
})
